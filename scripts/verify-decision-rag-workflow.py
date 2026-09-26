#!/usr/bin/env python3
"""Recheck frozen RAG evidence without loading models or rerunning the workload."""

import argparse
import hashlib
import math
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from decision_runtime import VERSION, implementation_sha256
from decision_runtime.artifacts import read_json, sealed, verify_seal, write_new
from decision_runtime.contracts import Request
from scripts.lib.decision_performance import distribution, profile_from_health, validate_result


def require(value, message):
    if not value: raise ValueError(message)


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def peak(calls):
    events = sorted((row[key], delta) for row in calls for key, delta in (('startedMs', 1), ('finishedMs', -1)))
    current = maximum = 0
    for _at, delta in events:
        current += delta;maximum = max(maximum, current)
        require(current >= 0, 'Invalid call interval')
    require(current == 0, 'Unfinished call')
    return maximum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-snapshot', type=Path, action='append', default=[])
    args = parser.parse_args()
    require(args.output.resolve().is_relative_to(ROOT / 'docs') and not args.output.exists(), 'Use new evidence under docs')
    base = args.evidence_dir
    snapshots = [verify_seal(read_json(path), 'agat.decision.source-snapshot.v1') for path in args.source_snapshot]
    plan_path, result_path = [base / f'rag-workflow-{name}.json' for name in ('plan', 'result')]
    # The workload uses JS JSON.stringify insertion order/number spelling. Check
    # that seal in its native serializer, independently of the workload module.
    subprocess.run(['node', '-e', '''
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const r=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));const {sha256,...body}=r;
assert.equal(crypto.createHash('sha256').update(JSON.stringify(body)).digest('hex'),sha256);
''', str(result_path)], check=True, timeout=10)
    plan, result = read_json(plan_path), read_json(result_path)
    lp = verify_seal(read_json(base / 'rag-workflow-launcher-plan.json'), 'agat.decision.rag-launcher-plan.v1')
    lr = verify_seal(read_json(base / 'rag-workflow-launcher-result.json'), 'agat.decision.rag-launcher-result.v1')
    require(plan['schemaVersion'] == 'agat.decision.workflow-plan.v1'
            and result['schemaVersion'] == 'agat.decision.workflow-result.v1', 'Unexpected workload schema')
    require(result['status'] == lr['status'] == 'observed' and result['failure'] is lr['failure'] is None
            and result['planSha256'] == sha(plan_path.read_bytes()) and lr['launcherPlanSha256'] == lp['sha256'], 'Incomplete/unbound run')
    require(all(value['routingEnabled'] is False for value in (plan, result, lp, lr))
            and result['qualification'] == plan['qualification'] == 'not_assessed' and lr['qualifiedForRouting'] is False, 'Unexpected qualification')
    for evidence in (plan, lp):
        for name, digest in evidence['files'].items():
            file = (ROOT / name).resolve()
            require(file.is_relative_to(ROOT), 'Source outside workspace')
            matches = file.is_file() and sha(file.read_bytes()) == digest
            for snapshot in snapshots:
                previous = snapshot['files'].get(name)
                if previous and previous['sha256'] == digest and sha(previous['content']) == digest: matches = True
            require(matches, 'Frozen source changed; supply its verified snapshot: ' + name)
    fixture = plan['fixture'];rag = fixture['rag'];sources = {source['id']: source for source in rag['sources']}
    require(len(sources) == 2 and fixture == read_json(ROOT / 'docs/qualification/local-decisions/performance/rag-workflow.fixture.json'), 'Fixture mismatch')
    for key in ('primary', 'embedding'):
        identity = plan[key]
        require(identity['digest'] == lp['models'][identity['name']] and identity['version'] == lr['ollamaVersion']['version'], 'Model identity mismatch')
    require(plan['embedding']['name'] == rag['embeddingModel'] and plan['embedding']['generation']['truncate'] is False, 'Embedding contract changed')
    profile = profile_from_health({'status': 'ready', 'mode': 'shadow', **plan['decision']})
    require(profile['runtimeVersion'] == VERSION and profile['model']['implementationSha256'] == implementation_sha256(), 'Runtime changed')
    require(lr['runtimeAvailableBeforeCleanup'] is True and lr['backendDiagnostics']['stopReason'] == 'closed'
            and lr['backendDiagnostics']['available'] is False and lr['backendDiagnostics']['childExitCode'] is not None
            and lr['modelsAfterUnload'] == {'models': []} and not lr['cleanupErrors'] and not lr['processInventoryErrors'], 'Cleanup/inventory incomplete')
    require(lr['nodeExitCode'] == 0 and lr['ollamaExitCode'] is not None, 'Owned command still running/failed')
    for pid in lr['observedOwnedPids']:
        require(type(pid) is int and pid > 0, 'Invalid PID')
        try: os.kill(pid, 0)
        except ProcessLookupError: continue
        raise ValueError(f'Observed owned PID {pid} still exists; inspect before claiming cleanup')
    require(len(plan['phases']) == len(result['batches']) == 4 and result['warmup'] is not None, 'Missing phase or warmup')
    totals = Counter();phases = [];finals = []
    for expected, batch in zip(plan['phases'], result['batches']):
        require(batch['phase'] == expected and batch['status'] == 'observed' and batch['failure'] is None
                and all(batch['checks'].values()), 'Incomplete phase')
        runs, calls, embeddings, decisions = (batch[key] for key in ('workflows', 'primaryCalls', 'embeddingCalls', 'decisionCalls'))
        n = expected['runs'] * 3
        require(len(runs) == expected['runs'] and len(calls) == n and len(decisions) == (n if expected['shadow'] else 0), 'Unexpected call counts')
        for call in calls + embeddings:
            require(call['status'] == 'completed' and 0 <= call['startedMs'] < call['finishedMs'] <= batch['elapsedMs'], 'Failed/invalid call')
        require(peak(calls) == expected['concurrency'] == batch['maxPrimaryRequestsInFlight']
                and peak(embeddings) == batch['maxEmbeddingRequestsInFlight'], 'HTTP concurrency mismatch')
        actual_overlap = sum(any(p['startedMs'] < d['finishedMs'] and p['finishedMs'] > d['startedMs'] for p in calls) for d in decisions)
        require(actual_overlap == batch['shadowCallsOverlappingPrimaryHttp'], 'Overlap mismatch')
        ingested = batch['rag'];dims = ingested['dimensions']
        require(ingested['embeddingModel'] == rag['embeddingModel'] and len(ingested['sources']) == 2 and dims > 0, 'Bad ingestion')
        for source in ingested['sources']:
            original = sources[source['id']]
            require(source['sourceUri'] == original['sourceUri'] and source['documentSha256'] == source['chunkSha256'] == sha(original['content'])
                    and source['dimensions'] == dims, 'Ingested provenance mismatch')
        items = Counter()
        for call in embeddings:
            require(call['model'] == rag['embeddingModel'] and call['dimensions'] == dims and call['inputTokens'] > 0
                    and call['items'] == len(call['inputSha256']) == len(call['vectorSha256']), 'Embedding evidence mismatch')
            items.update(zip(call['inputSha256'], call['vectorSha256']))
        require(sum(items.values()) == 2 + n, 'Unexpected embedding items')
        stage_outputs = [];statuses = Counter();coverage = unknown = 0
        decision_by_stage = {call['stageId']: call for call in decisions}
        require(len(decision_by_stage) == len(decisions), 'Duplicate shadow request')
        for run in runs:
            stages = sorted(run['stages'], key=lambda stage: stage['position'])
            require([stage['nodeId'] for stage in stages] == [role['id'] for role in fixture['roles']], 'Wrong stage sequence')
            require(math.isclose(run['wallMs'], run['finishedMs'] - run['startedMs'], abs_tol=.002), 'Workflow timer mismatch')
            known = {};context = [];state = fixture['input']
            for stage in stages:
                require(sha(stage['output']) == stage['outputSha256'], 'Changed primary output')
                stage_outputs.append(stage['outputSha256'])
                require(stage['metrics']['model'] == plan['primary']['name'] and stage['metrics']['modelCalls'] == 1, 'Wrong primary execution')
                retrieval = stage['retrieval'];require(len(retrieval['hits']) == 2 and len(retrieval['queries']) == 1, 'Missing retrieval')
                require({hit['sourceId'] for hit in retrieval['hits']} == set(sources), 'Source coverage changed')
                for hit in retrieval['hits']:
                    require(hit['chunkSha256'] == sha(sources[hit['sourceId']]['content']) and math.isfinite(hit['score']), 'Source changed')
                    require(re.fullmatch(r'K[0-9]+', hit['marker']) and hit['marker'] not in known, 'Marker reused')
                    known[hit['marker']] = hit
                require(retrieval['knownCitations'] == list(known.values()), 'Earlier/current citation bindings changed')
                markers = list(dict.fromkeys(re.findall(r'\[(K[0-9]+)\]', stage['output'])))
                cited = list(dict.fromkeys(known[marker]['sourceId'] for marker in markers if marker in known))
                missing = [marker for marker in markers if marker not in known]
                both = set(cited) == set(sources)
                require(retrieval['outputMarkers'] == markers and retrieval['unknownMarkers'] == missing
                        and retrieval['citedSourceIds'] == cited and retrieval['bothSourcesCited'] == both, 'Citation evidence mismatch')
                coverage += both;unknown += bool(missing)
                query = retrieval['queries'][0]
                query_text = '\n\n'.join([state, *context]).strip()[:50_000]
                pair = (sha(query_text), query['vectorSha256'])
                require(query['dimensions'] == dims and items[pair] > 0, 'Embedding query not bound to worker input/vector')
                items[pair] -= 1
                if expected['shadow']:
                    request = Request.from_dict({'schemaVersion': 'agat.decision.v1', 'id': stage['id'], 'state': state, **fixture['shadow']})
                    call = decision_by_stage[stage['id']];answer = call['result']
                    validate_result(answer, request, profile)
                    require(call['httpStatus'] == 200 and stage['observation']['fallback'] == 'primary'
                            and stage['observation']['status'] == answer['status'] and stage['observation']['reason'] == answer['reason'], 'Shadow observation mismatch')
                    statuses[f"{answer['status']}/{answer['reason']}"] += 1
                else: require('observation' not in stage, 'Unexpected shadow')
                context.append(stage['output']);state = stage['output']
            finals.append({'phase': expected['id'], 'runId': run['runId'], 'outputSha256': stages[-1]['outputSha256'],
                           'bothSourcesCited': stages[-1]['retrieval']['bothSourcesCited'], 'unknownMarkers': stages[-1]['retrieval']['unknownMarkers']})
        remaining = Counter()
        for (input_sha, _vector_sha), count in items.items():
            require(count >= 0, 'Embedding reused beyond calls')
            remaining[input_sha] += count
        require(+remaining == Counter(sha(source['content']) for source in sources.values()), 'Source embeddings not bound')
        require(Counter(stage_outputs) == Counter(call['outputSha256'] for call in calls), 'Primary multiset changed')
        for call in calls:
            require(sha(call['output']) == call['outputSha256'] and call['doneReason'] == 'stop'
                    and set(call['retrievedSourceIds']) == set(sources), 'Incomplete primary or source coverage assertion')
        totals.update(workflows=len(runs), primaryCalls=n, shadowCalls=len(decisions), embeddingItems=2 + n,
                      stagesWithBothSourcesCited=coverage, stagesWithUnknownMarkers=unknown)
        phases.append({'id': expected['id'], 'elapsedMs': batch['elapsedMs'], 'indexingMs': ingested['ingestionMs'],
                       'workflows': len(runs), 'primaryCalls': n, 'shadowCalls': len(decisions), 'embeddingCalls': len(embeddings),
                       'embeddingItems': 2 + n, 'embeddingDimensions': dims, 'primaryHttpConcurrency': peak(calls),
                       'embeddingHttpConcurrency': peak(embeddings), 'shadowOverlappingPrimaryHttp': actual_overlap,
                       'shadowOutcomes': dict(statuses), 'workflowWallMs': distribution([run['wallMs'] for run in runs]),
                       'shadowWallMs': distribution([call['finishedMs'] - call['startedMs'] for call in decisions]),
                       'embeddingWallMs': distribution([call['finishedMs'] - call['startedMs'] for call in embeddings]),
                       'stagesWithBothSourcesCited': coverage, 'stagesWithUnknownMarkers': unknown})
    require(totals['workflows'] == 12 and totals['primaryCalls'] == 36 and totals['shadowCalls'] == 18 and totals['embeddingItems'] == 44, 'Incomplete plan')
    paired = None
    if plan.get('design', {}).get('id') == 'paired-rag':
        require(lp['design'] == 'paired-rag' and plan['design']['order'] == 'ABBA'
                and plan['design']['modelVisibleMetadataId'] == 'paired_rag'
                and [phase['shadow'] for phase in plan['phases']] == [False, True, True, False]
                and all(phase['concurrency'] == 2 and phase['runs'] == 3 for phase in plan['phases']), 'Paired plan changed')
        warmup = result['warmup']
        require(warmup['embedding']['items'] == 2 and warmup['embedding']['inputSha256'] == [sha(s['content']) for s in rag['sources']],
                'Embedding warmup changed')
        request = Request.from_dict({'schemaVersion': 'agat.decision.v1', 'id': 'paired-rag-warmup', 'state': fixture['input'], **fixture['shadow']})
        validate_result(warmup['decision'], request, profile)
        require(warmup['decision']['status'] in ('ok', 'abstain'), 'Failed decision warmup')
        count = lambda batch, field: [list(pair) for pair in sorted(Counter(call[field] for call in batch['primaryCalls']).items())]
        prompts = [count(batch, 'messagesSha256') for batch in result['batches']]
        outputs = [count(batch, 'outputSha256') for batch in result['batches']]
        first = [batch['primaryCalls'][0]['messagesSha256'] for batch in result['batches']]
        paired = {'firstPrimaryMessagesSha256': first, 'firstPrimaryMessagesIdentical': len(set(first)) == 1,
                  'promptMultisets': prompts, 'outputMultisets': outputs,
                  'allPrimaryPromptMultisetsIdentical': all(p == prompts[0] for p in prompts),
                  'allPrimaryOutputMultisetsIdentical': all(o == outputs[0] for o in outputs)}
        require(result['comparison'] == paired and paired['firstPrimaryMessagesIdentical'], 'Prompt comparison mismatch')
    report = sealed({'schemaVersion': 'agat.decision.rag-workflow-verification.v1', 'createdAt': datetime.now(timezone.utc).isoformat(),
                     'status': 'verified_diagnostic_only', 'planSha256': result['planSha256'], 'resultSha256': result['sha256'],
                     'launcherResultSha256': lr['sha256'], 'runtimeVersion': VERSION, 'implementationSha256': implementation_sha256(),
                     'profileSha256': plan['decision']['profileSha256'], 'totals': dict(totals), 'phases': phases, 'finalReports': finals,
                     'verifiedGonePids': lr['observedOwnedPids'], 'verifierSha256': sha(Path(__file__).read_bytes()),
                     'sourceSnapshotsSha256': [snapshot['sha256'] for snapshot in snapshots], 'pairedComparison': paired,
                     'routingEnabled': False, 'qualifiedForRouting': False,
                     'checks': {'artifactSealsAndFrozenSources': True, 'runtimeProfileAndTypedShadowResults': True,
                                'queryInputAndVectorBindings': True, 'sourceDocumentAndChunkDigests': True,
                                'primaryOutputMultisetsPreserved': True, 'priorAndCurrentCitationBindings': True,
                                'httpConcurrencyAndOverlapRecomputed': True, 'ownedModelsUnloadedAndPidsGone': True},
                     'limitations': ['Primary prompts and full traces were not retained; source delivery and full trace provenance were asserted by the frozen live harness.',
                                     'Citation presence is not interpretation or arithmetic accuracy; no quality qualification.',
                                     'One authored task repeated with two documents; no independent cases, Temporal, external business connector or production SLO.']})
    write_new(args.output, report);print({'status': report['status'], **dict(totals)})


if __name__ == '__main__': main()
