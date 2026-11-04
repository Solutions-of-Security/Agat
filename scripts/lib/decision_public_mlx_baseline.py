"""Unlabelled whole-input MLX observations using the existing, unchanged scorer."""
from collections import Counter
import hashlib
import importlib.metadata
import io
import math
from pathlib import Path
import platform
import subprocess
import tarfile
import time
from types import SimpleNamespace

from decision_runtime import implementation_sha256
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import DecisionError, LETTERS, Policy, Request, canonical_json, fields, fingerprint, parse_json
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.evaluation import percentile
from decision_runtime.mlx_backend import MlxBackend, PROMPT_VERSION, prompt_parts
from decision_runtime.model_store import verify_manifest
from scripts.lib.decision_gliclass_resources import PHASES, snapshot, summarize as resources
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_public_native_baseline import AUTHORITY, SOURCE_PATHS as NATIVE_SOURCES
from scripts.lib.decision_shadow_pilot import require

ROOT = Path(__file__).resolve().parents[2]
DESCRIPTOR_PATH = ROOT / 'docs/qualification/local-decisions/baselines/public-mlx-candidates.json'
SCHEMA = 'agat.decision.public-support-mlx-baseline.v1'
NAMES = ('decider-2b', 'qwen3.5-2b-base')
VOCAB_SIZE = 248320  # Both immutable publisher configs declare this vocabulary.
PROFILE = {'maxInputTokens': 2048, 'allocatorCacheLimitBytes': 128 * 1024**2,
           'allocatorWiredLimitBytes': 4096 * 1024**2, 'quantization': 'none'}
SOURCE_PATHS = (*NATIVE_SOURCES, 'scripts/evaluate-public-mlx-baseline.py',
    'scripts/test/test_decision_public_mlx_baseline.py',
    'docs/qualification/local-decisions/baselines/public-mlx-candidates.json')


def frozen_sources(root):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=10).strip()
    raw = subprocess.check_output(['git', 'archive', commit, '--', *SOURCE_PATHS], cwd=root, timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require(all(path in sources or any(name.startswith(path + '/') for name in sources) for path in SOURCE_PATHS)
        and all((root / name).read_bytes() == raw for name, raw in sources.items()), 'Commit complete diagnostic sources before measuring')
    require(not subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '--', *SOURCE_PATHS], cwd=root, timeout=10),
        'Untracked diagnostic implementation')
    return {'sourceCommit': commit, 'sourceFiles': {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}}


def descriptor(name):
    require(name in NAMES, 'Unknown pinned MLX baseline')
    value = parse_json(DESCRIPTOR_PATH.read_bytes())
    fields(value, {'schemaVersion', 'candidates'}); fields(value['candidates'], set(NAMES))
    require(value['schemaVersion'] == 'agat.decision.public-mlx-candidates.v1', 'Invalid MLX artifact descriptor')
    row = value['candidates'][name]
    fields(row, {'name', 'repository', 'revision', 'license', 'role', 'files', 'artifactSha256', 'tokenizerSha256'})
    entry = parse_json((ROOT / 'decision_runtime/models.json').read_bytes())[name]
    require(row['name'] == name and all(row[key] == val for key, val in entry.items()), 'Publisher revision differs')
    require(isinstance(row['files'], dict) and {'config.json', 'tokenizer.json', 'tokenizer_config.json'} <= row['files'].keys(),
        'Incomplete pinned MLX input inventory')
    for filename, record in row['files'].items():
        fields(record, {'bytes', 'sha256'})
        require(Path(filename).name == filename and type(record['bytes']) is int and 0 < record['bytes'] <= 5 * 1024**3
            and isinstance(record['sha256'], str) and len(record['sha256']) == 64
            and all(letter in '0123456789abcdef' for letter in record['sha256']), 'Invalid pinned MLX file')
    require(row['artifactSha256'] == fingerprint({name: val['sha256'] for name, val in row['files'].items()})
        and row['tokenizerSha256'] == row['files']['tokenizer.json']['sha256'], 'Pinned MLX artifact differs')
    return row


def local_manifest(name, path):
    expected = descriptor(name); manifest, directory = verify_manifest(path)
    fields(manifest, {'schemaVersion', 'name', 'repository', 'revision', 'license', 'role', 'snapshot', 'files', 'artifactSha256'})
    require(type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 1
        and all(manifest[key] == expected[key] for key in ('name', 'repository', 'revision', 'license', 'role', 'artifactSha256'))
        and manifest['files'] == {key: value['sha256'] for key, value in expected['files'].items()}
        and all((directory / key).stat().st_size == value['bytes'] for key, value in expected['files'].items()),
        'Local bytes differ from the immutable publisher descriptor')
    config = parse_json((directory / 'config.json').read_bytes())
    vocabulary = config.get('text_config', config).get('vocab_size')
    require(type(vocabulary) is int and vocabulary == VOCAB_SIZE, 'Pinned vocabulary differs from token audit bound')
    return manifest, directory


def package_pins():
    return dict(line.strip().split('==') for line in (ROOT / 'decision_runtime/requirements-mlx.txt').read_text().splitlines()
                if line.strip() and not line.startswith('#'))


def environment():
    versions = {name: importlib.metadata.version(name) for name in package_pins()}
    require(versions == package_pins() and platform.system() == 'Darwin' and platform.machine() == 'arm64',
        'Use the pinned MLX stack on Apple Silicon')
    return {'python': platform.python_version(), 'system': platform.system(), 'machine': platform.machine(), 'versions': versions}


def _environment(stack):
    fields(stack, {'python', 'system', 'machine', 'versions'})
    require(isinstance(stack['python'], str) and stack['python'].startswith('3.13.') and stack['system'] == 'Darwin' and stack['machine'] == 'arm64'
        and stack['versions'] == package_pins(), 'Different measured native dependency stack')


def _source_identity(identity):
    fields(identity, {'sourceCommit', 'sourceFiles'})
    require(isinstance(identity['sourceCommit'], str) and len(identity['sourceCommit']) == 40
        and all(ch in '0123456789abcdef' for ch in identity['sourceCommit'])
        and isinstance(identity['sourceFiles'], dict) and identity['sourceFiles'], 'Invalid measured source identity')
    for name, digest in identity['sourceFiles'].items():
        require(isinstance(name, str) and not Path(name).is_absolute() and '..' not in Path(name).parts
            and isinstance(digest, str) and len(digest) == 64 and all(ch in '0123456789abcdef' for ch in digest), 'Invalid source path or SHA')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e12


def _memory(native, phase):
    return {'phase': phase, 'activeBytes': native.mx.get_active_memory(),
        'cacheBytes': native.mx.get_cache_memory(), 'peakBytes': native.mx.get_peak_memory()}


class ObservedBackend:
    """Observe actual tokenizer and backbone entry; delegate all scoring to MlxBackend.score."""
    def __init__(self, native):
        self.native = native; self.identity = native.identity; self.audit = []; self.current = None
        tokenizer = native.tokenizer; model = native.text_model.model
        owner = self
        class Tokenizer:
            def encode(self, text, *args, **kwargs):
                value = tokenizer.encode(text, *args, **kwargs)
                require(kwargs == {'add_special_tokens': False} and not args and isinstance(value, list), 'Unexpected native encoding path')
                owner.current['tokenizerEvents'].append({'textSha256': hashlib.sha256(text.encode()).hexdigest(), 'tokenIds': list(value)})
                return value
            def decode(self, ids, *args, **kwargs):
                value = tokenizer.decode(ids, *args, **kwargs)
                owner.current['continuationDecodedSha256'].append(hashlib.sha256(value.encode()).hexdigest())
                return value
        def observed_model(*args, **kwargs):
            require(owner.current['nativeForwardAttempts'] == 0 and len(args) == 1 and not kwargs, 'Unexpected cached/batched backbone path')
            owner.current['nativeForwardAttempts'] = 1
            owner.forward_started = time.perf_counter_ns()
            return model(*args, **kwargs)
        # Replace only per-process observer references; the model/head/weights stay untouched.
        native.tokenizer = Tokenizer()
        native.text_model = SimpleNamespace(model=observed_model)

    def score(self, request):
        require(self.current is None, 'Sequential observation only')
        row = {'ordinal': len(self.audit) + 1, 'id': request.id, 'inputSha256': request.input_sha256,
            'promptPartsSha256': fingerprint(prompt_parts(request)), 'tokenizerEvents': [], 'continuationDecodedSha256': [],
            'nativeForwardAttempts': 0, 'nativeInferenceMs': None, 'nativeBackendScoreMs': None, 'requestLogits': [], 'rejection': None}
        self.current = row; started = time.perf_counter_ns()
        try:
            scores = self.native.score(request)
            require(isinstance(scores, Scores) and len(scores.logits) == len(request.options)
                and all(type(z) in (int, float) and math.isfinite(z) for z in scores.logits), 'Invalid native scores')
            row['requestLogits'] = list(scores.logits)
            return scores
        except DecisionError as error:
            row['rejection'] = error.code; raise
        except Exception:
            row['rejection'] = 'backend_error'; raise
        finally:
            ended = time.perf_counter_ns()
            row['nativeBackendScoreMs'] = round((ended - started) / 1e6, 3)
            if row['nativeForwardAttempts']: row['nativeInferenceMs'] = round((ended - self.forward_started) / 1e6, 3)
            self.audit.append(row); self.current = None


def _model(name, model):
    expected = descriptor(name)
    fields(model, {'repository', 'revision', 'artifactSha256', 'tokenizerSha256', 'promptVersion', 'backend', 'mlxVersion',
        'mlxLmVersion', 'implementationSha256'} | set(PROFILE))
    require(all(model[key] == expected[key] for key in ('repository', 'revision', 'artifactSha256', 'tokenizerSha256'))
        and model['promptVersion'] == PROMPT_VERSION and model['backend'] == 'mlx-qwen3.5-letter-logits'
        and model['mlxVersion'] == package_pins()['mlx'] and model['mlxLmVersion'] == package_pins()['mlx-lm']
        and model['implementationSha256'] == implementation_sha256()
        and canonical_json({key: model[key] for key in PROFILE}) == canonical_json(PROFILE), 'Scorer or execution profile changed')


def _call(request, row, result, ordinal, model):
    fields(row, {'ordinal', 'id', 'inputSha256', 'promptPartsSha256', 'tokenizerEvents', 'continuationDecodedSha256',
        'nativeForwardAttempts', 'nativeInferenceMs', 'nativeBackendScoreMs', 'requestLogits', 'rejection'})
    parts = prompt_parts(request)
    require(type(row['ordinal']) is int and row['ordinal'] == ordinal and row['id'] == request.id
        and row['inputSha256'] == request.input_sha256 and row['promptPartsSha256'] == fingerprint(parts), 'Whole input/order binding differs')
    events = row['tokenizerEvents']
    require(isinstance(events, list) and 2 <= len(events) <= 2 + len(request.options), 'Incomplete native prompt tokenization')
    for index, event in enumerate(events):
        fields(event, {'textSha256', 'tokenIds'})
        text = parts[index] if index < 2 else LETTERS[index - 2]
        require(event['textSha256'] == hashlib.sha256(text.encode()).hexdigest() and isinstance(event['tokenIds'], list)
            and len(event['tokenIds']) <= 200_000 and all(type(token) is int and 0 <= token < VOCAB_SIZE for token in event['tokenIds']),
            'Native encoding text or full token inventory differs')
    count = sum(len(event['tokenIds']) for event in events[:2])
    require(type(row['nativeForwardAttempts']) is int and row['nativeForwardAttempts'] in (0, 1)
        and _finite(row['nativeBackendScoreMs']) and (row['nativeInferenceMs'] is None or _finite(row['nativeInferenceMs']))
        and (row['nativeInferenceMs'] is not None) == bool(row['nativeForwardAttempts']), 'Invalid native call/timing accounting')
    continuation = row['continuationDecodedSha256']
    require(isinstance(continuation, list) and len(continuation) <= len(request.options), 'Invalid continuation inventory')
    labels = [event['tokenIds'] for event in events[2:]]
    valid_labels = len(labels) == len(request.options) and all(len(ids) == 1 for ids in labels) and len({ids[0] for ids in labels if ids}) == len(labels)
    expected_continuation = [hashlib.sha256((''.join(parts) + letter).encode()).hexdigest() for letter in LETTERS[:len(request.options)]]
    if row['nativeForwardAttempts']:
        require(0 < count <= PROFILE['maxInputTokens'] and valid_labels and continuation == expected_continuation
            and row['nativeInferenceMs'] <= row['nativeBackendScoreMs'] + .001, 'Refused or incomplete input reached the native backbone')
    if result['status'] == 'error':
        require(result['reason'] == row['rejection'] and row['requestLogits'] == []
            and result['reason'] in ('context_too_long', 'unsupported_tokenizer', 'backend_error'), 'Unaccounted native refusal')
        if result['reason'] == 'context_too_long':
            require(count > PROFILE['maxInputTokens'] and len(events) == 2 and continuation == []
                and row['nativeForwardAttempts'] == 0, 'Oversize input reached the backbone or lost tokens')
        elif result['reason'] == 'unsupported_tokenizer':
            require(count <= PROFILE['maxInputTokens'] and row['nativeForwardAttempts'] == 0
                and (not valid_labels or continuation != expected_continuation), 'Invented tokenizer refusal')
    else:
        require(0 < count <= PROFILE['maxInputTokens'] and valid_labels and continuation == expected_continuation
            and row['nativeForwardAttempts'] == 1 and row['rejection'] is None and result['inputTokens'] == count
            and isinstance(row['requestLogits'], list) and len(row['requestLogits']) == len(request.options)
            and all(type(z) in (int, float) and math.isfinite(z) for z in row['requestLogits']), 'Native complete scoring contract differs')
    require(_finite(result.get('durationMs')) and row['nativeBackendScoreMs'] <= result['durationMs'] + .001, 'Invalid whole-request timing')
    class Replay:
        identity = model
        def score(self, _request):
            if result['status'] == 'error': raise DecisionError(result['reason'], 'Recorded native refusal')
            return Scores(row['requestLogits'], count)
    rebuilt = DecisionEngine(Replay(), Policy()).decide(request.to_dict()); rebuilt['durationMs'] = result['durationMs']
    require(canonical_json(rebuilt) == canonical_json(result), 'Typed policy outcome differs from native logits')
    return count


def recompute(context, name, model, cases, audit):
    validate_context(context); _model(name, model)
    require(isinstance(cases, list) and len(cases) == len(context['inputs']) and isinstance(audit, list) and len(audit) == 2 * len(cases),
        'Retain every original case in both orders')
    orders = {'original': [], 'reversed': []}; changed = []; both = []; admission = []; token_counts = []
    for index, (case, source) in enumerate(zip(cases, context['inputs'], strict=True)):
        fields(case, {'id', 'groupId', 'inputSha256', 'result', 'reversedResult'})
        require(all(case[key] == source[key] for key in ('id', 'groupId', 'inputSha256')), 'Case/group/source order differs')
        for offset, (order, key) in enumerate((('original', 'result'), ('reversed', 'reversedResult'))):
            raw = source['request'] if not offset else {**source['request'], 'options': list(reversed(source['request']['options']))}
            token_counts.append(_call(Request.from_dict(raw), audit[2 * index + offset], case[key], 2 * index + offset + 1, model))
            orders[order].append(case[key])
        pair = (case['result'], case['reversedResult'])
        if all(row['status'] != 'error' for row in pair):
            both.append(case['id'])
            if pair[0]['selectedOptionId'] != pair[1]['selectedOptionId']: changed.append(case['id'])
        if (pair[0]['reason'] == 'context_too_long') != (pair[1]['reason'] == 'context_too_long'): admission.append(case['id'])
    summaries = {}
    for order, rows in orders.items():
        counts = Counter(row['status'] for row in rows)
        summaries[order] = {'wholeCases': len(rows), 'computedCases': counts['ok'] + counts['abstain'], 'acceptedCases': counts['ok'],
            'abstainedCases': counts['abstain'], 'errorCases': counts['error'], 'acceptedFractionAllCases': counts['ok'] / len(rows),
            'refusals': dict(Counter(row['reason'] for row in rows if row['status'] == 'error')),
            'wholeRequestP50Ms': percentile([row['durationMs'] for row in rows], .5), 'wholeRequestP95Ms': percentile([row['durationMs'] for row in rows], .95)}
    durations = [row['nativeInferenceMs'] for row in audit if row['nativeInferenceMs'] is not None]
    return {'caseCount': len(cases), 'dependencyGroupCount': len({row['groupId'] for row in cases}), 'wholeVariantCount': len(audit),
        'nativeInferenceAttempts': sum(row['nativeForwardAttempts'] for row in audit), 'recordedInputTokens': sum(token_counts),
        'processedInputTokens': sum(n for n, row in zip(token_counts, audit, strict=True) if row['nativeForwardAttempts']),
        'bothOrdersComputedCaseCount': len(both), 'changedArgmaxBothComputedCaseIds': changed, 'contextAdmissionChangedCaseIds': admission,
        'nativeForwardP50Ms': percentile(durations, .5), 'nativeForwardP95Ms': percentile(durations, .95), 'orders': summaries}


def measure(context, name, manifest_path, source_identity, *, backend_factory=None, capture=snapshot, env=None):
    validate_context(context); descriptor(name); _source_identity(source_identity)
    samples = [capture(PHASES[0])]
    stack = environment() if env is None else env
    _environment(stack)
    native = (backend_factory or (lambda path: MlxBackend(path, 2048, cache_limit_mib=128, wired_limit_mib=4096)))(manifest_path)
    _model(name, native.identity)
    samples.append(capture(PHASES[1])); memory = [_memory(native, PHASES[1])]
    observed = ObservedBackend(native); engine = DecisionEngine(observed, Policy()); cases = []
    for source in context['inputs']:
        request = source['request']; reverse = {**request, 'options': list(reversed(request['options']))}
        cases.append({key: source[key] for key in ('id', 'groupId', 'inputSha256')} |
            {'result': engine.decide(request), 'reversedResult': engine.decide(reverse)})
    samples.append(capture(PHASES[2])); memory.append(_memory(native, PHASES[2]))
    return sealed({'schemaVersion': SCHEMA, 'status': 'unlabelled_development_diagnostic', 'usage': 'development-only', **AUTHORITY,
        'candidate': name, 'contextSha256': context['sha256'], 'sourceIdentity': source_identity, 'modelDescriptorSha256': fingerprint(descriptor(name)),
        'model': native.identity, 'modelLoadMs': native.load_ms, 'environment': stack, 'cases': cases, 'nativeCalls': observed.audit,
        'summary': recompute(context, name, native.identity, cases, observed.audit), 'resourceSamples': samples,
        'resourceSummary': resources(samples), 'mlxMemorySamples': memory,
        'limitations': ['No reference labels, accuracy, calibration, holdout, representative Agat/Russian quality or routing qualification.',
            'Whole original inputs and both dependent option orders; no translation, truncation or another model context prefilter.',
            'The original MlxBackend.score computes fresh full prompts and selected letter logits; observers do not change the graph/head/weights.',
            'Forward observation begins at backbone entry and ends after original score returns, including head, mx.eval and tolist.',
            'RUSAGE_SELF and process-global MLX allocator memory are distinct observations, not model-only allocation or an accepted SLO.']})


def verify_report(context, report):
    validate_context(context); verify_seal(report, SCHEMA)
    fields(report, {'schemaVersion', 'sha256', 'status', 'usage', 'candidate', 'contextSha256', 'sourceIdentity', 'modelDescriptorSha256',
        'model', 'modelLoadMs', 'environment', 'cases', 'nativeCalls', 'summary', 'resourceSamples', 'resourceSummary', 'mlxMemorySamples', 'limitations'} | set(AUTHORITY))
    require(report['status'] == 'unlabelled_development_diagnostic' and report['usage'] == 'development-only'
        and all(type(report.get(key)) is type(value) and report[key] == value for key, value in AUTHORITY.items()), 'Unsupported diagnostic authority')
    require(report['contextSha256'] == context['sha256'] and report['modelDescriptorSha256'] == fingerprint(descriptor(report['candidate']))
        and _finite(report['modelLoadMs']), 'Context, publisher descriptor or load timing differs')
    _environment(report['environment']); _source_identity(report['sourceIdentity'])
    require(canonical_json(report['summary']) == canonical_json(recompute(context, report['candidate'], report['model'], report['cases'], report['nativeCalls']))
        and canonical_json(report['resourceSummary']) == canonical_json(resources(report['resourceSamples'])), 'Cached counts or process resource arithmetic differs')
    memory = report['mlxMemorySamples']; require(isinstance(memory, list) and len(memory) == 2, 'Missing MLX allocator phases')
    for index, row in enumerate(memory):
        fields(row, {'phase', 'activeBytes', 'cacheBytes', 'peakBytes'})
        require(row['phase'] == PHASES[index + 1] and all(type(row[key]) is int and 0 <= row[key] < 2**63 for key in ('activeBytes', 'cacheBytes', 'peakBytes'))
            and row['peakBytes'] >= row['activeBytes'] and (not index or row['peakBytes'] >= memory[0]['peakBytes']), 'Invalid MLX allocator memory')
    return report
