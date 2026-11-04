"""Whole-process resource snapshots around the unchanged offline GLiClass baseline."""
from datetime import datetime, timezone
import hashlib
import io
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import tarfile
import time

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import canonical_json, fields, fingerprint
from scripts.lib.decision_gliclass_baseline import (AUTHORITY, SOURCE_PATHS as BASELINE_SOURCES,
    GLiClassBackend, development_dataset, measure as measure_baseline, verify_report)
from scripts.lib.decision_shadow_pilot import require

SCHEMA = 'agat.decision.gliclass-resource-profile.v1'
PHASES = ('before-backend', 'after-backend', 'after-scoring')
SOURCE_PATHS = (*BASELINE_SOURCES, 'scripts/profile-gliclass-development.py',
                'scripts/test/test_decision_gliclass_resources.py')
SCOPE = 'RUSAGE_SELF for the whole isolated Python process, including imports, tokenizer, model and allocator; child processes excluded'
COUNTERS = ('minorFaults', 'majorFaults', 'voluntaryContextSwitches', 'involuntaryContextSwitches')


def frozen_sources(root):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=10).strip()
    raw = subprocess.check_output(['git', 'archive', commit, '--', *SOURCE_PATHS], cwd=root, timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require(all(path in sources or any(name.startswith(path + '/') for name in sources) for path in SOURCE_PATHS)
            and all((root / name).read_bytes() == content for name, content in sources.items()),
            'Commit the complete resource-profile implementation before measuring')
    require(not subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '--', *SOURCE_PATHS],
                                       cwd=root, timeout=10), 'Untracked resource-profile source')
    return {'sourceCommit': commit, 'sourceFiles': {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}}


def rss_multiplier(platform):
    require(platform in ('darwin', 'linux'), 'Resource profile supports explicit Darwin/Linux RSS units only')
    return 1 if platform == 'darwin' else 1024


def snapshot(phase):
    require(phase in PHASES, 'Unknown resource phase')
    before = time.perf_counter_ns()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    after = time.perf_counter_ns()
    return {'phase': phase, 'pid': os.getpid(), 'platform': sys.platform,
        'capturedAt': datetime.now(timezone.utc).isoformat(), 'monotonicBeforeNs': before, 'monotonicAfterNs': after,
        'rawMaxRss': usage.ru_maxrss, 'rawMaxRssToBytesMultiplier': rss_multiplier(sys.platform),
        'maxRssBytes': usage.ru_maxrss * rss_multiplier(sys.platform),
        'userCpuSeconds': usage.ru_utime, 'systemCpuSeconds': usage.ru_stime,
        'minorFaults': usage.ru_minflt, 'majorFaults': usage.ru_majflt,
        'voluntaryContextSwitches': usage.ru_nvcsw, 'involuntaryContextSwitches': usage.ru_nivcsw}


def summarize(samples):
    require(isinstance(samples, list) and len(samples) == 3, 'All three resource phases are required')
    for index, item in enumerate(samples):
        fields(item, {'phase', 'pid', 'platform', 'capturedAt', 'monotonicBeforeNs', 'monotonicAfterNs', 'rawMaxRss',
            'rawMaxRssToBytesMultiplier', 'maxRssBytes', 'userCpuSeconds', 'systemCpuSeconds'} | set(COUNTERS))
        require(item['phase'] == PHASES[index] and type(item['pid']) is int and item['pid'] > 0
                and item['pid'] == samples[0]['pid'] and item['platform'] == samples[0]['platform'], 'Resource phase/order/PID/platform differs')
        multiplier = rss_multiplier(item['platform'])
        require(type(item['rawMaxRssToBytesMultiplier']) is int and item['rawMaxRssToBytesMultiplier'] == multiplier,
                'Raw RSS unit conversion differs from the platform')
        for key in ('rawMaxRss', 'maxRssBytes', 'monotonicBeforeNs', 'monotonicAfterNs', *COUNTERS):
            require(type(item[key]) is int and 0 <= item[key] < 2**63, 'Invalid resource integer: ' + key)
        require(item['rawMaxRss'] > 0 and item['maxRssBytes'] == item['rawMaxRss'] * multiplier
                and item['monotonicAfterNs'] >= item['monotonicBeforeNs'], 'RSS or monotonic sampling bracket differs')
        try:
            captured = datetime.fromisoformat(item['capturedAt'])
            require(captured.tzinfo is not None, 'Resource timestamp lacks timezone')
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid resource timestamp') from error
        for key in ('userCpuSeconds', 'systemCpuSeconds'):
            require(type(item[key]) in (int, float) and math.isfinite(item[key]) and 0 <= item[key] < 1e12,
                    'Invalid CPU counter')
        if index:
            previous = samples[index - 1]
            require(item['monotonicBeforeNs'] >= previous['monotonicAfterNs']
                    and all(item[key] >= previous[key] for key in ('rawMaxRss', 'maxRssBytes', 'userCpuSeconds',
                                                                  'systemCpuSeconds', *COUNTERS)),
                    'Resource cumulative counter or sampling time moved backward')
    phases = {}
    for name, start, end in (('backend-load', samples[0], samples[1]), ('whole-scoring', samples[1], samples[2]),
                             ('load-and-scoring', samples[0], samples[2])):
        phases[name] = {'wallTimeLowerMs': (end['monotonicBeforeNs'] - start['monotonicAfterNs']) / 1e6,
            'wallTimeUpperMs': (end['monotonicAfterNs'] - start['monotonicBeforeNs']) / 1e6,
            'userCpuSecondsDelta': end['userCpuSeconds'] - start['userCpuSeconds'],
            'systemCpuSecondsDelta': end['systemCpuSeconds'] - start['systemCpuSeconds'],
            'maxRssHighWaterIncreaseBytes': end['maxRssBytes'] - start['maxRssBytes'],
            **{key + 'Delta': end[key] - start[key] for key in COUNTERS}}
    return {'scope': SCOPE, 'pid': samples[0]['pid'], 'platform': samples[0]['platform'],
            'maxRssBytesBeforeBackend': samples[0]['maxRssBytes'], 'maxRssBytesAfterBackend': samples[1]['maxRssBytes'],
            'maxRssBytesAfterWholeScoring': samples[2]['maxRssBytes'], 'phases': phases}


def measure(dataset, model_directory, identity, *, backend_factory=GLiClassBackend, capture=snapshot):
    development_dataset(dataset)
    samples = [capture(PHASES[0])]
    backend = backend_factory(model_directory)
    samples.append(capture(PHASES[1]))
    baseline = verify_report(dataset, measure_baseline(dataset, backend, identity))
    samples.append(capture(PHASES[2]))
    return sealed({'schemaVersion': SCHEMA, 'status': 'diagnostic_only', 'usage': 'development-only', **AUTHORITY,
        'sourceIdentity': identity, 'datasetSha256': fingerprint(dataset), 'baseline': baseline,
        'resourceSamples': samples, 'resourceSummary': summarize(samples),
        'limitations': ['RSS is a process high-water mark; its difference is not an isolated model allocation measurement.',
            'CPU counters exclude child-process work; timestamps bracket the resource syscall.',
            'One process and the same dependent development variants do not establish capacity or an accepted SLO.',
            'The profiling run is recorded separately from earlier latency runs; no controlled speed improvement is claimed.']})


def verify_profile(dataset, report):
    verify_seal(report, SCHEMA)
    fields(report, {'schemaVersion', 'sha256', 'status', 'usage', 'sourceIdentity', 'datasetSha256', 'baseline',
                    'resourceSamples', 'resourceSummary', 'limitations'} | set(AUTHORITY))
    require(report['status'] == 'diagnostic_only' and report['usage'] == 'development-only'
            and all(type(report.get(key)) is type(value) and report[key] == value for key, value in AUTHORITY.items()),
            'Resource profile cannot change development authority')
    require(report['datasetSha256'] == fingerprint(development_dataset(dataset)), 'Resource profile dataset differs')
    baseline = verify_report(dataset, report['baseline'])
    require(report['sourceIdentity'] == baseline['sourceIdentity'], 'Native scorer and resource profile use different sources')
    require(canonical_json(report['resourceSummary']) == canonical_json(summarize(report['resourceSamples'])),
            'Saved resource summary differs from raw snapshots')
    return report
