"""Real CLI signals must drain owned trees and retain a failed experiment report."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import test_temporal_log_retention as retention
from test_temporal_cleanup import launcher

ROOT = Path(__file__).resolve().parents[2]

# Only model/DB responses are fixtures. The launcher, OS signal, owned model
# tree, workload process, inventory, group teardown and report writer are real.
RUNNER = r'''import json,os,shutil,signal,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'scripts/test'))
import test_temporal_log_retention as retention
import test_temporal_shadow_control as fixture
from test_temporal_cleanup import launcher
real_spawn=subprocess.Popen;real_stop=launcher.shared.stop
def real_inventory(parent):
    # check_output delegates to the patched global Popen; use the saved class.
    with real_spawn(['/bin/ps','-axo','pid=,ppid='],stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,text=True) as query:
        stdout,_=query.communicate(timeout=5)
        if query.returncode:raise RuntimeError('Fixture inventory failed')
    table={int(p):int(pp) for p,pp in (line.split() for line in stdout.splitlines())}
    found={parent}
    while extra:={p for p,pp in table.items() if pp in found}-found:found.update(extra)
    return found,set(table)
destination=Path(sys.argv[1]);phase=sys.argv[2];processes=[]
case=retention.PrivateLogRetentionTests();case.setUp()
script=destination/'model-fixture.py';script.write_text(fixture.FIXTURE)
def announce():
    (destination/'ready.json').write_text(json.dumps({'groups':[p.pid for p in processes],
        'ownedPids':sorted(set().union(*(real_inventory(p.pid)[0] for p in processes)))}))
def spawn(args,**kwargs):
    if args==['ollama','serve']:
        ready=destination/'model-ready'
        with (destination/'model.log').open('wb') as log:
            process=real_spawn([sys.executable,str(script),str(ready)],cwd=ROOT,
                stdout=log,stderr=log,start_new_session=True)
        processes.append(process)
        deadline=time.monotonic()+10
        while not ready.exists():
            if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Model fixture startup failed')
            time.sleep(.02)
        if phase=='spawn':
            announce();os.kill(os.getpid(),signal.SIGTERM)
        return process
    process=real_spawn([sys.executable,'-c','import time;time.sleep(60)'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    processes.append(process);announce();return process
def request(port,path,body=None,timeout=5):
    if path=='/api/chat' and phase in ('warmup','cleanup'):
        announce();time.sleep(2)
    return case.request(port,path,body,timeout)
def stop(process):
    if phase=='cleanup' and process is not None:
        (destination/'cleanup-ready').touch();time.sleep(1)
    real_stop(process)
ROOT=Path.cwd()
launcher.subprocess.Popen=spawn;launcher.shared.inventory=real_inventory;launcher.shared.stop=stop
launcher.request=request
try:
    # Before this fix the CLI directly invoked main(), with default SIGTERM.
    result=getattr(launcher,'cli',launcher.main)()
    shutil.copy2(case.directory/'launcher.json',destination/'report.json')
    raise SystemExit(result)
finally:
    for process in processes:real_stop(process)
    case.doCleanups()
'''


@unittest.skipUnless(os.name == 'posix', 'CLI signal cleanup requires POSIX')
class NativeLauncherSignalsTests(unittest.TestCase):
    def exercise(self, signal_number, phase, *, repeated=False, injected=False):
        with tempfile.TemporaryDirectory(prefix='agat-launcher-signal-') as temporary:
            directory=Path(temporary);script=directory/'runner.py';script.write_text(RUNNER)
            diagnostics=(directory/'diagnostics.log').open('wb')
            process=subprocess.Popen([sys.executable,str(script),str(directory),phase],cwd=ROOT,
                stdout=diagnostics,stderr=diagnostics,start_new_session=True,
                env={**os.environ,'PYTHONPATH':str(ROOT)})
            ready=None
            try:
                deadline=time.monotonic()+15
                while not (directory/'ready.json').exists():
                    report_path=directory/'report.json';model_log=directory/'model.log'
                    details=(directory/'diagnostics.log').read_text()[-1500:]
                    if report_path.exists():details+=str(json.loads(report_path.read_text())['failure'])
                    if model_log.exists():details+=model_log.read_text()[-1500:]
                    self.assertIsNone(process.poll(),'Fixture launcher exited before signal admission: '+details)
                    self.assertLess(time.monotonic(),deadline,'Fixture did not announce owned processes')
                    time.sleep(.02)
                ready=json.loads((directory/'ready.json').read_text())
                self.assertGreaterEqual(len(ready['ownedPids']),3)
                if not injected:os.kill(process.pid,signal_number)
                if repeated:
                    deadline=time.monotonic()+10
                    while not (directory/'cleanup-ready').exists():
                        self.assertIsNone(process.poll(),'Cleanup was abandoned before the second signal')
                        self.assertLess(time.monotonic(),deadline)
                        time.sleep(.02)
                    os.kill(process.pid,signal.SIGINT)
                process.wait(timeout=20)
                self.assertEqual(process.returncode,1,'Interrupted experiment must persist as failure')
                report=json.loads((directory/'report.json').read_text())
                self.assertEqual(report['status'],'fail')
                self.assertEqual(report['failure']['type'],'ExperimentInterrupted')
                self.assertIn(signal.Signals(signal_number).name,report['failure']['reason'])
                self.assertEqual(report['cleanupErrors'],[])
                self.assertEqual(report['remainingOwnedPids'],[])
                self.assertEqual(report['modelsAfterUnload'],{'models':[]})
                alive=launcher.shared.inventory(os.getpid())[1]
                self.assertFalse(set(ready['ownedPids']) & alive)
            finally:
                # A failing pre-fix test must not leave any fixture behind.
                if process.poll() is None:process.kill();process.wait(timeout=5)
                if ready:
                    for pid in ready['groups']:
                        try:os.killpg(pid,signal.SIGTERM)
                        except ProcessLookupError:pass
                    deadline=time.monotonic()+5
                    while set(ready['ownedPids']) & launcher.shared.inventory(os.getpid())[1] and time.monotonic()<deadline:
                        time.sleep(.05)
                    for pid in ready['groups']:
                        try:os.killpg(pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                diagnostics.close()

    def test_sigterm_during_model_warmup_reaps_owned_tree_and_retains_failure(self):
        self.exercise(signal.SIGTERM,'warmup')

    def test_sigint_during_workload_reaps_both_groups_and_retains_failure(self):
        self.exercise(signal.SIGINT,'workload')

    def test_signal_between_spawn_and_handle_registration_cannot_orphan_model(self):
        self.exercise(signal.SIGTERM,'spawn',injected=True)

    def test_repeated_signal_during_cleanup_preserves_first_reason_and_drains(self):
        self.exercise(signal.SIGTERM,'cleanup',repeated=True)


class LauncherSignalHandlerTests(unittest.TestCase):
    def test_cli_restores_previous_sigterm_handler_on_return_and_exception(self):
        previous=signal.getsignal(signal.SIGTERM)
        previous_int=signal.getsignal(signal.SIGINT)
        for outcome in (0,ValueError('Preflight failure')):
            with self.subTest(outcome=type(outcome).__name__),patch.object(launcher,'main',side_effect=outcome if isinstance(outcome,Exception) else None,return_value=outcome):
                if isinstance(outcome,Exception):
                    with self.assertRaises(ValueError):launcher.cli()
                else:self.assertEqual(launcher.cli(),0)
                self.assertIs(signal.getsignal(signal.SIGTERM),previous)
                self.assertIs(signal.getsignal(signal.SIGINT),previous_int)


if __name__ == '__main__':unittest.main()
