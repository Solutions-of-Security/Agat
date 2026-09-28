"""Reject misleading counters and restrict sampling to explicitly owned subtrees."""
import errno
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.lib.shadow_resource_sample import ResourceSampler, parse_swap, parse_vm_stat

VM = 'Mach Virtual Memory Statistics: (page size of 16384 bytes)\n' + '\n'.join(
    f'{name}: 7.' for name in ['Pages free', 'Pages active', 'Pages inactive', 'Pages wired down',
        'Pages occupied by compressor', 'Pages stored in compressor', 'Pageins', 'Pageouts',
        'Swapins', 'Swapouts', 'Compressions', 'Decompressions'])


class Counters:
    timebase = {'numer': 125, 'denom': 3}

    def __init__(self, error=None): self.called = []; self.error = error

    def sample(self, pid):
        self.called.append(pid)
        if self.error and pid == 12: raise OSError(self.error, 'fixture')
        return {'pid': pid, 'startTicks': 42, 'userTicks': 10, 'systemTicks': 5,
                'rssBytes': 1024, 'footprintBytes': 2048, 'uuid': 'must-not-be-recorded'}


class ResourceSampleTests(unittest.TestCase):
    def run_command(self, args):
        return {'ps': '1 0\n10 1\n11 10\n12 11\n99 1', '/usr/bin/vm_stat': VM,
                'vm.swapusage': 'total = 10.00M  used = 7.50M  free = 2.50M (encrypted)',
                'kern.memorystatus_vm_pressure_level': '2'}[args[0] if args[0] != '/usr/sbin/sysctl' else args[-1]]

    def test_vm_units_missing_duplicate_and_malformed_counters(self):
        self.assertEqual(parse_vm_stat(VM)['pageSizeBytes'], 16384)
        for changed in (VM.replace('16384', '8192'), VM.replace('Swapouts: 7.', ''), VM + '\nPageins: 7.', VM.replace('7.', '-1.')):
            with self.assertRaises(ValueError): parse_vm_stat(changed)

    def test_swap_units_rounding_and_invalid_totals(self):
        self.assertEqual(parse_swap('total = 10.00M used = 7.50M free = 2.50M')['usedBytes'], 7864320)
        parse_swap('total = 10.00M used = 7.51M free = 2.50M')
        for raw in ('total = 10G used = 7G free = 3G', 'total = 10.00M used = 20.00M free = 0.00M'):
            with self.assertRaises(ValueError): parse_swap(raw)

    def test_only_owned_subtrees_are_sampled_without_executable_identifiers(self):
        counters = Counters(); sampler = ResourceSampler(time.monotonic(), counters=counters, run=self.run_command)
        row = sampler.sample('running', {'shadow': 10, 'alreadyExited': 90})
        self.assertEqual(counters.called, [10, 11, 12]); self.assertEqual(row['groups']['alreadyExited'], [])
        self.assertEqual(row['pressureDispatchLevel'], 2); self.assertNotIn('uuid', str(row))
        self.assertEqual(sampler.report()['ownedPids'], [10, 11, 12]); self.assertEqual(sampler.errors, [])

    def test_exit_race_is_recorded_but_permission_failure_is_not_hidden(self):
        for number in (errno.ESRCH, errno.EPERM):
            sampler = ResourceSampler(time.monotonic(), counters=Counters(number), run=self.run_command)
            row = sampler.sample('running', {'shadow': 10})
            if number == errno.ESRCH:
                self.assertEqual(row['exitedDuringSample'], [12]); self.assertEqual(sampler.errors, [])
            else: self.assertEqual(row['error'], 'PermissionError'); self.assertTrue(sampler.errors)


if __name__ == '__main__': unittest.main()
