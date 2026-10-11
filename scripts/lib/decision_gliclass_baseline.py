"""Opt-in local GLiClass development baseline; serving profiles stay frozen."""
from __future__ import annotations

import hashlib
import importlib.metadata
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import io
import time
import urllib.request

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.calibration_workflow import scoring_rows
from decision_runtime.contracts import DecisionError, Policy, Request, canonical_json, fields, fingerprint, parse_json
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.evaluation import evaluate, metrics, percentile, replay_scores, validate_dataset
from scripts.lib.decision_public_sources import write_json_new
from scripts.lib.decision_shadow_pilot import require

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / 'docs/qualification/local-decisions/baselines/gliclass-model.json'
MODEL_SCHEMA = 'agat.decision.gliclass-model.v1'
ARTIFACT_SCHEMA = 'agat.decision.gliclass-local-artifact.v1'
REPORT_SCHEMA = 'agat.decision.gliclass-development.v1'
MODEL_FILES = {'config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json'}
PACKAGE_VERSIONS = {'gliclass': '0.1.20', 'torch': '2.14.1', 'transformers': '5.19.0',
                    'numpy': '2.5.4', 'tokenizers': '0.23.3', 'safetensors': '0.8.0'}
PROFILE = {'promptVersion': 'agat.gliclass.full-request.v1', 'device': 'cpu', 'dtype': 'float32',
           'attentionImplementation': 'eager', 'batchSize': 1, 'maxInputTokens': 1024,
           'intraOpThreads': 2, 'interOpThreads': 1, 'truncation': False, 'generatedTokens': 0,
           'addPrefixSpace': True, 'classification': 'single-label-softmax', 'temperature': 1.0}
AUTHORITY = {'routingEnabled': False, 'qualifiedForRouting': False, 'humanLabelsVerified': False,
             'classificationAccuracyQualified': False, 'statisticalIndependenceVerified': False,
             'calibrationFrozen': False, 'holdoutEvaluated': False, 'qualification': 'not_assessed'}
CLASS_TOKEN, TEXT_TOKEN = 50368, 50369
CONTROL_MARKERS = ('<<LABEL>>', '<<SEP>>', '<<EXAMPLE>>')
SOURCE_PATHS = ('decision_runtime', 'scripts/lib', 'scripts/evaluate-gliclass-development.py',
                'scripts/test/test_decision_gliclass_baseline.py',
                'docs/qualification/local-decisions/baselines/gliclass-model.json',
                'docs/qualification/local-decisions/baselines/requirements-gliclass-macos-arm64.txt')


def frozen_sources(root):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=10).strip()
    raw = subprocess.check_output(['git', 'archive', commit, '--', *SOURCE_PATHS], cwd=root, timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}
    require(all(path in sources or any(name.startswith(path + '/') for name in sources) for path in SOURCE_PATHS)
            and all((root / name).read_bytes() == content for name, content in sources.items()),
            'Commit the complete measured implementation before running')
    require(not subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '--', *SOURCE_PATHS],
                                       cwd=root, timeout=10), 'Untracked measured implementation')
    return {'sourceCommit': commit, 'sourceFiles': {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}}


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def catalog():
    value = parse_json(CATALOG_PATH.read_bytes())
    fields(value, {'schemaVersion', 'repository', 'revision', 'licenseDeclaredByModelCard', 'files'})
    require(value['schemaVersion'] == MODEL_SCHEMA and value['repository'] == 'knowledgator/gliclass-edge-v3.0'
            and value['revision'] == 'df03993a2ed98e5e4a0d2dd7efbbd105abe874cf', 'Unsupported GLiClass revision')
    fields(value['files'], MODEL_FILES)
    for item in value['files'].values():
        fields(item, {'bytes', 'sha256'})
        require(type(item['bytes']) is int and 0 < item['bytes'] <= 160 * 1024 * 1024
                and isinstance(item['sha256'], str) and len(item['sha256']) == 64
                and all(c in '0123456789abcdef' for c in item['sha256']), 'Invalid pinned model file')
    return value


def verify_local(directory, descriptor=None):
    descriptor = catalog() if descriptor is None else descriptor
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), 'Use a regular local model directory')
    require({path.name for path in directory.iterdir()} == MODEL_FILES | {'artifact.json'}, 'Local model file inventory differs')
    for name, expected in descriptor['files'].items():
        path = directory / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == expected['bytes']
                and file_sha(path) == expected['sha256'], 'Local model file differs: ' + name)
    manifest_path = directory / 'artifact.json'
    require(manifest_path.is_file() and not manifest_path.is_symlink() and manifest_path.stat().st_size <= 16 * 1024,
            'Invalid local artifact manifest')
    manifest = parse_json(manifest_path.read_bytes())
    expected = sealed({'schemaVersion': ARTIFACT_SCHEMA, 'modelDescriptorSha256': fingerprint(descriptor),
                       'repository': descriptor['repository'], 'revision': descriptor['revision'], 'files': descriptor['files']})
    require(canonical_json(manifest) == canonical_json(expected), 'Local manifest differs from committed model descriptor')
    return expected


def download(directory):
    """Only an explicit download command accesses the public model repository."""
    descriptor = catalog()
    directory = Path(directory)
    require(not directory.exists() and not directory.is_symlink(), 'Use a new model directory')
    directory.mkdir(parents=True, mode=0o700)
    for name, expected in sorted(descriptor['files'].items()):
        url = f"https://huggingface.co/{descriptor['repository']}/resolve/{descriptor['revision']}/{name}"
        digest, count = hashlib.sha256(), 0
        descriptor_fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor_fd, 'wb') as stream, urllib.request.urlopen(url, timeout=60) as response:
            while block := response.read(min(1024 * 1024, expected['bytes'] - count + 1)):
                count += len(block)
                require(count <= expected['bytes'], 'Downloaded file exceeds pinned size')
                stream.write(block); digest.update(block)
        require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'], 'Downloaded model checksum differs')
    write_json_new(directory / 'artifact.json', sealed({'schemaVersion': ARTIFACT_SCHEMA,
        'modelDescriptorSha256': fingerprint(descriptor), 'repository': descriptor['repository'],
        'revision': descriptor['revision'], 'files': descriptor['files']}))
    return verify_local(directory)


def development_dataset(dataset):
    validate_dataset(dataset)
    require(all(case['split'] == 'development' and case['labelSource'] in {'synthetic-authored', 'assistant-reviewed'}
                for case in dataset['cases']), 'Use the complete synthetic/assistant development dataset; no calibration or holdout')
    require(all(dataset.get(key, False) is False for key in ('routingEnabled', 'qualifiedForRouting', 'humanLabelsVerified')),
            'Development input cannot assert routing or independent human authority')
    return dataset


def render_request(request):
    """Follow the pinned uni-encoder training order, preserving typed candidate meaning."""
    parts = [request.state, request.question, *[option.description for option in request.options]]
    if any(marker in part for part in parts for marker in CONTROL_MARKERS):
        raise DecisionError('unsupported_control_marker', 'Request contains GLiClass control syntax')
    labels = []
    for option in request.options:
        label = option.description
        if request.kind != 'choice':
            label += '\nvalue=' + canonical_json(option.value)
        if option.abstain:
            label += '\nabstain=true'
        labels.append('<<LABEL>>' + label)
    return ''.join(labels) + '<<SEP>>' + request.question + '\nkind=' + request.kind + '\n' + request.state


def validate_encoding(value, option_count):
    fields(value, {'input_ids', 'attention_mask'})
    ids, mask = value['input_ids'], value['attention_mask']
    require(isinstance(ids, list) and ids and all(type(i) is int and 0 <= i < 50370 for i in ids)
            and isinstance(mask, list) and len(mask) == len(ids) and all(type(i) is int and i == 1 for i in mask),
            'Invalid complete single-request tokenizer output')
    if len(ids) > PROFILE['maxInputTokens']:
        raise DecisionError('context_bound_exceeded', 'Full input exceeds the prospective context limit')
    if ids.count(CLASS_TOKEN) != option_count or ids.count(TEXT_TOKEN) != 1:
        raise DecisionError('invalid_candidate_tokens', 'Tokenizer changed candidate control tokens')
    if max(i for i, value in enumerate(ids) if value == CLASS_TOKEN) >= ids.index(TEXT_TOKEN):
        raise DecisionError('invalid_candidate_tokens', 'Candidate markers are outside the label section')
    return value


def package_identity():
    versions = {name: importlib.metadata.version(name) for name in PACKAGE_VERSIONS}
    require(versions == PACKAGE_VERSIONS, 'Use the pinned isolated GLiClass dependency versions')
    dist = importlib.metadata.distribution('gliclass')
    source_files = {str(path): file_sha(Path(dist.locate_file(path))) for path in dist.files
                    if str(path).startswith('gliclass/') and str(path).endswith('.py')}
    require(source_files, 'Missing GLiClass implementation files')
    return {'versions': versions, 'gliclassSourceFiles': source_files,
            'pythonVersion': platform.python_version(), 'machine': platform.machine(), 'platform': platform.platform()}


class GLiClassBackend:
    def __init__(self, directory):
        self.directory = Path(directory); manifest = verify_local(self.directory)
        stack = package_identity()
        # Construction and scoring are offline. No from_pretrained encoder/network fallback is permitted.
        os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'; os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        start = time.perf_counter()
        import torch
        from gliclass import GLiClassModel
        from gliclass.config import GLiClassModelConfig
        from transformers import AutoTokenizer
        torch.set_num_threads(PROFILE['intraOpThreads']); torch.set_num_interop_threads(PROFILE['interOpThreads'])
        torch.manual_seed(0)
        config_raw = parse_json((self.directory / 'config.json').read_bytes())
        require(config_raw['architecture_type'] == 'uni-encoder' and config_raw['prompt_first'] is True
                and config_raw['encoder_config']['model_type'] == 'modernbert'
                and config_raw['class_token_index'] == CLASS_TOKEN and config_raw['text_token_index'] == TEXT_TOKEN
                and not config_raw.get('auto_map') and not config_raw.get('quantization_config'), 'Unsupported local model architecture')
        config = GLiClassModelConfig(**config_raw)
        config.encoder_config._attn_implementation = 'eager'
        config.encoder_config.reference_compile = False
        config._attn_implementation = 'eager'
        self.model, info = GLiClassModel.from_pretrained(str(self.directory), config=config,
            local_files_only=True, use_safetensors=True, trust_remote_code=False,
            dtype=torch.float32, output_loading_info=True)
        require(not any(info.get(key) for key in ('missing_keys', 'unexpected_keys', 'mismatched_keys', 'error_msgs')),
                'Model loading did not exactly cover the pinned weights')
        self.model.to('cpu'); self.model.eval()
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.directory), local_files_only=True,
            trust_remote_code=False, add_prefix_space=PROFILE['addPrefixSpace'])
        self.torch, self.audit = torch, []
        self.load_ms = round((time.perf_counter() - start) * 1000, 3)
        self.identity = {'repository': manifest['repository'], 'revision': manifest['revision'],
            'artifactSha256': fingerprint(manifest['files']), 'tokenizerSha256': manifest['files']['tokenizer.json']['sha256'],
            'backend': 'gliclass-uni-encoder-native-logits', 'profile': dict(PROFILE), 'stack': stack}
        require(verify_local(self.directory) == manifest, 'Local model changed while loading')

    def score(self, request):
        row = {'ordinal': len(self.audit) + 1, 'id': request.id, 'inputSha256': request.input_sha256,
               'renderedInputSha256': None, 'tokenIds': [], 'inferenceAttempted': False, 'inferenceMs': None,
               'rejection': None}
        self.audit.append(row)
        try:
            rendered = render_request(request)
            row['renderedInputSha256'] = hashlib.sha256(rendered.encode()).hexdigest()
            encoded = self.tokenizer(rendered, truncation=False, padding=False,
                return_attention_mask=True, return_token_type_ids=False)
            row['tokenIds'] = list(encoded['input_ids'])
            validate_encoding(encoded, len(request.options))
            tensors = {key: self.torch.tensor([values], dtype=self.torch.long, device='cpu') for key, values in encoded.items()}
            start = time.perf_counter(); row['inferenceAttempted'] = True
            try:
                with self.torch.inference_mode():
                    output = self.model(**tensors, max_num_classes=len(request.options), return_dict=True)
                    require(tuple(output.logits.shape) == (1, len(request.options)), 'Native logits shape differs from candidates')
                    logits = output.logits[0].detach().to(device='cpu', dtype=self.torch.float32).tolist()
            finally:
                row['inferenceMs'] = round((time.perf_counter() - start) * 1000, 3)
            require(all(type(z) in (int, float) and math.isfinite(z) for z in logits), 'Native logits contain non-finite values')
            return Scores(logits, len(encoded['input_ids']))
        except DecisionError as error:
            row['rejection'] = error.code
            raise
        except Exception:
            row['rejection'] = 'backend_error'
            raise


def recompute(dataset, evaluation, audit):
    development_dataset(dataset)
    require(evaluation.get('policy') == Policy().to_dict(), 'Development baseline policy was changed')
    scores = scoring_rows(evaluation, dataset, 'development', require_reverse=True)
    require([row['id'] for _, row in scores] == [case['id'] for case in dataset['cases']], 'Original case order changed')
    require(isinstance(audit, list) and len(audit) == 2 * len(scores), 'Incomplete native call inventory')
    rows = {'original': [], 'reversed': []}; changed = []
    for case_index, (case, raw) in enumerate(scores):
        for order_index, (order, key) in enumerate((('original', 'result'), ('reversed', 'reversedResult'))):
            request_raw = case['request'] if order == 'original' else {**case['request'], 'options': list(reversed(case['request']['options']))}
            request = Request.from_dict(request_raw); result = raw[key]; call = audit[case_index * 2 + order_index]
            fields(call, {'ordinal', 'id', 'inputSha256', 'renderedInputSha256', 'tokenIds', 'inferenceAttempted', 'inferenceMs', 'rejection'})
            require(type(call['ordinal']) is int and call['ordinal'] == case_index * 2 + order_index + 1
                    and call['id'] == case['id'] and call['inputSha256'] == request.input_sha256
                    and type(call['inferenceAttempted']) is bool, 'Native call binding differs')
            render_error = None
            try:
                rendered_sha = hashlib.sha256(render_request(request).encode()).hexdigest()
            except DecisionError as error:
                rendered_sha, render_error = None, error.code
            require(call['renderedInputSha256'] == rendered_sha, 'Full rendered request differs')
            ids = call['tokenIds']
            require(isinstance(ids, list) and len(ids) <= 200_000
                    and all(type(token) is int and 0 <= token < 50370 for token in ids), 'Invalid recorded token inventory')
            if result['status'] == 'error':
                require(call['rejection'] == result['reason'] and result['reason'] in {
                    'context_bound_exceeded', 'invalid_candidate_tokens', 'unsupported_control_marker', 'backend_error'},
                        'Unaccounted native refusal')
                if result['reason'] == 'context_bound_exceeded':
                    require(len(ids) > PROFILE['maxInputTokens'] and not call['inferenceAttempted'] and call['inferenceMs'] is None,
                            'Over-context input was inferred or clipped')
                elif result['reason'] == 'unsupported_control_marker':
                    require(render_error == result['reason'] and ids == [] and not call['inferenceAttempted'] and call['inferenceMs'] is None,
                            'Unsupported source control syntax reached tokenization/inference')
                elif result['reason'] == 'invalid_candidate_tokens':
                    require(not call['inferenceAttempted'] and call['inferenceMs'] is None, 'Invalid candidate input was inferred')
                    try:
                        validate_encoding({'input_ids': ids, 'attention_mask': [1] * len(ids)}, len(request.options))
                    except DecisionError as error:
                        require(error.code == result['reason'], 'Recorded candidate rejection differs')
                    else:
                        raise ValueError('Recorded candidate input should not have been rejected')
            else:
                validate_encoding({'input_ids': ids, 'attention_mask': [1] * len(ids)}, len(request.options))
                require(call['inferenceAttempted'] and call['rejection'] is None and result['inputTokens'] == len(ids), 'Native scoring inventory differs')
            require(call['inferenceMs'] is None or type(call['inferenceMs']) in (int, float)
                    and math.isfinite(call['inferenceMs']) and 0 <= call['inferenceMs'] <= 1e12, 'Invalid native timing')
            require(call['inferenceAttempted'] == (call['inferenceMs'] is not None), 'Native inference timing is missing or fabricated')
            rebuilt = replay_scores(case, result, evaluation['model'], Policy(), None, reverse=order == 'reversed')
            require(canonical_json(rebuilt['result']) == canonical_json(result), 'Typed result differs from recorded logits')
            rows[order].append(rebuilt)
        original, reverse = rows['original'][-1], rows['reversed'][-1]
        order_agreement = (original['result']['selectedOptionId'] == reverse['result']['selectedOptionId']
                           if all(row['result']['status'] != 'error' for row in (original, reverse)) else None)
        require(type(raw['correct']) is bool and raw['correct'] == original['correct']
                and raw['orderAgreement'] is order_agreement, 'Cached case agreement differs')
        if rows['original'][-1]['result']['selectedOptionId'] != rows['reversed'][-1]['result']['selectedOptionId']:
            changed.append(case['id'])
    require(canonical_json(evaluation['metrics']) == canonical_json(metrics(rows['original']))
            and canonical_json(evaluation['families']) == canonical_json({family: metrics([r for r in rows['original'] if r['family'] == family])
                for family in sorted({c['family'] for c in dataset['cases']})}), 'Cached evaluation metrics differ')
    paired = [raw for _, raw in scores if raw['orderAgreement'] is not None]
    expected_order = {'attemptedPairs': len(scores), 'scoredPairs': len(paired),
        'agreement': sum(raw['orderAgreement'] for raw in paired) / len(paired) if paired else None,
        'reversedBackendErrors': sum(row['result']['status'] == 'error' for row in rows['reversed']),
        'reversedAccuracyAllAttempts': sum(row['correct'] for row in rows['reversed']) / len(scores)}
    require(canonical_json(evaluation['optionOrder']) == canonical_json(expected_order), 'Cached order metrics differ')
    return {'caseCount': len(scores), 'dependencyGroupCount': len({c['groupId'] for c in dataset['cases']}),
            'nativeInferenceAttempts': sum(call['inferenceAttempted'] for call in audit),
            'original': metrics(rows['original']), 'reversed': metrics(rows['reversed']), 'changedCaseIds': changed,
            'families': {family: {order: metrics([r for r in values if r['family'] == family]) for order, values in rows.items()}
                         for family in sorted({c['family'] for c in dataset['cases']})},
            'nativeInferenceP50Ms': percentile([c['inferenceMs'] for c in audit if c['inferenceMs'] is not None], .5),
            'nativeInferenceP95Ms': percentile([c['inferenceMs'] for c in audit if c['inferenceMs'] is not None], .95)}


def measure(dataset, backend, source_identity):
    development_dataset(dataset)
    evaluation = evaluate(DecisionEngine(backend, Policy()), dataset, 'development', reverse_options=True)
    summary = recompute(dataset, evaluation, backend.audit)
    return sealed({'schemaVersion': REPORT_SCHEMA, 'status': 'diagnostic_only', 'usage': 'development-only', **AUTHORITY,
        'datasetSha256': fingerprint(dataset), 'sourceIdentity': source_identity, 'modelDescriptorSha256': fingerprint(catalog()),
        'evaluation': evaluation, 'nativeCalls': backend.audit, 'summary': summary,
        'limitations': ['Development labels are synthetic/assistant labels, not independently reviewed qualification labels.',
            'Original/reversed requests are dependent variants; groups are the unit of independence.',
            'CPU FP32 latency is not a controlled comparison with older MLX/GPU or quantized generative runs.',
            'No thresholds, temperature, labels, splits or weights are fitted or promoted by this baseline.']})


def verify_report(dataset, report):
    verify_seal(report, REPORT_SCHEMA)
    fields(report, {'schemaVersion', 'sha256', 'status', 'usage', 'datasetSha256', 'sourceIdentity', 'modelDescriptorSha256',
                    'evaluation', 'nativeCalls', 'summary', 'limitations'} | set(AUTHORITY))
    identity = report['sourceIdentity']; fields(identity, {'sourceCommit', 'sourceFiles'})
    require(isinstance(identity['sourceCommit'], str) and re.fullmatch(r'[0-9a-f]{40}', identity['sourceCommit'])
            and isinstance(identity['sourceFiles'], dict) and identity['sourceFiles']
            and all(isinstance(name, str) and not Path(name).is_absolute() and '..' not in Path(name).parts
                    and isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{64}', sha) for name, sha in identity['sourceFiles'].items()),
            'Invalid measured source identity')
    require(report.get('usage') == 'development-only' and report.get('status') == 'diagnostic_only'
            and all(type(report.get(key)) is type(value) and report[key] == value for key, value in AUTHORITY.items()),
            'Development baseline authority was changed')
    require(report['datasetSha256'] == fingerprint(development_dataset(dataset))
            and report['modelDescriptorSha256'] == fingerprint(catalog()), 'Dataset or model descriptor differs')
    model = report['evaluation']['model']; descriptor = catalog()
    require(model['repository'] == descriptor['repository'] and model['revision'] == descriptor['revision']
            and model['backend'] == 'gliclass-uni-encoder-native-logits'
            and model['artifactSha256'] == fingerprint(descriptor['files'])
            and model['tokenizerSha256'] == descriptor['files']['tokenizer.json']['sha256']
            and canonical_json(model['profile']) == canonical_json(PROFILE)
            and model['stack']['versions'] == PACKAGE_VERSIONS, 'Unpinned native baseline identity')
    require(canonical_json(report['summary']) == canonical_json(recompute(dataset, report['evaluation'], report['nativeCalls'])),
            'Saved baseline summary differs from whole-inventory recomputation')
    return report
