"""Read-only, allowlisted macOS resources for owned real-model test processes."""
from decimal import Decimal
import importlib.util
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATHS = ['scripts/lib/shadow_resource_sample.py', 'scripts/test/test_shadow_resource_sample.py',
    'scripts/profile-embedding-idle-budget.py', 'scripts/profile-embedding-parent-guard.py',
    'scripts/profile-embedding-model-transport.py', 'scripts/profile-embedding-transport.py']
PLAN = {'intervalSeconds': 1, 'processCounters': 'proc_pid_rusage-v0', 'processScope': 'owned_subtrees',
        'systemCounters': ['vm_stat', 'vm.swapusage', 'kern.memorystatus_vm_pressure_level'],
        'sampledNotPeaks': True}


def parse_vm_stat(raw):
    lines = raw.strip().splitlines()
    header = re.fullmatch(r'Mach Virtual Memory Statistics: \(page size of ([0-9]+) bytes\)', lines[0])
    if not header or int(header[1]) not in (4096, 16384):
        raise ValueError('Unsupported vm_stat page size')
    counters = {}
    for line in lines[1:]:
        match = re.fullmatch(r'([^:]+):\s+([0-9]+)\.', line)
        if not match or match[1].strip('"') in counters:
            raise ValueError('Invalid vm_stat counter')
        counters[match[1].strip('"')] = int(match[2])
    names = ['Pages free', 'Pages active', 'Pages inactive', 'Pages wired down', 'Pages occupied by compressor',
             'Pages stored in compressor', 'Pageins', 'Pageouts', 'Swapins', 'Swapouts', 'Compressions', 'Decompressions']
    if any(name not in counters for name in names):
        raise ValueError('Missing vm_stat counter')
    return {'pageSizeBytes': int(header[1]), 'pages': {name: counters[name] for name in names}}


def parse_swap(raw):
    match = re.fullmatch(r'total = ([0-9]+\.[0-9]+)M\s+used = ([0-9]+\.[0-9]+)M\s+free = ([0-9]+\.[0-9]+)M(?:\s+\(encrypted\))?', raw.strip())
    if not match:
        raise ValueError('Invalid swap counter')
    values = [int(Decimal(value) * 1024**2) for value in match.groups()]
    # sysctl rounds each displayed field to .01 MiB independently.
    if abs(values[0] - values[1] - values[2]) > 2 * 10486 or max(values[1:]) > values[0]:
        raise ValueError('Inconsistent swap counters')
    return dict(zip(('totalBytes', 'usedBytes', 'freeBytes'), values))


def command(args):
    return subprocess.check_output(args, text=True, timeout=5).strip()


def native_counters():
    spec = importlib.util.spec_from_file_location('shadow_native_counters', ROOT / 'scripts/profile-embedding-idle-budget.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.DarwinCounters()


class ResourceSampler:
    def __init__(self, started, *, counters=None, run=command):
        self.started, self.run = started, run
        self.counters = counters or native_counters()
        self.samples, self.errors, self.owned = [], [], set()
        self.timebase = self.counters.timebase

    def clock(self):
        return round((time.monotonic() - self.started) * 1000, 3)

    def sample(self, phase, roots):
        row = {'phase': phase, 'startedMs': self.clock(), 'groups': {}, 'processes': []}
        try:
            table = {int(pid): int(parent) for pid, parent in
                     (line.split() for line in self.run(['ps', '-axo', 'pid=,ppid=']).splitlines())}
            for name, pid in roots.items():
                if type(pid) is not int or pid <= 0:
                    raise ValueError('Invalid owned root PID')
                found = {pid} if pid in table else set()
                while extra := {child for child, parent in table.items() if parent in found} - found:
                    found.update(extra)
                row['groups'][name] = sorted(found)
            pids = set().union(*map(set, row['groups'].values()))
            self.owned.update(pids)
            row['exitedDuringSample'] = []
            for pid in sorted(pids):
                try:
                    usage = self.counters.sample(pid)
                except OSError as error:
                    if error.errno != 3:  # ESRCH: short-lived owned helpers may exit between reads.
                        raise
                    row['exitedDuringSample'].append(pid); continue
                row['processes'].append({key: usage[key] for key in
                    ('pid', 'startTicks', 'userTicks', 'systemTicks', 'rssBytes', 'footprintBytes')})
            row['vm'] = parse_vm_stat(self.run(['/usr/bin/vm_stat']))
            row['swap'] = parse_swap(self.run(['/usr/sbin/sysctl', '-n', 'vm.swapusage']))
            row['pressureDispatchLevel'] = int(self.run(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level']))
            if row['pressureDispatchLevel'] not in (1, 2, 4):
                raise ValueError('Unknown memory-pressure dispatch level')
        except Exception as error:
            row['error'] = type(error).__name__
            self.errors.append({'phase': phase, 'type': type(error).__name__})
        row['finishedMs'] = self.clock(); self.samples.append(row)
        return row

    def report(self):
        return {'schema': 'agat.shadow.resources.v1', 'timebase': self.timebase,
                'samples': self.samples, 'errors': self.errors, 'ownedPids': sorted(self.owned)}
