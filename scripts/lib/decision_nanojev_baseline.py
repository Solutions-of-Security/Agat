"""Pinned CPU reference for NanoJev decision heads, isolated from serving profiles."""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import io
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import tarfile
import time
import urllib.request

from decision_runtime.artifacts import sealed, verify_seal
from decision_runtime.calibration_workflow import scoring_rows
from decision_runtime.contracts import DecisionError, Policy, Request, canonical_json, fields, fingerprint, parse_json
from decision_runtime.engine import DecisionEngine, Scores
from decision_runtime.evaluation import evaluate, metrics, percentile, replay_scores
from scripts.lib.decision_gliclass_baseline import AUTHORITY, development_dataset, file_sha
from scripts.lib.decision_gliclass_resources import PHASES, snapshot, summarize
from scripts.lib.decision_public_sources import write_json_new
from scripts.lib.decision_shadow_pilot import require

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / 'docs/qualification/local-decisions/baselines/nanojev-model.json'
MODEL_SCHEMA = 'agat.decision.nanojev-model.v1'
ARTIFACT_SCHEMA = 'agat.decision.nanojev-local-artifact.v1'
REPORT_SCHEMA = 'agat.decision.nanojev-development.v1'
MODEL_FILES = {'best.safetensors', 'config.json', 'backbone_config/config.json', 'tokenizer/tokenizer.json',
    'tokenizer/tokenizer_config.json', 'tokenizer/chat_template.jinja', 'source/scripts/predict_toy_decisions.py',
    'source/scripts/train_toy_decisions.py', 'source/LICENSE', 'source/requirements-toy.txt', 'README.md', 'SHA256_MANIFEST.json'}
PACKAGE_VERSIONS = {'torch': '2.14.1', 'transformers': '5.19.0', 'safetensors': '0.8.0',
                    'numpy': '2.5.4', 'tokenizers': '0.23.3'}
PROFILE = {'promptVersion': 'agat.nanojev.full-request.v1', 'device': 'cpu', 'dtype': 'float32',
    'attentionImplementation': 'eager', 'batchQuestions': 1, 'maxTokensPerCandidatePath': 1024,
    'intraOpThreads': 2, 'interOpThreads': 1, 'truncation': False, 'generatedTokens': 0,
    'prefixSharing': False, 'useCache': False, 'disableNativeTriton': True, 'temperature': 1.0,
    'booleanMapping': 'native-logits-0-z-by-typed-false-true',
    'scoreMapping': 'ascending-typed-values-with-explicit-values-in-question'}
SOURCE_PATHS = ('decision_runtime', 'scripts/lib', 'scripts/evaluate-nanojev-development.py',
    'scripts/test/test_decision_nanojev_baseline.py',
    'scripts/test/test_decision_gliclass_baseline.py',
    'docs/qualification/local-decisions/baselines/nanojev-model.json',
    'docs/qualification/local-decisions/baselines/requirements-nanojev-macos-arm64.txt')
CONTROL_MARKERS = ('<|endoftext|>', '<|im_start|>', '<|im_end|>', '<|object_ref_start|>', '<|object_ref_end|>',
    '<|box_start|>', '<|box_end|>', '<|quad_start|>', '<|quad_end|>', '<|vision_start|>', '<|vision_end|>',
    '<|vision_pad|>', '<|image_pad|>', '<|video_pad|>')
EOS_TOKEN, PAD_TOKEN, VOCAB_SIZE = 151645, 151643, 151936


def frozen_sources(root):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=10).strip()
    raw = subprocess.check_output(['git', 'archive', commit, '--', *SOURCE_PATHS], cwd=root, timeout=30)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        sources = {entry.name: archive.extractfile(entry).read() for entry in archive if entry.isfile()}
    require(all(path in sources or any(name.startswith(path + '/') for name in sources) for path in SOURCE_PATHS)
        and all((root / name).read_bytes() == value for name, value in sources.items()), 'Commit the complete NanoJev implementation before running')
    require(not subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '--', *SOURCE_PATHS],
        cwd=root, timeout=10), 'Untracked NanoJev implementation')
    return {'sourceCommit': commit, 'sourceFiles': {name: hashlib.sha256(value).hexdigest() for name, value in sources.items()}}


def catalog():
    value = parse_json(CATALOG_PATH.read_bytes())
    fields(value, {'schemaVersion', 'repository', 'revision', 'releaseTag', 'sourceLicense', 'baseRepository', 'baseRevision', 'files'})
    require(value['schemaVersion'] == MODEL_SCHEMA and value['repository'] == 'C-Tianyu/NanoJev'
        and value['revision'] == '047b927b30882a1138fc504821b82ac145a4b81a'
        and value['releaseTag'] == 'unified-games-v1' and value['sourceLicense'] == 'MIT'
        and value['baseRepository'] == 'Qwen/Qwen3-0.6B'
        and value['baseRevision'] == 'c1899de289a04d12100db370d81485cdf75e47ca', 'Unsupported NanoJev checkpoint')
    fields(value['files'], MODEL_FILES)
    for name, item in value['files'].items():
        fields(item, {'bytes', 'sha256'})
        require(type(item['bytes']) is int and 0 < item['bytes'] <= (3 * 1024**3 if name == 'best.safetensors' else 16 * 1024**2)
            and isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']), 'Invalid pinned NanoJev file')
    return value


def artifact(descriptor):
    return sealed({'schemaVersion': ARTIFACT_SCHEMA, 'modelDescriptorSha256': fingerprint(descriptor),
        'repository': descriptor['repository'], 'revision': descriptor['revision'], 'files': descriptor['files']})


def verify_local(directory, descriptor=None):
    descriptor = catalog() if descriptor is None else descriptor
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), 'Use a regular local NanoJev directory')
    paths = list(directory.rglob('*'))
    require(not any(path.is_symlink() or not (path.is_dir() or path.is_file()) for path in paths), 'Non-regular NanoJev artifact entry')
    expected_dirs = {parent.as_posix() for name in MODEL_FILES for parent in Path(name).parents if parent != Path('.')}
    require({path.relative_to(directory).as_posix() for path in paths if path.is_dir()} == expected_dirs
        and {path.relative_to(directory).as_posix() for path in paths if path.is_file()} == MODEL_FILES | {'artifact.json'},
        'Local NanoJev inventory differs')
    for name, expected in descriptor['files'].items():
        path = directory / name
        require(path.stat().st_size == expected['bytes'] and file_sha(path) == expected['sha256'], 'Pinned NanoJev file differs: ' + name)
    manifest = directory / 'artifact.json'
    require(manifest.stat().st_size <= 32 * 1024 and canonical_json(parse_json(manifest.read_bytes())) == canonical_json(artifact(descriptor)),
        'Local NanoJev manifest differs from committed descriptor')
    return artifact(descriptor)


def download(directory):
    descriptor = catalog()
    directory = Path(directory)
    require(not directory.exists() and not directory.is_symlink(), 'Use a new NanoJev model directory')
    directory.mkdir(parents=True, mode=0o700)
    for name, expected in sorted(descriptor['files'].items()):
        target = directory / name
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        digest, count = hashlib.sha256(), 0
        url = f"https://huggingface.co/{descriptor['repository']}/resolve/{descriptor['revision']}/{name}"
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream, \
             urllib.request.urlopen(url, timeout=60) as response:
            while block := response.read(min(1024 * 1024, expected['bytes'] - count + 1)):
                count += len(block)
                require(count <= expected['bytes'], 'Downloaded NanoJev file exceeds pinned size')
                stream.write(block); digest.update(block)
        require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'], 'Downloaded NanoJev checksum differs')
    write_json_new(directory / 'artifact.json', artifact(descriptor))
    return verify_local(directory)


def weights_header(path):
    """Validate the complete FP32 tensor layout before importing or loading model code."""
    with Path(path).open('rb') as stream:
        prefix = stream.read(8)
        require(len(prefix) == 8, 'Missing safetensors header length')
        length = struct.unpack('<Q', prefix)[0]
        require(2 <= length <= 16 * 1024**2, 'Invalid safetensors header bound')
        raw = stream.read(length)
    require(len(raw) == length, 'Incomplete safetensors header')
    header = parse_json(raw)
    require(isinstance(header, dict) and header, 'Missing safetensors tensor inventory')
    entries = []
    for name, item in header.items():
        if name == '__metadata__':
            require(isinstance(item, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in item.items()), 'Invalid tensor metadata')
            continue
        fields(item, {'dtype', 'shape', 'data_offsets'})
        require(isinstance(name, str) and name and item['dtype'] == 'F32' and isinstance(item['shape'], list)
            and len(item['shape']) <= 8 and all(type(n) is int and 0 < n <= 2**31 for n in item['shape'])
            and isinstance(item['data_offsets'], list) and len(item['data_offsets']) == 2
            and all(type(n) is int and 0 <= n <= 3 * 1024**3 for n in item['data_offsets']), 'Invalid FP32 tensor descriptor')
        start, end = item['data_offsets']
        require(end - start == math.prod(item['shape']) * 4, 'FP32 tensor size differs from shape')
        entries.append((start, end, name))
    offset = 0
    for start, end, _ in sorted(entries):
        require(start == offset and end > start, 'Overlapping or incomplete tensor layout')
        offset = end
    require(entries and offset + length + 8 == Path(path).stat().st_size, 'Tensor bytes differ from complete checkpoint')
    return {'tensorCount': len(entries), 'tensorNamesSha256': fingerprint(sorted(name for _, _, name in entries)),
            'headerSha256': hashlib.sha256(raw).hexdigest(), 'dataBytes': offset, 'parameterStorage': 'float32'}


def render_payload(request):
    texts = (request.state, request.question, *[option.description for option in request.options])
    if any(marker in text for text in texts for marker in CONTROL_MARKERS):
        raise DecisionError('unsupported_control_marker', 'Native tokenizer control syntax in source')
    question = {'type': request.kind, 'instructions': request.question}
    if request.kind == 'choice':
        ordered = list(request.options)
        question['criteria'] = {option.id: option.description + ('\nabstain=true' if option.abstain else '') for option in ordered}
    elif request.kind == 'boolean':
        ordered = sorted(request.options, key=lambda option: option.value)
        question['criteria'] = {str(option.value).lower(): option.description for option in ordered}
    else:
        ordered = sorted(request.options, key=lambda option: option.value)
        question['instructions'] += '\nScore values in ascending semantic order: ' + canonical_json([option.value for option in ordered])
        question['criteria'] = [option.description for option in ordered]
    return {'states': [{'id': request.id, 'state': request.state, 'questions': {'decision': question}}]}, [option.id for option in ordered]


def rendered_paths(payload):
    row = payload['states'][0]
    question = row['questions']['decision']
    kind = question['type']
    prefix = [f"State:\n{row['state']}\n", f"Question type: {kind}\nQuestion:\n{question['instructions']}\n"]
    if kind == 'boolean':
        for key, label in (('false', 'False'), ('true', 'True')):
            prefix[1] += f"{label} criterion: {question['criteria'][key]}\n"
        native_ids, labels = ['false', 'true'], ['The proposition is true.']
    elif kind == 'choice':
        native_ids = list(question['criteria'])
        labels = [key + ': ' + question['criteria'][key] for key in native_ids]
    else:
        native_ids = [str(index) for index in range(len(question['criteria']))]
        labels = question['criteria']
    return native_ids, [prefix + [f'Candidate:\n{label}\nDecision:'] for label in labels]


def validate_paths(paths, kind, option_count):
    require(isinstance(paths, list) and len(paths) == (1 if kind == 'boolean' else option_count), 'Wrong native candidate path count')
    require(all(isinstance(path, list) and 0 < len(path) <= 200_000
        and all(type(token) is int and 0 <= token < VOCAB_SIZE for token in path) for path in paths), 'Invalid full native token paths')
    if any(len(path) > PROFILE['maxTokensPerCandidatePath'] for path in paths):
        raise DecisionError('context_bound_exceeded', 'Complete candidate path exceeds prospective context limit')
    if any(path[-1] != EOS_TOKEN or any(token >= PAD_TOKEN for token in path[:-1]) for path in paths):
        raise DecisionError('invalid_candidate_tokens', 'Native control tokens changed or occur in source text')
    return paths


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, 'Pinned native source cannot be imported')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NanoJevBackend:
    def __init__(self, directory):
        directory = Path(directory)
        manifest = verify_local(directory)
        layout = weights_header(directory / 'best.safetensors')
        versions = {name: importlib.metadata.version(name) for name in PACKAGE_VERSIONS}
        require(versions == PACKAGE_VERSIONS, 'Use the pinned isolated NanoJev diagnostic dependency versions')
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
        import torch
        from safetensors.torch import load_file
        from transformers import AutoConfig, AutoModel, AutoTokenizer
        from torch._native import triton_utils
        triton_utils.deregister_op_overrides()
        torch.set_num_threads(PROFILE['intraOpThreads'])
        torch.set_num_interop_threads(PROFILE['interOpThreads'])
        torch.manual_seed(0)
        run_config = parse_json((directory / 'config.json').read_bytes())
        body_raw = parse_json((directory / 'backbone_config/config.json').read_bytes())
        require(run_config['schema_version'] == 'nanojev-unified-games-v1' and run_config['set_head'] == 'attention'
            and run_config['model'] == 'Qwen/Qwen3-0.6B' and run_config['resolved_model_revision'] == catalog()['baseRevision']
            and type(run_config['max_length']) is int and run_config['max_length'] >= PROFILE['maxTokensPerCandidatePath'], 'Pinned checkpoint contract differs')
        require(body_raw['model_type'] == 'qwen3' and body_raw['vocab_size'] == VOCAB_SIZE
            and body_raw['hidden_size'] == 1024 and body_raw['num_hidden_layers'] == 28
            and body_raw['max_position_embeddings'] >= PROFILE['maxTokensPerCandidatePath']
            and not any(key in body_raw for key in ('auto_map', 'quantization_config')), 'Unsupported native backbone config')
        start = time.perf_counter()
        tokenizer = AutoTokenizer.from_pretrained(str(directory / 'tokenizer'), local_files_only=True, trust_remote_code=False)
        require(tokenizer.eos_token_id == EOS_TOKEN and tokenizer.pad_token_id == PAD_TOKEN, 'Pinned native tokenizer special IDs differ')
        config = AutoConfig.from_pretrained(str(directory / 'backbone_config'), local_files_only=True, trust_remote_code=False)
        config.use_cache = False
        body = AutoModel.from_config(config, attn_implementation='eager', dtype=torch.float32, trust_remote_code=False).to(device='cpu', dtype=torch.float32)
        predictor = load_module(directory / 'source/scripts/predict_toy_decisions.py', 'agat_pinned_nanojev_predictor')
        trainer = load_module(directory / 'source/scripts/train_toy_decisions.py', 'agat_pinned_nanojev_trainer')
        model = trainer.DecisionModel(body, run_config['set_head'])
        weights = load_file(str(directory / 'best.safetensors'), device='cpu')
        loaded = model.load_state_dict(weights, strict=True)
        require(loaded.missing_keys == [] and loaded.unexpected_keys == [], 'Incomplete native state_dict')
        del weights
        model.to(device='cpu', dtype=torch.float32).eval()
        require(all(parameter.device.type == 'cpu' and parameter.dtype == torch.float32 for parameter in model.parameters()), 'Native model storage differs')
        self.load_ms = round((time.perf_counter() - start) * 1000, 3)
        self.torch, self.tokenizer, self.prepare, self.model, self.audit = torch, tokenizer, predictor.prepare_examples, model, []
        self.identity = {'repository': manifest['repository'], 'revision': manifest['revision'],
            'artifactSha256': fingerprint(manifest['files']), 'tokenizerSha256': manifest['files']['tokenizer/tokenizer.json']['sha256'],
            'backend': 'nanojev-native-decision-heads-cpu-reference', 'profile': dict(PROFILE),
            'stack': {'versions': versions, 'nativeSourceFiles': {name: manifest['files'][name]['sha256'] for name in
                ('source/scripts/predict_toy_decisions.py', 'source/scripts/train_toy_decisions.py')}},
            'weightsLayout': layout, 'strictStateDictLoaded': True}
        require(verify_local(directory) == manifest, 'Pinned native files changed while loading')

    def score(self, request):
        call = {'ordinal': len(self.audit) + 1, 'id': request.id, 'inputSha256': request.input_sha256,
            'payloadSha256': None, 'renderedPathsSha256': None, 'nativeCandidateIds': [], 'requestOptionIdsInNativeOrder': [],
            'leafTokenIds': [], 'nativeLogits': [], 'inferenceAttempted': False, 'inferenceMs': None, 'rejection': None}
        self.audit.append(call)
        try:
            payload, option_order = render_payload(request)
            native_ids, texts = rendered_paths(payload)
            call.update(payloadSha256=fingerprint(payload), renderedPathsSha256=fingerprint(texts),
                        nativeCandidateIds=native_ids, requestOptionIdsInNativeOrder=option_order)
            # Tokenize completely before the tighter prospective CPU context gate.
            examples = self.prepare(payload, self.tokenizer, 200_000)
            require(len(examples) == 1 and examples[0]['type'] == request.kind and examples[0]['candidate_ids'] == native_ids,
                'Pinned formatter changed candidate semantics')
            paths = examples[0]['leaf_tokens']
            call['leafTokenIds'] = paths
            expected = [[token for part in parts for token in self.tokenizer.encode(part, add_special_tokens=False)] + [EOS_TOKEN] for parts in texts]
            require(paths == expected, 'Pinned formatter differs from complete independently rendered paths')
            validate_paths(paths, request.kind, len(request.options))
            start = time.perf_counter(); call['inferenceAttempted'] = True
            try:
                with self.torch.inference_mode():
                    values, valid = self.model(examples, self.tokenizer.pad_token_id)
                    mask = valid.detach().to(device='cpu').tolist()
                    require(tuple(values.shape) == tuple(valid.shape) == (1, len(native_ids))
                        and isinstance(mask, list) and len(mask) == 1 and isinstance(mask[0], list)
                        and len(mask[0]) == len(native_ids) and all(value is True for value in mask[0]), 'Incomplete native logits or valid mask')
                    logits = values[0].detach().to(device='cpu', dtype=self.torch.float32).tolist()
            finally:
                call['inferenceMs'] = round((time.perf_counter() - start) * 1000, 3)
            require(all(type(z) in (int, float) and math.isfinite(z) for z in logits)
                and (request.kind != 'boolean' or logits[0] == 0), 'Invalid native decision-head logits')
            call['nativeLogits'] = logits
            mapped = dict(zip(option_order, logits, strict=True))
            return Scores([mapped[option.id] for option in request.options], sum(map(len, paths)))
        except DecisionError as error:
            call['rejection'] = error.code
            raise
        except Exception:
            call['rejection'] = 'backend_error'
            raise


def recompute(dataset, evaluation, audit):
    development_dataset(dataset)
    require(evaluation.get('policy') == Policy().to_dict(), 'Native baseline policy changed')
    scores = scoring_rows(evaluation, dataset, 'development', require_reverse=True)
    require([case['id'] for case, _ in scores] == [case['id'] for case in dataset['cases']]
        and isinstance(audit, list) and len(audit) == 2 * len(scores), 'Incomplete native case/call inventory')
    rows = {'original': [], 'reversed': []}
    changed = []
    for index, (case, raw) in enumerate(scores):
        for offset, (order, key) in enumerate((('original', 'result'), ('reversed', 'reversedResult'))):
            request = Request.from_dict(case['request'] if not offset else {**case['request'], 'options': list(reversed(case['request']['options']))})
            result, call = raw[key], audit[2 * index + offset]
            fields(call, {'ordinal', 'id', 'inputSha256', 'payloadSha256', 'renderedPathsSha256', 'nativeCandidateIds',
                'requestOptionIdsInNativeOrder', 'leafTokenIds', 'nativeLogits', 'inferenceAttempted', 'inferenceMs', 'rejection'})
            require(type(call['ordinal']) is int and call['ordinal'] == 2 * index + offset + 1 and call['id'] == case['id']
                and call['inputSha256'] == request.input_sha256 and type(call['inferenceAttempted']) is bool, 'Native request binding differs')
            render_error = None
            try:
                payload, option_order = render_payload(request)
                native_ids, texts = rendered_paths(payload)
                expected_render = (fingerprint(payload), fingerprint(texts), native_ids, option_order)
            except DecisionError as error:
                render_error = error.code
                expected_render = (None, None, [], [])
            require((call['payloadSha256'], call['renderedPathsSha256'], call['nativeCandidateIds'], call['requestOptionIdsInNativeOrder']) == expected_render,
                'Native full-input rendering or typed candidate mapping differs')
            paths = call['leafTokenIds']
            require(isinstance(paths, list) and len(paths) <= 10 and all(isinstance(path, list) and len(path) <= 200_000
                and all(type(token) is int and 0 <= token < VOCAB_SIZE for token in path) for path in paths), 'Invalid recorded token paths')
            if result['status'] == 'error':
                require(call['rejection'] == result['reason'] and result['reason'] in {
                    'context_bound_exceeded', 'invalid_candidate_tokens', 'unsupported_control_marker', 'backend_error'}, 'Unaccounted native refusal')
                require(call['nativeLogits'] == [], 'Refused native call cannot claim logits')
                if result['reason'] == 'unsupported_control_marker':
                    require(render_error == result['reason'] and paths == [] and not call['inferenceAttempted'], 'Reserved source control syntax was inferred')
                elif result['reason'] in {'context_bound_exceeded', 'invalid_candidate_tokens'}:
                    require(not call['inferenceAttempted'], 'Invalid or clipped native input was inferred')
                    try:
                        validate_paths(paths, request.kind, len(request.options))
                    except DecisionError as error:
                        require(error.code == result['reason'], 'Recorded native path rejection differs')
                    else:
                        raise ValueError('Recorded native paths should have been refused')
            else:
                validate_paths(paths, request.kind, len(request.options))
                logits = call['nativeLogits']
                require(call['inferenceAttempted'] and call['rejection'] is None and result['inputTokens'] == sum(map(len, paths))
                    and isinstance(logits, list) and len(logits) == len(request.options)
                    and all(type(z) in (int, float) and math.isfinite(z) for z in logits)
                    and (request.kind != 'boolean' or logits[0] == 0), 'Native logits or complete-token inventory differs')
                mapped = dict(zip(call['requestOptionIdsInNativeOrder'], logits, strict=True))
                require(canonical_json([row['logit'] for row in result['distribution']]) == canonical_json([float(mapped[option.id]) for option in request.options]),
                    'Raw native logits were not mapped to the requested typed candidates')
            require(call['inferenceMs'] is None or type(call['inferenceMs']) in (int, float)
                and math.isfinite(call['inferenceMs']) and 0 <= call['inferenceMs'] <= 1e12, 'Invalid native forward timing')
            require(call['inferenceAttempted'] == (call['inferenceMs'] is not None), 'Native forward timing missing or fabricated')
            rebuilt = replay_scores(case, result, evaluation['model'], Policy(), None, reverse=bool(offset))
            require(canonical_json(rebuilt['result']) == canonical_json(result), 'Typed result differs from raw native logits')
            rows[order].append(rebuilt)
        original, reverse = rows['original'][-1], rows['reversed'][-1]
        agreement = original['result']['selectedOptionId'] == reverse['result']['selectedOptionId'] if all(row['result']['status'] != 'error' for row in (original, reverse)) else None
        require(type(raw['correct']) is bool and raw['correct'] == original['correct'] and raw['orderAgreement'] is agreement, 'Saved native correctness/order agreement differs')
        if agreement is False: changed.append(case['id'])
    require(canonical_json(evaluation['metrics']) == canonical_json(metrics(rows['original']))
        and canonical_json(evaluation['families']) == canonical_json({family: metrics([row for row in rows['original'] if row['family'] == family])
            for family in sorted({case['family'] for case in dataset['cases']})}), 'Cached native metrics differ')
    paired = [raw for _, raw in scores if raw['orderAgreement'] is not None]
    order_metrics = {'attemptedPairs': len(scores), 'scoredPairs': len(paired),
        'agreement': sum(raw['orderAgreement'] for raw in paired) / len(paired) if paired else None,
        'reversedBackendErrors': sum(row['result']['status'] == 'error' for row in rows['reversed']),
        'reversedAccuracyAllAttempts': sum(row['correct'] for row in rows['reversed']) / len(scores)}
    require(canonical_json(evaluation['optionOrder']) == canonical_json(order_metrics), 'Cached native order metrics differ')
    return {'caseCount': len(scores), 'dependencyGroupCount': len({case['groupId'] for case in dataset['cases']}),
        'nativeInferenceAttempts': sum(call['inferenceAttempted'] for call in audit),
        'nativeCandidatePaths': sum(len(call['leafTokenIds']) for call in audit if call['inferenceAttempted']),
        'original': metrics(rows['original']), 'reversed': metrics(rows['reversed']), 'changedCaseIds': changed,
        'families': {family: {order: metrics([row for row in values if row['family'] == family]) for order, values in rows.items()}
            for family in sorted({case['family'] for case in dataset['cases']})},
        'nativeInferenceP50Ms': percentile([call['inferenceMs'] for call in audit if call['inferenceMs'] is not None], .5),
        'nativeInferenceP95Ms': percentile([call['inferenceMs'] for call in audit if call['inferenceMs'] is not None], .95)}


def measure(dataset, directory, identity, *, backend_factory=NanoJevBackend, capture=snapshot):
    development_dataset(dataset)
    samples = [capture(PHASES[0])]
    backend = backend_factory(directory)
    samples.append(capture(PHASES[1]))
    evaluation = evaluate(DecisionEngine(backend, Policy()), dataset, 'development', reverse_options=True)
    summary = recompute(dataset, evaluation, backend.audit)
    samples.append(capture(PHASES[2]))
    return sealed({'schemaVersion': REPORT_SCHEMA, 'status': 'diagnostic_only', 'usage': 'development-only', **AUTHORITY,
        'sourceIdentity': identity, 'datasetSha256': fingerprint(dataset), 'modelDescriptorSha256': fingerprint(catalog()),
        'evaluation': evaluation, 'nativeCalls': backend.audit, 'summary': summary, 'modelLoadMs': backend.load_ms,
        'resourceSamples': samples, 'resourceSummary': summarize(samples),
        'limitations': ['Published loader is CUDA-only; this is an explicit CPU FP32/eager reference using the pinned native heads.',
            'The checkpoint is trained for game decisions; Russian-document or Boolean/Score qualification is not established.',
            'Authored synthetic development cases and their permutations are not independent human qualification labels.',
            'RSS is a whole-process high-water mark, not isolated model allocation or concurrency capacity.',
            'No temperature, policy, weights, labels or splits are fitted or promoted by this command.']})


def verify_report(dataset, report):
    verify_seal(report, REPORT_SCHEMA)
    fields(report, {'schemaVersion', 'sha256', 'status', 'usage', 'sourceIdentity', 'datasetSha256', 'modelDescriptorSha256',
        'evaluation', 'nativeCalls', 'summary', 'modelLoadMs', 'resourceSamples', 'resourceSummary', 'limitations'} | set(AUTHORITY))
    identity = report['sourceIdentity']; fields(identity, {'sourceCommit', 'sourceFiles'})
    require(isinstance(identity['sourceCommit'], str) and re.fullmatch('[0-9a-f]{40}', identity['sourceCommit'])
        and isinstance(identity['sourceFiles'], dict) and identity['sourceFiles'] and all(isinstance(name, str)
            and not Path(name).is_absolute() and '..' not in Path(name).parts and isinstance(digest, str)
            and re.fullmatch('[0-9a-f]{64}', digest) for name, digest in identity['sourceFiles'].items()), 'Invalid native source identity')
    require(report['status'] == 'diagnostic_only' and report['usage'] == 'development-only'
        and all(type(report.get(key)) is type(value) and report[key] == value for key, value in AUTHORITY.items()), 'Native development authority changed')
    descriptor = catalog(); model = report['evaluation']['model']
    require(report['datasetSha256'] == fingerprint(development_dataset(dataset)) and report['modelDescriptorSha256'] == fingerprint(descriptor),
        'Native dataset/model descriptor differs')
    require(model['repository'] == descriptor['repository'] and model['revision'] == descriptor['revision']
        and model['backend'] == 'nanojev-native-decision-heads-cpu-reference' and model['artifactSha256'] == fingerprint(descriptor['files'])
        and model['tokenizerSha256'] == descriptor['files']['tokenizer/tokenizer.json']['sha256']
        and canonical_json(model['profile']) == canonical_json(PROFILE) and model['stack']['versions'] == PACKAGE_VERSIONS
        and model['stack']['nativeSourceFiles'] == {name: descriptor['files'][name]['sha256'] for name in
            ('source/scripts/predict_toy_decisions.py', 'source/scripts/train_toy_decisions.py')}
        and model['strictStateDictLoaded'] is True, 'Unpinned native reference identity')
    fields(model['weightsLayout'], {'tensorCount', 'tensorNamesSha256', 'headerSha256', 'dataBytes', 'parameterStorage'})
    layout = model['weightsLayout']
    require(type(layout['tensorCount']) is int and layout['tensorCount'] > 0 and type(layout['dataBytes']) is int
        and 0 < layout['dataBytes'] < descriptor['files']['best.safetensors']['bytes'] and layout['parameterStorage'] == 'float32'
        and all(isinstance(layout[key], str) and re.fullmatch('[0-9a-f]{64}', layout[key]) for key in ('tensorNamesSha256', 'headerSha256')),
        'Invalid measured native tensor layout')
    require(type(report['modelLoadMs']) in (int, float) and math.isfinite(report['modelLoadMs']) and 0 <= report['modelLoadMs'] <= 1e12,
        'Invalid native load timing')
    require(canonical_json(report['summary']) == canonical_json(recompute(dataset, report['evaluation'], report['nativeCalls']))
        and canonical_json(report['resourceSummary']) == canonical_json(summarize(report['resourceSamples'])), 'Saved native scoring/resource summary differs')
    return report
