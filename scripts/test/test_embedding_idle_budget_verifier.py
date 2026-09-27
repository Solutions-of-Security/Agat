"""Offline counter, ownership, concurrency and cleanup mutation checks."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / 'docs/qualification/local-decisions/performance/evidence/2026-09-27/embedding-idle-budget'
spec = importlib.util.spec_from_file_location('idle_budget_verifier', ROOT / 'scripts/verify-embedding-idle-budget.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class IdleBudgetEvidenceTests(unittest.TestCase):
    @contextmanager
    def changed(self, mutation):
        with tempfile.TemporaryDirectory(prefix='idle-replay-', dir=ROOT / 'docs') as folder:
            target = Path(folder)
            shutil.copytree(EVIDENCE, target, dirs_exist_ok=True)
            plan = json.loads((target / 'plan.json').read_text())
            result = json.loads((target / 'result.json').read_text())
            mutation(plan, result)
            (target / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            result['planSha256'] = verifier.replay.sha((target / 'plan.json').read_bytes())
            (target / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            yield target

    def rejects(self, mutation):
        with self.changed(mutation) as directory:
            with self.assertRaises((AssertionError, ValueError, KeyError)):
                verifier.verify(directory)

    def test_native_measurements_replay_without_native_apis(self):
        self.assertEqual(verifier.verify(EVIDENCE), json.loads((EVIDENCE / 'replay.json').read_text()))

    def test_mach_units_and_independent_calibration_cannot_be_relabelled(self):
        self.rejects(lambda p, _r: p.update(timebase={'numer': 1, 'denom': 1}))
        self.rejects(lambda _p, r: r['calibration'].update(processTimeAfterNs=r['calibration']['processTimeBeforeNs'] + 100_000_000))
        self.rejects(lambda p, _r: p.update(abiBytes=80))

    def test_source_baseline_and_no_model_contract_are_pinned(self):
        self.rejects(lambda p, _r: p['sourceSha256'].update({'workers/embedding_http.py': '0' * 64}))
        self.rejects(lambda p, _r: p.update(baselineCommit=p['implementationCommit']))
        self.rejects(lambda p, _r: p.update(model='real model'))

    def test_missing_capacity_or_shortened_idle_is_rejected(self):
        self.rejects(lambda _p, r: r['phases'][-1]['children'].pop())
        self.rejects(lambda p, _r: p['phases'][0].update(idleSeconds=.01))
        self.rejects(lambda _p, r: r['phases'][0]['idleAfter'][0].update(atNs=r['phases'][0]['idleBefore'][0]['atNs'] + 1000))

    def test_pid_reuse_and_replaced_executable_identity_are_rejected(self):
        self.rejects(lambda _p, r: r['phases'][0]['idleAfter'][0].update(startTicks=1))
        self.rejects(lambda _p, r: r['phases'][0]['idleAfter'][0].update(uuid='a' * 32))
        self.rejects(lambda _p, r: r['phases'][0]['idleAfter'][0].update(pid=1))

    def test_counter_reset_and_dead_process_do_not_count_as_idle(self):
        self.rejects(lambda _p, r: r['phases'][0]['idleAfter'][0].update(userTicks=0))
        self.rejects(lambda _p, r: r['phases'][0]['idleAfter'][0].update(exitTicks=1))
        self.rejects(lambda _p, r: r['phases'][0]['idleAfter'][0].update(rssBytes=0))

    def test_guard_binding_and_abba_order_are_verified(self):
        self.rejects(lambda p, _r: p['phases'][0].update(guard=True))
        self.rejects(lambda _p, r: r['phases'][1]['children'][0].update(arguments=['--serve']))
        self.rejects(lambda p, r: r['phases'][1]['children'][0].update(scriptSha256=p['baselineSha256']['workers/embedding_transport.py']))

    def test_successful_resume_requires_all_exact_vectors(self):
        self.rejects(lambda _p, r: r['phases'][-1]['afterCalls'].pop())
        self.rejects(lambda _p, r: r['phases'][-1]['afterCalls'][0].update(vectors=[[1, 1]]))
        self.rejects(lambda _p, r: r['phases'][0]['afterCalls'][0].update(startedNs=r['phases'][0]['idleBefore'][0]['atNs']))

    def test_http_barrier_and_every_actor_are_independent_evidence(self):
        self.rejects(lambda _p, r: r['phases'][4]['serverRows'][0].update(enteredNs=r['phases'][4]['serverRows'][1]['finishedNs'] + 1))
        self.rejects(lambda _p, r: r['phases'][4]['serverRows'][0].update(actor=99))
        self.rejects(lambda _p, r: r['phases'][4].update(serverErrors=['broken']))

    def test_leaked_fds_pipes_processes_and_threads_fail(self):
        self.rejects(lambda _p, r: r['phases'][0].update(fdAfterClose=r['phases'][0]['fdBefore'] + 1))
        self.rejects(lambda _p, r: r['phases'][0]['children'][0].update(stdoutClosed=False))
        self.rejects(lambda _p, r: r['phases'][0]['children'][0].update(returncode=None))
        self.rejects(lambda _p, r: r['phases'][0].update(remainingThreads=['deadline']))

    def test_failed_run_is_not_accepted(self):
        self.rejects(lambda _p, r: r.update(status='incomplete'))
        self.rejects(lambda _p, r: r.update(failure='cleanup'))


if __name__ == '__main__':
    unittest.main()
