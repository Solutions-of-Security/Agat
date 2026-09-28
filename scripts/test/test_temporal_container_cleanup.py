"""Owned-container cleanup must continue after Docker observation/stop errors."""
import json
import os
import subprocess
import time
import unittest
from unittest.mock import patch

from test_temporal_cleanup import launcher


class DockerFixture:
    def __init__(self, names, listing_errors=(), fail_first_remove=False, skip_remove=False):
        self.names = set(names)
        self.listing_errors = set(listing_errors)
        self.fail_first_remove, self.skip_remove = fail_first_remove, skip_remove
        self.listings, self.removals = 0, []

    def command(self, args):
        if args[:2] == ['docker', 'ps']:
            self.listings += 1
            if self.listings in self.listing_errors:
                raise OSError('private Docker diagnostic')
            return '\n'.join(sorted(self.names))
        if args[:3] == ['docker', 'rm', '--force'] and len(args) == 4:
            name = args[3]
            self.removals.append(name)
            if self.fail_first_remove and len(self.removals) == 1:
                raise OSError('private Docker diagnostic')
            if not self.skip_remove:
                self.names.discard(name)
            return name
        raise AssertionError('Unexpected Docker operation: ' + repr(args))


class ContainerCleanupTests(unittest.TestCase):
    names = {'agat-temporal-rag-11-100', 'agat-temporal-postgres-rag-12-100'}
    foreign = 'agat-temporal-rag-99-100'

    def run_cleanup(self, docker, known):
        errors = []
        with patch.object(launcher, 'command', side_effect=docker.command):
            launcher.cleanup_owned_containers({11, 12}, known, errors)
        self.assertNotIn('private Docker diagnostic', repr(errors))
        return errors

    def test_listing_failure_still_removes_known_owned_containers(self):
        docker = DockerFixture(self.names | {self.foreign}, listing_errors={1})
        errors = self.run_cleanup(docker, set(self.names))
        self.assertEqual(set(docker.removals), self.names)
        self.assertEqual(docker.names, {self.foreign})
        self.assertEqual(docker.listings, 2)
        self.assertEqual(errors, ['containerInventory:OSError'])

    def test_one_remove_error_does_not_skip_other_containers_or_final_inventory(self):
        docker = DockerFixture(self.names | {self.foreign}, fail_first_remove=True)
        known = set()
        errors = self.run_cleanup(docker, known)
        self.assertEqual(len(docker.removals), len(self.names))
        self.assertEqual(docker.names, {docker.removals[0], self.foreign})
        self.assertEqual(docker.listings, 2)
        self.assertEqual(known, self.names)
        self.assertEqual(errors, ['containerRemove:OSError', 'containerRemove:ContainersStillPresent'])

    def test_failed_discovery_without_known_containers_still_verifies_final_state(self):
        docker = DockerFixture({self.foreign}, listing_errors={1, 2})
        errors = self.run_cleanup(docker, set())
        self.assertEqual(docker.listings, 2)
        self.assertEqual(docker.removals, [])
        self.assertEqual(errors, ['containerInventory:OSError', 'containerVerification:OSError'])

    def test_successful_inventory_discovers_new_containers_and_skips_already_removed_ones(self):
        already_removed = 'agat-temporal-rag-11-99'
        known = {already_removed}
        docker = DockerFixture(self.names | {self.foreign})
        self.assertEqual(self.run_cleanup(docker, known), [])
        self.assertEqual(docker.removals, sorted(self.names))
        self.assertEqual(known, self.names | {already_removed})
        self.assertEqual(docker.names, {self.foreign})

    def test_failed_final_inventory_does_not_claim_successful_cleanup(self):
        docker = DockerFixture(self.names, listing_errors={2})
        self.assertEqual(self.run_cleanup(docker, set()), ['containerVerification:OSError'])
        self.assertEqual(docker.names, set())
        self.assertEqual(docker.listings, 2)

    def test_successful_remove_command_that_leaves_containers_is_not_success(self):
        docker = DockerFixture(self.names, skip_remove=True)
        errors = self.run_cleanup(docker, set())
        self.assertEqual(errors, ['containerRemove:ContainersStillPresent'])
        self.assertEqual(docker.removals, sorted(self.names))

    def test_fallback_never_deletes_foreign_or_partially_matching_names(self):
        unrelated = {self.foreign, 'prefix-agat-temporal-rag-11-100', 'agat-temporal-rag-11-100-suffix',
                     'agat-temporal-rag-111-100', 'agat-temporal-rag-11-not-a-timestamp', 'user-database'}
        docker = DockerFixture(self.names | unrelated, listing_errors={1})
        errors = self.run_cleanup(docker, self.names | unrelated)
        self.assertEqual(docker.names, unrelated)
        self.assertEqual(set(docker.removals), self.names)
        self.assertEqual(errors, ['containerInventory:OSError'])

    def test_docker_name_filter_still_requires_an_exact_name_and_observed_owner(self):
        unrelated = {self.foreign, 'prefix-agat-temporal-rag-11-100', 'agat-temporal-rag-11-100-suffix',
                     'agat-temporal-rag-111-100', 'agat-temporal-rag-11-not-a-timestamp', 'user-database'}
        docker = DockerFixture(self.names | unrelated)
        self.assertEqual(self.run_cleanup(docker, set()), [])
        self.assertEqual(docker.names, unrelated)
        self.assertEqual(set(docker.removals), self.names)


@unittest.skipUnless(os.environ.get('AGAT_TEST_DOCKER_CLEANUP') == '1', 'Requires explicit owned Docker fixtures')
class LiveContainerCleanupTests(unittest.TestCase):
    def test_observation_and_removal_failures_with_real_containers(self):
        # Resolve the local image once and run by ID; this test never pulls.
        image = os.environ.get('AGAT_TEST_DOCKER_CLEANUP_IMAGE', 'busybox:1.36')
        image_id = json.loads(launcher.command(['docker', 'image', 'inspect', image]))[0]['Id']
        real_command = launcher.command
        for fault in ['discovery', 'removal', 'verification']:
            with self.subTest(fault=fault):
                stamp, owner = time.time_ns(), os.getpid()
                names = {f'agat-temporal-rag-{owner}-{stamp}', f'agat-temporal-postgres-rag-{owner}-{stamp}'}
                sentinel = f'agat-temporal-rag-{owner + 1000000000}-{stamp}'
                created, removals, listings = [], [], 0
                def command(args):
                    nonlocal listings
                    if args[:2] == ['docker', 'ps']:
                        listings += 1
                        if (fault == 'discovery' and listings == 1) or (fault == 'verification' and listings == 2):
                            raise OSError('Injected private Docker listing failure')
                    if args[:3] == ['docker', 'rm', '--force']:
                        removals.append(args[3])
                        if fault == 'removal' and len(removals) == 1:
                            raise OSError('Injected private Docker removal failure')
                    return real_command(args)
                try:
                    for name in sorted(names | {sentinel}):
                        created.append(real_command(['docker', 'run', '--pull', 'never', '--network', 'none',
                            '--read-only', '--detach', '--name', name, image_id, 'sleep', '120']))
                    errors = []
                    with patch.object(launcher, 'command', side_effect=command):
                        launcher.cleanup_owned_containers({owner}, set(names), errors)
                    self.assertEqual(set(removals), names)
                    self.assertEqual(listings, 2)
                    self.assertTrue(json.loads(real_command(['docker', 'inspect', sentinel]))[0]['State']['Running'])
                    remaining = launcher.own_containers({owner})
                    if fault == 'removal':
                        self.assertEqual(remaining, {removals[0]})
                        self.assertEqual(errors, ['containerRemove:OSError', 'containerRemove:ContainersStillPresent'])
                        launcher.cleanup_owned_containers({owner}, remaining, errors)
                        self.assertEqual(launcher.own_containers({owner}), set())
                        self.assertEqual(errors, ['containerRemove:OSError', 'containerRemove:ContainersStillPresent'])
                    else:
                        self.assertEqual(remaining, set())
                        phase = 'Inventory' if fault == 'discovery' else 'Verification'
                        self.assertEqual(errors, ['container' + phase + ':OSError'])
                finally:
                    # Only immutable IDs created by this test are removed here;
                    # every fixture is attempted even if one removal fails.
                    cleanup_errors = []
                    for container_id in created:
                        try:
                            subprocess.run(['docker', 'rm', '--force', container_id],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
                        except (OSError, subprocess.SubprocessError) as error:
                            cleanup_errors.append(type(error).__name__)
                    alive = set(real_command(['docker', 'ps', '--all', '--no-trunc', '--format', '{{.ID}}']).splitlines())
                    self.assertFalse(set(created) & alive, 'Owned Docker fixtures survived teardown')
                    self.assertEqual(cleanup_errors, [])


if __name__ == '__main__':
    unittest.main()
