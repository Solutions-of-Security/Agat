#!/usr/bin/env python3
"""Replay native idle counters, bounded pool reuse and cleanup without macOS APIs."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('idle_replay_support', ROOT / 'scripts/verify-embedding-parent-guard.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
SOURCES = replay.SOURCES | {'scripts/profile-embedding-idle-budget.py', 'scripts/verify-embedding-idle-budget.py'}


def usage(row):
    assert type(row['pid']) is int and row['pid'] > 0 and row['exitTicks'] == 0
    assert re.fullmatch('[0-9a-f]{32}', row['uuid']) and int(row['uuid'], 16) > 0
    assert all(type(row[key]) is int and row[key] > 0 for key in ('atNs', 'startTicks', 'rssBytes', 'footprintBytes'))
    assert all(type(row[key]) is int and row[key] >= 0 for key in ('userTicks', 'systemTicks', 'packageWakeups', 'interruptWakeups'))


def interval(before, after, timebase):
    usage(before)
    usage(after)
    assert all(before[key] == after[key] for key in ('pid', 'uuid', 'startTicks'))
    assert all(after[key] >= before[key] for key in ('userTicks', 'systemTicks', 'packageWakeups', 'interruptWakeups'))
    assert after['atNs'] > before['atNs']
    ticks = after['userTicks'] + after['systemTicks'] - before['userTicks'] - before['systemTicks']
    return ticks * timebase['numer'] / timebase['denom'], after['atNs'] - before['atNs']


def verify(directory):
    if not __debug__:
        raise RuntimeError('Assertions must be enabled')
    directory = directory.resolve()
    assert directory.is_relative_to(ROOT / 'docs')
    plan = json.loads((directory / 'plan.json').read_text())
    result = json.loads((directory / 'result.json').read_text())
    assert result['status'] == 'observed' and result['failure'] is None
    assert result['planSha256'] == replay.sha((directory / 'plan.json').read_bytes())
    assert plan['schema'] == 'agat.embedding.idle-budget.v1' and plan['model'] == 'synthetic-no-inference'
    assert plan['platform'] == 'Darwin' and plan['machine'] == 'arm64' and plan['memoryBytes'] >= 24 * 1024**3
    assert plan['counter'] == 'proc_pid_rusage/RUSAGE_INFO_V0' and plan['abiBytes'] == 96
    assert plan['budgetSeconds'] == 180 and 120 <= result['elapsedSeconds'] <= 195
    timebase = plan['timebase']
    assert set(timebase) == {'numer', 'denom'} and all(type(value) is int and value > 0 for value in timebase.values())
    assert set(plan['sourceSha256']) == SOURCES
    assert plan['sourceSha256'] == replay.source_hashes(plan['implementationCommit'], SOURCES)
    assert plan['baselineCommit'] == replay.BASELINE
    baseline = replay.source_hashes(replay.BASELINE, replay.TRANSPORTS | replay.COMMON)
    assert plan['baselineSha256'] == {name: baseline[name] for name in replay.TRANSPORTS}
    assert all(plan['sourceSha256'][name] == baseline[name] for name in replay.COMMON)
    assert all(plan['sourceSha256'][name] != baseline[name] for name in replay.TRANSPORTS)
    expected = [{'id': f'c{c}-{index}', 'capacity': c, 'guard': enabled, 'idleSeconds': 10}
                for c in (1, 4, 32) for index, enabled in enumerate((False, True, True, False))]
    assert plan['phases'] == expected and all(type(item['guard']) is bool for item in plan['phases'])
    assert [row['id'] for row in result['phases']] == [item['id'] for item in expected]
    calibration = result['calibration']
    cpu, wall = interval(calibration['before'], calibration['after'], timebase)
    independent = calibration['processTimeAfterNs'] - calibration['processTimeBeforeNs']
    assert 200_000_000 <= independent < 400_000_000 and independent <= wall + 1_000_000
    assert abs(cpu - independent) <= 1_000_000 + .02 * independent
    phases, total = [], 0
    for item, phase in zip(expected, result['phases'], strict=True):
        c = item['capacity']
        children = {child['pid']: child for child in phase['children']}
        assert len(phase['children']) == len(children) == c
        assert type(phase['parentPid']) is int and phase['parentPid'] == calibration['before']['pid']
        assert {child['actor'] for child in children.values()} == set(range(c))
        assert phase['remainingThreads'] == phase['serverErrors'] == []
        assert type(phase['fdBefore']) is int and phase['fdBefore'] > 0
        assert phase['fdBefore'] == phase['fdAfterClose'] and phase['fdIdle'] == phase['fdBefore'] + 1 + c * 2
        arguments = ['--serve'] + (['--parent-pid', str(phase['parentPid'])] if item['guard'] else [])
        source = plan['sourceSha256' if item['guard'] else 'baselineSha256']['workers/embedding_transport.py']
        for pid, child in children.items():
            assert type(pid) is int and pid > 0 and pid != phase['parentPid']
            assert child['arguments'] == arguments and child['scriptSha256'] == source
            assert child['returncode'] == 0 and child['stdinClosed'] is True and child['stdoutClosed'] is True
        before, after = [{row['pid']: row for row in phase[key]} for key in ('idleBefore', 'idleAfter')]
        assert len(phase['idleBefore']) == len(phase['idleAfter']) == c and before.keys() == after.keys() == children.keys()
        costs = [interval(before[pid], after[pid], timebase) for pid in children]
        assert all(10e9 <= wall <= 11e9 for _, wall in costs)
        assert max(row['atNs'] for row in before.values()) < min(row['atNs'] for row in after.values())
        assert len(phase['serverRows']) == c * 2
        for round_id, key in enumerate(('beforeCalls', 'afterCalls')):
            calls = phase[key]
            assert len(calls) == c and [row['actor'] for row in calls] == list(range(c))
            servers = {row['actor']: row for row in phase['serverRows'] if row['round'] == round_id}
            assert len(servers) == c and set(servers) == set(range(c))
            # Every real HTTP request reaches the barrier before any reply finishes.
            assert max(row['enteredNs'] for row in servers.values()) < min(row['finishedNs'] for row in servers.values())
            for call in calls:
                assert call['round'] == round_id and call['vectors'] == [[round_id + 1, call['actor'] + 1]]
                assert 0 < call['startedNs'] < servers[call['actor']]['enteredNs'] < call['finishedNs']
                assert servers[call['actor']]['enteredNs'] < servers[call['actor']]['finishedNs']
                if round_id == 0:
                    assert call['finishedNs'] < min(row['atNs'] for row in before.values())
                else:
                    assert max(row['atNs'] for row in after.values()) < call['startedNs'] < call['finishedNs'] < phase['closeStartedNs']
        assert phase['closeStartedNs'] < phase['closeFinishedNs']
        phases.append({'id': item['id'], 'capacity': c, 'guard': item['guard'],
                       'idleCpuMs': round(sum(cpu for cpu, _ in costs) / 1e6, 6),
                       'oneCorePercent': round(sum(cpu / wall for cpu, wall in costs) * 100, 6),
                       'idleWindowMs': [round(min(wall for _, wall in costs) / 1e6, 3), round(max(wall for _, wall in costs) / 1e6, 3)],
                       'helperRssBeforeBytes': sum(row['rssBytes'] for row in before.values()),
                       'helperRssAfterBytes': sum(row['rssBytes'] for row in after.values()),
                       'helperFootprintAfterBytes': sum(row['footprintBytes'] for row in after.values()),
                       'closeMs': round((phase['closeFinishedNs'] - phase['closeStartedNs']) / 1e6, 3)})
        total += c
    assert total == 148
    return {'status': 'verified', 'helpersReaped': total, 'httpRequests': total * 2, 'phases': phases,
            'calibrationNativeCpuMs': round(interval(calibration['before'], calibration['after'], timebase)[0] / 1e6, 6),
            'calibrationPythonCpuMs': round(independent / 1e6, 6),
            'planSha256': result['planSha256'], 'resultSha256': replay.sha((directory / 'result.json').read_bytes())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    print(json.dumps(verify(parser.parse_args().directory), indent=2, allow_nan=False))
