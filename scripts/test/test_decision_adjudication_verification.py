from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib.decision_adjudication_verification import verify_session
from scripts.lib.decision_review_adjudication import encoded
import scripts.test.test_decision_adjudication_session as fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('adjudication_verification_cli', ROOT / 'scripts/verify-decision-adjudication-session.py')
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
sha = lambda raw: hashlib.sha256(raw).hexdigest()


class AdjudicationVerificationTest(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.AdjudicationSessionTest(); self.helper.setUp(); self.addCleanup(self.helper.doCleanups)
        self.directory = self.helper.private

    def artifacts(self, answers='1\nSynthetic A\ny\n2\nSynthetic B\ny\n:submit\ny\n', name='session', review=None):
        code, directory, receipt, saved, _ = self.helper.invoke(answers, name=name, review=review)
        self.paths = [directory / 'session.json', review or self.helper.paths['adjudication.review.blank.json'],
                      directory / 'review.json', self.helper.paths['packet.json'],
                      self.helper.paths['adjudication.review.blank.json'], self.helper.paths['case-notes.json']]
        return code, receipt, saved

    def binding(self):
        return tuple(value for path in self.paths for value in (path, sha(path.read_bytes())))

    def verify(self):
        return verify_session(*self.binding())

    def mutate(self, index, mutation, *, reseal=False):
        from decision_runtime.contracts import parse_json
        value = parse_json(self.paths[index].read_bytes()); mutation(value)
        if reseal: value = sealed({key: item for key, item in value.items() if key != 'sha256'})
        self.paths[index].write_bytes(encoded(value))

    def test_submitted_subset_binds_every_input_and_preserves_whole_pool(self):
        code, _, saved = self.artifacts(); self.assertEqual(code, 0)
        report = self.verify(); self.assertTrue(report['completeAdjudicationArtifact'])
        self.assertEqual(report['sessionStatus'], 'completed'); self.assertEqual(report['disputedCaseIds'], ['fixture-0', 'fixture-2'])
        for key, path in zip(('sessionFileSha256', 'inputReviewFileSha256', 'outputReviewFileSha256',
                              'packetFileSha256', 'blankReviewFileSha256', 'caseNotesFileSha256'), self.paths):
            self.assertEqual(report[key], sha(path.read_bytes()))
        self.assertEqual(saved['pool'], self.helper.blank['pool'])
        for key in ('reviewerIdentityVerified', 'humanExecutionVerified', 'independentReviewVerified',
                    'expertQualificationsVerified', 'routingEnabled', 'handoffSourcesRevalidated', 'reviewSourcesRevalidated'):
            self.assertIs(report[key], False)
        self.assertEqual(report['referenceLabelsCreated'], 0); self.assertEqual(report['modelCallsDuringVerification'], 0)

    def test_full_draft_quit_is_not_submission_and_resume_can_submit(self):
        code, _, saved = self.artifacts('1\nSynthetic A\ny\n2\nSynthetic B\ny\n:quit\n')
        self.assertEqual(code, 2); report = self.verify()
        self.assertTrue(report['allAnswersPresent']); self.assertFalse(report['completeAdjudicationArtifact'])
        self.assertIsNone(saved['reviewedAt']); initial = self.paths[2]
        self.artifacts(':submit\ny\n', name='resumed', review=initial)
        report = self.verify(); self.assertTrue(report['completeAdjudicationArtifact'])
        self.assertEqual(report['counts']['existingAnswers'], 2); self.assertEqual(report['counts']['newAnswers'], 0)

    def test_failed_checkpoint_preserves_failure_and_reconstructed_counts(self):
        session_cli = fixtures.cli
        checkpoint = session_cli.checkpoint; calls = []
        def save(directory, review):
            calls.append(1)
            if len(calls) > 1: raise OSError('Synthetic disk failure')
            return checkpoint(directory, review)
        with patch.object(session_cli, 'checkpoint', side_effect=save):
            code, _, _ = self.artifacts('1\nSynthetic A\ny\n')
        self.assertEqual(code, 1); report = self.verify()
        self.assertEqual(report['sessionStatus'], 'failed'); self.assertFalse(report['completeAdjudicationArtifact'])
        self.assertFalse(report['reportedCountsReconciled']); self.assertEqual(report['counts']['remainingAnswers'], 2)

    def test_each_external_pin_is_required_even_when_other_files_are_valid(self):
        self.artifacts(); bindings = self.binding()
        for index in range(6):
            changed = list(bindings); changed[2 * index + 1] = '0' * 64
            with self.subTest(index=index), self.assertRaises(ValueError): verify_session(*changed)

    def test_rehashed_session_cannot_forge_counts_submission_authority_or_handoff(self):
        self.artifacts(); original = self.paths[0].read_bytes()
        changes = [('newAnswers', 0), ('remainingAnswers', True), ('submissionConfirmed', False),
                   ('packetFileSha256', '0' * 64), ('blankReviewFileSha256', '0' * 64),
                   ('caseNotesFileSha256', '0' * 64), ('inputReviewFileSha256', '0' * 64),
                   ('outputReviewFileSha256', '0' * 64), ('handoffSourcesRevalidated', True),
                   ('distinctDeclaredReviewerId', False), ('humanExecutionVerified', True),
                   ('sourceCommit', 'main'), ('finishedAt', '2020-01-01T00:00:00Z'),
                   ('disputedCaseIds', ['fixture-2', 'fixture-0'])]
        for key, value in changes:
            self.paths[0].write_bytes(original); self.mutate(0, lambda item: item.update({key: value}), reseal=True)
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify()

    def test_rehashed_handoff_or_notes_cannot_change_source_or_grant_authority(self):
        self.artifacts(); original = [path.read_bytes() for path in self.paths]
        for key, value in [('sourceCaseCount', True), ('resolvedCases', 1), ('humanExecutionVerified', True),
                           ('disputedCaseIds', ['fixture-2', 'fixture-0'])]:
            for path, raw in zip(self.paths, original): path.write_bytes(raw)
            self.mutate(3, lambda item: item.update({key: value}), reseal=True)
            packet_sha = sha(self.paths[3].read_bytes())
            self.mutate(0, lambda item: item.update(packetFileSha256=packet_sha), reseal=True)
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify()
        for path, raw in zip(self.paths, original): path.write_bytes(raw)
        self.mutate(5, lambda item: item['cases'][0]['case']['request'].update(state='Changed source text'))
        # Even all raw bindings changed together cannot substitute another case source.
        notes_sha = sha(self.paths[5].read_bytes()); size = self.paths[5].stat().st_size
        self.mutate(3, lambda item: item['outputFiles']['case-notes.json'].update(sha256=notes_sha, bytes=size), reseal=True)
        self.mutate(0, lambda item: item.update(packetFileSha256=sha(self.paths[3].read_bytes()), caseNotesFileSha256=notes_sha), reseal=True)
        with self.assertRaises(ValueError): self.verify()

    def test_rehashed_saved_review_cannot_change_owner_pool_seed_or_subset(self):
        self.artifacts(); original = [path.read_bytes() for path in self.paths]
        changes = [lambda item: item.update(reviewerId='fixture-first'), lambda item: item.update(splitSeed='different'),
                   lambda item: item['pool']['cases'][1]['request'].update(state='Changed undisputed source'),
                   lambda item: item['labels'].reverse(), lambda item: item['labels'].append(deepcopy(item['labels'][0]))]
        for mutation in changes:
            for path, raw in zip(self.paths, original): path.write_bytes(raw)
            self.mutate(2, mutation); self.mutate(0, lambda item: item.update(outputReviewFileSha256=sha(self.paths[2].read_bytes())), reseal=True)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.verify()

    def test_duplicate_members_are_refused_in_each_artifact(self):
        self.artifacts(); original = [path.read_bytes() for path in self.paths]
        for index, path in enumerate(self.paths):
            for target, raw in zip(self.paths, original): target.write_bytes(raw)
            from decision_runtime.contracts import parse_json
            key = next(iter(parse_json(path.read_bytes())))
            path.write_bytes(('{"' + key + '":null,').encode() + path.read_bytes()[1:])
            with self.subTest(index=index), self.assertRaises(ValueError): self.verify()

    def test_symlinks_and_size_bounds_are_refused_for_each_input(self):
        self.artifacts(); bindings = self.binding()
        for index, path in enumerate(self.paths):
            link = self.directory / ('link-' + str(index)); link.symlink_to(path)
            changed = list(bindings); changed[2 * index] = link
            with self.subTest(index=index), self.assertRaises(ValueError): verify_session(*changed)
        with patch('scripts.lib.decision_adjudication_verification.MAX_SESSION_BYTES', 10), self.assertRaises(ValueError): self.verify()
        with patch('scripts.lib.decision_adjudication_verification.MAX_REVIEW_BYTES', 10), self.assertRaises(ValueError): self.verify()

    def test_cli_validates_before_private_output_and_never_overwrites(self):
        self.artifacts(); output = self.directory / 'verified'; arguments = []
        for name, path in zip(cli.INPUTS, self.paths):
            arguments.extend(['--' + name, str(path), '--' + name + '-file-sha256', sha(path.read_bytes())])
        arguments.extend(['--output-dir', str(output)])
        with patch.object(cli, 'ROOT', self.helper.root):
            self.assertEqual(cli.main(arguments), 0); raw = (output / 'verification.json').read_bytes()
            self.assertEqual(cli.main(arguments), 1); self.assertEqual((output / 'verification.json').read_bytes(), raw)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((output / 'verification.json').stat().st_mode & 0o777, 0o600)
        arguments[-1] = str(self.directory / 'refused'); arguments[3] = '0' * 64
        with patch.object(cli, 'ROOT', self.helper.root): self.assertEqual(cli.main(arguments), 1)
        self.assertFalse((self.directory / 'refused').exists())


if __name__ == '__main__': unittest.main()
