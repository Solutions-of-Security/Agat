"""Native logits on original published Russian development references; Agat authority stays closed."""
from collections import Counter
from datetime import datetime
import math

from decision_runtime import VERSION
from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.contracts import Policy, Request, canonical_json, fields, fingerprint
from decision_runtime.engine import DecisionEngine
from decision_runtime.evaluation import evaluate, metrics, percentile
from decision_runtime.mlx_backend import MlxBackend
from scripts.lib import decision_gliclass_baseline as gl, decision_nanojev_baseline as nano
from scripts.lib import decision_public_native_baseline as cpu, decision_public_mlx_baseline as mlx
from scripts.lib.decision_gliclass_resources import PHASES, snapshot, summarize as resources
from scripts.lib.decision_shadow_pilot import require
from scripts.lib import decision_terra_corpus as corpus

SCHEMA = 'agat.decision.external-terra-native-baseline.v1'
NAMES = ('gliclass', 'nanojev', 'decider-2b', 'qwen3.5-2b-base')
AUTHORITY = {**corpus.AUTHORITY, 'classificationAccuracyMeasured': True}
SOURCE_PATHS = (*mlx.SOURCE_PATHS, 'scripts/import-terra-development.py',
    'scripts/test/test_decision_terra_corpus.py', corpus.DESCRIPTOR,
    'scripts/evaluate-terra-baseline.py', 'scripts/test/test_decision_terra_baseline.py')
LIMITATIONS = [
    'Only original published TERRa development references; no local blind reviews or representative Agat qualification.',
    'Pretraining contamination has not been assessed; this public corpus is not claimed unseen by the checkpoints.',
    'All original inputs and both dependent option orders; no translation, truncation, foreign context prefilter or label rewriting.',
    'Fixed uncalibrated temperature=1 and existing 0.8/0.1 policy; no weights, thresholds or calibration fitted.',
    'Normalized premise groups and option-order variants are dependent; no statistical independence or production generalization claim.',
    'One-host CPU/MLX resource and latency observations have different profiles/scopes; no controlled speed, capacity or accepted SLO.',
    'RUSAGE_SELF covers the whole Python process; MLX allocator samples are a distinct scope, not model-only memory.']


def candidate_descriptor(name):
    require(name in NAMES, 'Unknown pinned external baseline')
    return (gl.catalog() if name == 'gliclass' else nano.catalog()) if name in NAMES[:2] else mlx.descriptor(name)


def model_identity(name, model):
    require(name in NAMES, 'Unknown pinned external baseline')
    if name in NAMES[:2]: cpu._model(name, model)
    else: mlx._model(name, model)


def stamp(value):
    require(isinstance(value, str), 'Missing evaluation timestamp')
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, 'Missing evaluation timezone')
    return parsed


def recompute(dataset, archive_raw, license_raw, name, model, evaluation, audit):
    labelled = corpus.evaluation_dataset(dataset, archive_raw, license_raw)
    model_identity(name, model)
    fields(evaluation, {'schemaVersion', 'runtimeVersion', 'startedAt', 'createdAt', 'dataset', 'model', 'policy',
        'qualityGate', 'metrics', 'families', 'timing', 'cases', 'optionOrder'})
    require(evaluation['schemaVersion'] == 'agat.decision.evaluation.v1' and evaluation['runtimeVersion'] == VERSION
        and evaluation['qualityGate'] == 'not_configured' and evaluation['model'] == model
        and canonical_json(evaluation['policy']) == canonical_json(Policy().to_dict())
        and stamp(evaluation['createdAt']) >= stamp(evaluation['startedAt']), 'Scoring runtime/policy/time differs')
    cases = labelled['cases']
    require(canonical_json(evaluation['dataset']) == canonical_json(dict(id=labelled['id'], sha256=fingerprint(labelled),
        split='development', groupCount=len({v['groupId'] for v in cases}), labelSources=['published-corpus'])),
        'Wrong corpus reference scope or denominator')
    rows = evaluation['cases']
    require(isinstance(rows, list) and len(rows) == len(cases) and isinstance(audit, list) and len(audit) == 2 * len(cases),
        'Incomplete original/reversed native inventory')
    reverse_rows, tokens, processed_tokens, forwards, inference_ms = [], [], 0, 0, []
    for index, (source, row) in enumerate(zip(cases, rows, strict=True)):
        fields(row, {'id', 'family', 'groupId', 'labelSource', 'expectedOptionId', 'result', 'correct', 'reversedResult', 'orderAgreement'})
        require(all(row[k] == source[k] for k in ('id', 'family', 'groupId', 'labelSource', 'expectedOptionId')),
            'Published reference, source group or row order differs')
        for reverse in (False, True):
            request = source['request'] if not reverse else {**source['request'], 'options': list(reversed(source['request']['options']))}
            request = Request.from_dict(request); result = row['reversedResult' if reverse else 'result']
            ordinal = 2 * index + int(reverse) + 1; call = audit[ordinal - 1]
            if name in NAMES[:2]:
                count = cpu._call(name, request, call, result, ordinal)
                cpu._replay(request, call, result, model, count)
                attempted = int(call['inferenceAttempted']); duration = call['inferenceMs']
                require(duration is None or duration <= result['durationMs'] + .001, 'Forward exceeds whole-request interval')
            else:
                count = mlx._call(request, call, result, ordinal, model)
                attempted = call['nativeForwardAttempts']; duration = call['nativeInferenceMs']
            tokens.append(count); forwards += attempted
            if attempted: processed_tokens += count; inference_ms.append(duration)
        require(type(row['correct']) is bool and row['correct'] == (row['result']['selectedOptionId'] == source['expectedOptionId']),
            'Cached reference correctness differs')
        agreement = (row['result']['selectedOptionId'] == row['reversedResult']['selectedOptionId']
            if row['result']['status'] != 'error' and row['reversedResult']['status'] != 'error' else None)
        require(type(row['orderAgreement']) is type(agreement) and row['orderAgreement'] == agreement, 'Cached order agreement differs')
        reverse_rows.append({**{k: source[k] for k in ('id', 'family', 'groupId', 'labelSource', 'expectedOptionId')},
            'result': row['reversedResult'], 'correct': row['reversedResult']['selectedOptionId'] == source['expectedOptionId']})
    original_metrics, reversed_metrics = metrics(rows), metrics(reverse_rows)
    paired = [row for row in rows if row['orderAgreement'] is not None]
    order = dict(attemptedPairs=len(rows), scoredPairs=len(paired), agreement=sum(row['orderAgreement'] for row in paired) / len(paired) if paired else None,
        reversedBackendErrors=sum(row['reversedResult']['status'] == 'error' for row in rows),
        reversedAccuracyAllAttempts=sum(row['correct'] for row in reverse_rows) / len(rows))
    require(canonical_json(evaluation['metrics']) == canonical_json(original_metrics)
        and canonical_json(evaluation['families']) == canonical_json({family: metrics([row for row in rows if row['family'] == family])
            for family in sorted({row['family'] for row in rows})}) and canonical_json(evaluation['optionOrder']) == canonical_json(order),
        'Cached accuracy/NLL/Brier/ECE/F1/order metrics differ')
    fields(evaluation['timing'], {'modelLoadMs', 'firstRequestMs', 'subsequentOriginalRequestsP95Ms'})
    require(canonical_json(evaluation['timing']) == canonical_json(dict(modelLoadMs=evaluation['timing']['modelLoadMs'],
        firstRequestMs=rows[0]['result']['durationMs'], subsequentOriginalRequestsP95Ms=percentile(
            [row['result']['durationMs'] for row in rows[1:] if row['result']['status'] != 'error'], .95))), 'Cached timing differs')
    return dict(caseCount=len(rows), normalizedPremiseGroups=len({v['groupId'] for v in cases}), publishedReferenceLabels=len(rows),
        dependentOrderVariants=2 * len(rows), nativeInferenceAttempts=forwards, recordedInputTokens=sum(tokens), processedInputTokens=processed_tokens,
        wholePromptOrCandidateTokensMin=min(tokens), wholePromptOrCandidateTokensMax=max(tokens),
        nativeForwardP50Ms=percentile(inference_ms, .5), nativeForwardP95Ms=percentile(inference_ms, .95),
        original=original_metrics, reversed=reversed_metrics, changedArgmaxBothComputed=sum(not row['orderAgreement'] for row in paired),
        bothOrdersComputed=len(paired), refusalsByOrder={order: dict(Counter(row[key]['reason'] for row in rows if row[key]['status'] == 'error'))
            for order, key in (('original', 'result'), ('reversed', 'reversedResult'))})


def measure(dataset, archive_raw, license_raw, name, model_path, identity, *, backend_factory=None, capture=snapshot, env=None):
    corpus.evaluation_dataset(dataset, archive_raw, license_raw); candidate_descriptor(name); cpu._source_identity(identity)
    samples = [capture(PHASES[0])]; memory = []; stack = None
    if name in NAMES[:2]:
        native = (backend_factory or (gl.GLiClassBackend if name == 'gliclass' else nano.NanoJevBackend))(model_path)
        class Record:
            identity = native.identity
            load_ms = native.load_ms
            def score(self, request):
                try: scores = native.score(request)
                except Exception:
                    native.audit[-1]['requestLogits'] = []; raise
                native.audit[-1]['requestLogits'] = list(scores.logits); return scores
        observed = Record()
    else:
        stack = mlx.environment() if env is None else env; mlx._environment(stack)
        native = (backend_factory or (lambda path: MlxBackend(path, 2048, cache_limit_mib=128, wired_limit_mib=4096)))(model_path)
        observed = mlx.ObservedBackend(native); observed.load_ms = native.load_ms
        memory.append(mlx._memory(native, PHASES[1]))
    model_identity(name, native.identity); samples.append(capture(PHASES[1]))
    evaluation = evaluate(DecisionEngine(observed, Policy()), corpus.evaluation_dataset(dataset, archive_raw, license_raw),
        'development', reverse_options=True)
    samples.append(capture(PHASES[2]))
    if name not in NAMES[:2]: memory.append(mlx._memory(native, PHASES[2]))
    audit = native.audit if name in NAMES[:2] else observed.audit
    report = sealed(dict(schemaVersion=SCHEMA, status='external_development_diagnostic', usage='external-development-only', **AUTHORITY,
        referenceScope=dataset['referenceScope'], candidate=name, corpusSha256=dataset['sha256'], sourceIdentity=identity,
        modelDescriptorSha256=fingerprint(candidate_descriptor(name)), model=native.identity, modelLoadMs=native.load_ms,
        environment=stack, evaluation=evaluation, nativeCalls=audit,
        summary=recompute(dataset, archive_raw, license_raw, name, native.identity, evaluation, audit),
        resourceSamples=samples, resourceSummary=resources(samples), mlxMemorySamples=memory, limitations=LIMITATIONS))
    return verify_report(dataset, archive_raw, license_raw, report)


def verify_report(dataset, archive_raw, license_raw, report):
    corpus.evaluation_dataset(dataset, archive_raw, license_raw); verify_seal(report, SCHEMA)
    fields(report, {'schemaVersion', 'sha256', 'status', 'usage', 'referenceScope', 'candidate', 'corpusSha256', 'sourceIdentity',
        'modelDescriptorSha256', 'model', 'modelLoadMs', 'environment', 'evaluation', 'nativeCalls', 'summary',
        'resourceSamples', 'resourceSummary', 'mlxMemorySamples', 'limitations'} | set(AUTHORITY))
    name = report['candidate']; candidate_descriptor(name); cpu._source_identity(report['sourceIdentity']); model_identity(name, report['model'])
    require(report['status'] == 'external_development_diagnostic' and report['usage'] == 'external-development-only'
        and report['referenceScope'] == dataset['referenceScope'] and report['corpusSha256'] == dataset['sha256']
        and report['modelDescriptorSha256'] == fingerprint(candidate_descriptor(name))
        and all(type(report[k]) is type(v) and report[k] == v for k, v in AUTHORITY.items())
        and report['limitations'] == LIMITATIONS, 'Wrong external benchmark identity or invented authority')
    require(type(report['modelLoadMs']) in (int, float) and math.isfinite(report['modelLoadMs']) and 0 <= report['modelLoadMs'] <= 1e9
        and type(report['evaluation']['timing']['modelLoadMs']) is type(report['modelLoadMs'])
        and report['evaluation']['timing']['modelLoadMs'] == report['modelLoadMs'], 'Invalid actual model-load interval')
    require(canonical_json(report['summary']) == canonical_json(recompute(dataset, archive_raw, license_raw, name,
        report['model'], report['evaluation'], report['nativeCalls'])), 'Cached external benchmark metrics differ')
    require(canonical_json(report['resourceSummary']) == canonical_json(resources(report['resourceSamples'])), 'Raw resources or cached CPU/RSS differ')
    if name in NAMES[:2]: require(report['environment'] is None and report['mlxMemorySamples'] == [], 'Invented MLX observation on CPU baseline')
    else:
        mlx._environment(report['environment']); samples = report['mlxMemorySamples']
        require(isinstance(samples, list) and len(samples) == 2, 'Incomplete MLX allocator phases')
        for sample, phase in zip(samples, PHASES[1:], strict=True):
            fields(sample, {'phase', 'activeBytes', 'cacheBytes', 'peakBytes'})
            require(sample['phase'] == phase and all(type(sample[k]) is int and 0 <= sample[k] < 2**63 for k in ('activeBytes', 'cacheBytes', 'peakBytes'))
                and sample['peakBytes'] >= sample['activeBytes'], 'Invalid MLX allocator observation')
        require(samples[1]['peakBytes'] >= samples[0]['peakBytes'], 'MLX peak moved backwards')
    return report
