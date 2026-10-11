"""Whole unlabelled public development inputs on pinned optional native baselines."""
from collections import Counter
import hashlib
import io
import math
from pathlib import Path
import re
import subprocess
import tarfile

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import DecisionError, Policy, Request, canonical_json, fields, fingerprint
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.evaluation import percentile
from scripts.lib import decision_gliclass_baseline as gl, decision_nanojev_baseline as nano
from scripts.lib.decision_gliclass_resources import PHASES, snapshot, summarize as resources
from scripts.lib.decision_public_load import validate_context
from scripts.lib.decision_shadow_pilot import require

SCHEMA = 'agat.decision.public-support-native-baseline.v1'
AUTHORITY = {**gl.AUTHORITY, 'classificationAccuracyMeasured': False, 'representativeAgatTraffic': False,
             'sloAccepted': False, 'referenceLabelsVerified': 0}
SOURCE_PATHS = tuple(dict.fromkeys((*gl.SOURCE_PATHS, *nano.SOURCE_PATHS,
    'scripts/evaluate-public-native-baseline.py', 'scripts/test/test_decision_public_native_baseline.py',
    'scripts/test/test_decision_public_load.py', 'scripts/test/test_decision_public_context.py')))
CANDIDATES = {'gliclass': gl, 'nanojev': nano}


def frozen_sources(root):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=10).strip()
    raw = subprocess.check_output(['git', 'archive', commit, '--', *SOURCE_PATHS], cwd=root, timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require(all(path in sources or any(name.startswith(path + '/') for name in sources) for path in SOURCE_PATHS)
        and all((root / name).read_bytes() == content for name, content in sources.items()),
        'Commit the complete unlabelled diagnostic implementation before measuring')
    require(not subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '--', *SOURCE_PATHS],
                                       cwd=root, timeout=10), 'Untracked diagnostic implementation')
    return {'sourceCommit': commit, 'sourceFiles': {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}}


def candidate(kind):
    require(isinstance(kind, str) and kind in CANDIDATES, 'Unsupported pinned native baseline')
    return CANDIDATES[kind]


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1e12


def _source_identity(identity):
    fields(identity, {'sourceCommit', 'sourceFiles'})
    require(isinstance(identity['sourceCommit'], str) and re.fullmatch('[0-9a-f]{40}', identity['sourceCommit'])
        and isinstance(identity['sourceFiles'], dict) and identity['sourceFiles'] and all(isinstance(name, str)
            and not Path(name).is_absolute() and '..' not in Path(name).parts and isinstance(digest, str)
            and re.fullmatch('[0-9a-f]{64}', digest) for name, digest in identity['sourceFiles'].items()),
        'Invalid measured diagnostic source identity')


def _model(kind, model):
    module = candidate(kind); descriptor = module.catalog()
    fields(model, {'repository', 'revision', 'artifactSha256', 'tokenizerSha256', 'backend', 'profile', 'stack'} |
        ({'strictStateDictLoaded', 'weightsLayout'} if kind == 'nanojev' else set()))
    require(model['repository'] == descriptor['repository'] and model['revision'] == descriptor['revision']
        and model['artifactSha256'] == fingerprint(descriptor['files'])
        and model['tokenizerSha256'] == descriptor['files']['tokenizer.json' if kind == 'gliclass' else 'tokenizer/tokenizer.json']['sha256']
        and canonical_json(model['profile']) == canonical_json(module.PROFILE)
        and canonical_json(model['stack']['versions']) == canonical_json(module.PACKAGE_VERSIONS), 'Unpinned diagnostic model identity')
    if kind == 'gliclass':
        require(model['backend'] == 'gliclass-uni-encoder-native-logits', 'Different GLiClass execution backend')
    else:
        require(model['backend'] == 'nanojev-native-decision-heads-cpu-reference' and model['strictStateDictLoaded'] is True
            and model['stack']['nativeSourceFiles'] == {name: descriptor['files'][name]['sha256'] for name in
                ('source/scripts/predict_toy_decisions.py', 'source/scripts/train_toy_decisions.py')}, 'Different native NanoJev reference')
        layout = model['weightsLayout']
        fields(layout, {'tensorCount', 'tensorNamesSha256', 'headerSha256', 'dataBytes', 'parameterStorage'})
        require(type(layout['tensorCount']) is int and layout['tensorCount'] > 0 and type(layout['dataBytes']) is int
            and 0 < layout['dataBytes'] < descriptor['files']['best.safetensors']['bytes'] and layout['parameterStorage'] == 'float32'
            and all(isinstance(layout[key], str) and re.fullmatch('[0-9a-f]{64}', layout[key])
                    for key in ('tensorNamesSha256', 'headerSha256')), 'Invalid pinned checkpoint tensor layout')


def _call(kind, request, call, result, ordinal):
    common = {'ordinal', 'id', 'inputSha256', 'inferenceAttempted', 'inferenceMs', 'rejection', 'requestLogits'}
    extra = ({'renderedInputSha256', 'tokenIds'} if kind == 'gliclass' else
        {'payloadSha256', 'renderedPathsSha256', 'nativeCandidateIds', 'requestOptionIdsInNativeOrder', 'leafTokenIds', 'nativeLogits'})
    fields(call, common | extra)
    require(type(call['ordinal']) is int and call['ordinal'] == ordinal and call['id'] == request.id
        and call['inputSha256'] == request.input_sha256 and type(call['inferenceAttempted']) is bool, 'Native input/order binding differs')
    require(call['inferenceMs'] is None or _finite(call['inferenceMs']), 'Invalid native forward timing')
    require(call['inferenceAttempted'] == (call['inferenceMs'] is not None), 'Native forward timing missing or fabricated')
    require(isinstance(call['requestLogits'], list), 'Missing raw score inventory')
    render_error = None
    if kind == 'gliclass':
        try:
            expected_render = hashlib.sha256(gl.render_request(request).encode()).hexdigest()
        except DecisionError as error:
            expected_render, render_error = None, error.code
        require(call['renderedInputSha256'] == expected_render, 'Complete GLiClass render differs')
        paths = [call['tokenIds']]
        require(isinstance(paths[0], list) and len(paths[0]) <= 200_000
            and all(type(token) is int and 0 <= token < 50370 for token in paths[0]), 'Invalid full GLiClass token inventory')
        def validate():
            return gl.validate_encoding({'input_ids': paths[0], 'attention_mask': [1] * len(paths[0])}, len(request.options))
    else:
        try:
            payload, ordered = nano.render_payload(request)
            ids, texts = nano.rendered_paths(payload)
            expected_render = (fingerprint(payload), fingerprint(texts), ids, ordered)
        except DecisionError as error:
            render_error = error.code; expected_render = (None, None, [], [])
        require((call['payloadSha256'], call['renderedPathsSha256'], call['nativeCandidateIds'], call['requestOptionIdsInNativeOrder'])
            == expected_render, 'Complete NanoJev render or semantic candidate order differs')
        paths = call['leafTokenIds']
        require(isinstance(paths, list) and len(paths) <= 10 and all(isinstance(path, list) and len(path) <= 200_000
            and all(type(token) is int and 0 <= token < nano.VOCAB_SIZE for token in path) for path in paths), 'Invalid complete native paths')
        def validate():
            return nano.validate_paths(paths, request.kind, len(request.options))
    if result['status'] == 'error':
        require(result['reason'] == call['rejection'] and call['requestLogits'] == []
            and result['reason'] in {'context_bound_exceeded', 'invalid_candidate_tokens', 'unsupported_control_marker', 'backend_error'},
            'Unaccounted native refusal or logits on a failed call')
        if kind == 'nanojev': require(call['nativeLogits'] == [], 'Refused native paths have logits')
        if result['reason'] == 'unsupported_control_marker':
            require(render_error == result['reason'] and not any(paths) and not call['inferenceAttempted'], 'Control syntax was tokenized or inferred')
        elif result['reason'] in {'context_bound_exceeded', 'invalid_candidate_tokens'}:
            require(not call['inferenceAttempted'], 'Refused input reached native forward')
            try: validate()
            except DecisionError as error: require(error.code == result['reason'], 'Recorded context/candidate refusal differs')
            else: raise ValueError('Recorded paths should not have been refused')
    else:
        validate()
        logits = call['requestLogits']
        require(call['inferenceAttempted'] and call['rejection'] is None and result['inputTokens'] == sum(map(len, paths))
            and len(logits) == len(request.options) and all(type(z) in (int, float) and math.isfinite(z) for z in logits),
            'Complete tokens or native raw score inventory differs')
        if kind == 'nanojev':
            native = call['nativeLogits']
            require(isinstance(native, list) and len(native) == len(request.options)
                and all(type(z) in (int, float) and math.isfinite(z) for z in native)
                and (request.kind != 'boolean' or native[0] == 0), 'Invalid native decision-head logits')
            mapping = dict(zip(call['requestOptionIdsInNativeOrder'], native, strict=True))
            require(canonical_json(logits) == canonical_json([mapping[option.id] for option in request.options]), 'Native typed logits mapping changed')
    return sum(map(len, paths))


def _replay(request, call, result, model, input_tokens):
    require(_finite(result.get('durationMs')), 'Invalid whole-request duration')
    class Replay:
        identity = model
        def score(self, _request):
            if result['status'] == 'error': raise DecisionError(result['reason'], 'Recorded native refusal')
            return Scores(call['requestLogits'], input_tokens)
    rebuilt = DecisionEngine(Replay(), Policy()).decide(request.to_dict())
    rebuilt['durationMs'] = result['durationMs']
    require(canonical_json(rebuilt) == canonical_json(result), 'Typed policy outcome differs from raw native scores')


def recompute(context, kind, model, cases, audit):
    validate_context(context); _model(kind, model)
    require(isinstance(cases, list) and len(cases) == len(context['inputs']) and isinstance(audit, list)
        and len(audit) == 2 * len(cases), 'Use every original development input in both orders')
    orders = {'original': [], 'reversed': []}; changed = []; both = []; context_changed = []
    for index, (case, source) in enumerate(zip(cases, context['inputs'], strict=True)):
        fields(case, {'id', 'groupId', 'inputSha256', 'result', 'reversedResult'})
        require(all(case[key] == source[key] for key in ('id', 'groupId', 'inputSha256')), 'Whole original input or group order changed')
        for offset, (order, key) in enumerate((('original', 'result'), ('reversed', 'reversedResult'))):
            raw = source['request'] if not offset else {**source['request'], 'options': list(reversed(source['request']['options']))}
            request = Request.from_dict(raw); result = case[key]; call = audit[2 * index + offset]
            tokens = _call(kind, request, call, result, 2 * index + offset + 1)
            _replay(request, call, result, model, tokens)
            orders[order].append(result)
        pair = (case['result'], case['reversedResult'])
        if all(result['status'] != 'error' for result in pair):
            both.append(case['id'])
            if pair[0]['selectedOptionId'] != pair[1]['selectedOptionId']: changed.append(case['id'])
        if (pair[0]['reason'] == 'context_bound_exceeded') != (pair[1]['reason'] == 'context_bound_exceeded'):
            context_changed.append(case['id'])
    summaries = {}
    for order, rows in orders.items():
        counts = Counter(row['status'] for row in rows)
        summaries[order] = {'wholeCases': len(rows), 'computedCases': counts['ok'] + counts['abstain'],
            'acceptedCases': counts['ok'], 'abstainedCases': counts['abstain'], 'errorCases': counts['error'],
            'acceptedFractionAllCases': counts['ok'] / len(rows),
            'refusals': dict(Counter(row['reason'] for row in rows if row['status'] == 'error')),
            'wholeRequestP50Ms': percentile([row['durationMs'] for row in rows], .5),
            'wholeRequestP95Ms': percentile([row['durationMs'] for row in rows], .95)}
    durations = [call['inferenceMs'] for call in audit if call['inferenceMs'] is not None]
    token_counts = [sum(map(len, call['leafTokenIds'])) if kind == 'nanojev' else len(call['tokenIds']) for call in audit]
    return {'caseCount': len(cases), 'dependencyGroupCount': len({case['groupId'] for case in cases}),
        'wholeVariantCount': len(audit), 'nativeInferenceAttempts': sum(call['inferenceAttempted'] for call in audit),
        'wholeTokenPaths': sum(len(call['leafTokenIds']) if kind == 'nanojev' else bool(call['tokenIds']) for call in audit),
        'processedTokenPaths': sum((len(call['leafTokenIds']) if kind == 'nanojev' else 1) for call in audit if call['inferenceAttempted']),
        'recordedInputTokens': sum(token_counts),
        'processedInputTokens': sum(count for count, call in zip(token_counts, audit, strict=True) if call['inferenceAttempted']),
        'bothOrdersComputedCaseCount': len(both), 'changedArgmaxBothComputedCaseIds': changed,
        'contextAdmissionChangedCaseIds': context_changed, 'orders': summaries,
        'nativeForwardP50Ms': percentile(durations, .5), 'nativeForwardP95Ms': percentile(durations, .95)}


def measure(context, kind, model_dir, source_identity, *, backend_factory=None, capture=snapshot):
    validate_context(context); module = candidate(kind); _source_identity(source_identity)
    samples = [capture(PHASES[0])]
    native = (backend_factory or (gl.GLiClassBackend if kind == 'gliclass' else nano.NanoJevBackend))(model_dir)
    samples.append(capture(PHASES[1]))
    class Record:
        identity = native.identity
        def score(self, request):
            try:
                scores = native.score(request)
            except Exception:
                native.audit[-1]['requestLogits'] = []
                raise
            native.audit[-1]['requestLogits'] = list(scores.logits)
            return scores
    engine = DecisionEngine(Record(), Policy()); cases = []
    for source in context['inputs']:
        request = source['request']; reverse = {**request, 'options': list(reversed(request['options']))}
        cases.append({key: source[key] for key in ('id', 'groupId', 'inputSha256')} |
            {'result': engine.decide(request), 'reversedResult': engine.decide(reverse)})
    samples.append(capture(PHASES[2]))
    summary = recompute(context, kind, native.identity, cases, native.audit)
    return sealed({'schemaVersion': SCHEMA, 'status': 'unlabelled_development_diagnostic', 'usage': 'development-only', **AUTHORITY,
        'candidate': kind, 'contextSha256': context['sha256'], 'sourceIdentity': source_identity,
        'modelDescriptorSha256': fingerprint(module.catalog()), 'model': native.identity, 'modelLoadMs': native.load_ms,
        'cases': cases, 'nativeCalls': native.audit, 'summary': summary, 'resourceSamples': samples, 'resourceSummary': resources(samples),
        'limitations': ['No reference labels, accuracy, calibration, holdout or human review are used or inferred.',
            'All original public development inputs are retained, including context refusals; sources are not translated or truncated.',
            'Option orders are dependent variants; public sources do not establish representative Agat traffic or statistical independence.',
            'CPU profiles differ from older MLX runs; these are process diagnostics, not controlled latency comparisons or model-only allocation.',
            'No weights, policy, temperature, labels, splits or serving profiles are fitted or promoted.']})


def verify_report(context, report):
    validate_context(context); verify_seal(report, SCHEMA)
    fields(report, {'schemaVersion', 'sha256', 'status', 'usage', 'candidate', 'contextSha256', 'sourceIdentity',
        'modelDescriptorSha256', 'model', 'modelLoadMs', 'cases', 'nativeCalls', 'summary', 'resourceSamples', 'resourceSummary', 'limitations'} | set(AUTHORITY))
    require(report['status'] == 'unlabelled_development_diagnostic' and report['usage'] == 'development-only'
        and all(type(report.get(key)) is type(value) and report[key] == value for key, value in AUTHORITY.items()), 'Diagnostic grants unsupported authority')
    _source_identity(report['sourceIdentity']); module = candidate(report['candidate'])
    require(report['contextSha256'] == context['sha256'] and report['modelDescriptorSha256'] == fingerprint(module.catalog()),
        'Original unlabelled context or pinned model descriptor differs')
    require(_finite(report['modelLoadMs']), 'Invalid backend load duration')
    require(canonical_json(report['summary']) == canonical_json(recompute(context, report['candidate'], report['model'], report['cases'], report['nativeCalls']))
        and canonical_json(report['resourceSummary']) == canonical_json(resources(report['resourceSamples'])), 'Saved diagnostic summary differs from whole inventory')
    return report
