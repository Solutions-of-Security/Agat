import importlib.util
import contextlib
import json
import os
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from decision_runtime.contracts import canonical_json,fingerprint

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('resident_manage',ROOT/'scripts/manage-decision-resident-deployment.py')
manage = importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(manage)


class ServiceManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.bundle_root = self.root/'release';self.bundle_root.mkdir()
        (self.bundle_root/'launchd').mkdir();(self.bundle_root/'logs').mkdir()
        for label in manage.LABELS: (self.bundle_root/'launchd'/f'{label}.plist').write_bytes(label.encode())
        self.profile = json.loads((ROOT/'docs/qualification/local-decisions/performance/profiles/runtime-0.12.2.json').read_text())
        self.bundle = {'sha256':'b'*64,'profile':self.profile,'profileSha256':fingerprint(self.profile)}
        self.home = patch.object(Path,'home',return_value=self.root);self.home.start();self.addCleanup(self.home.stop)
        self.children = [{'pid':112,'role':'inference'},{'pid':113,'role':'resource_tracker'}]
        self.health = {'status':'ready','mode':'shadow','profileJson':canonical_json(self.profile),'profileSha256':fingerprint(self.profile)}
        self.up = {'status':'success','data':{'resultType':'vector','result':[{'metric':{'__name__':'up','job':'agat-decision','instance':'127.0.0.1:8766'},'value':[1,'1']}]}}
        self.targets = {'status':'success','data':{'activeTargets':[{'labels':{'job':'agat-decision','instance':'127.0.0.1:8766'},
                       'scrapeUrl':'http://127.0.0.1:8766/metrics','health':'up','lastError':'','lastScrape':datetime.now(timezone.utc).isoformat()}]}}

    def evidence(self,**changes):
        path = self.root/'gate.json'
        body = {'schemaVersion':'agat.decision.resident-package-verification.v1','status':'verified',
                'bundleSeal':self.bundle['sha256'],'profileSha256':self.bundle['profileSha256'],
                'routingEnabled':False,'checks':dict.fromkeys(manage.PACKAGE_CHECKS,True),'identicalToPreviousNativeBaseline':True}
        body.update(changes);path.write_text(canonical_json(sealed(body)));return path

    def test_incomplete_failed_or_other_bundle_gate_cannot_authorize_install(self):
        manage.gate_evidence(self.evidence(),self.bundle)
        for changes in ({'checks':{}},{'checks':dict.fromkeys(manage.PACKAGE_CHECKS,False)},
                        {'bundleSeal':'c'*64},{'routingEnabled':True},{'identicalToPreviousNativeBaseline':False}):
            with self.subTest(changes=changes),self.assertRaises(RuntimeError): manage.gate_evidence(self.evidence(**changes),self.bundle)

    def test_bundle_traversal_and_symlink_inputs_are_rejected(self):
        (self.bundle_root/'file').write_bytes(b'owned')
        (self.bundle_root/'linked').symlink_to(self.bundle_root/'file')
        for name in ('../file','/absolute/file','linked'):
            with self.subTest(name=name),self.assertRaises(RuntimeError): manage.checked_path(self.bundle_root,name)
        self.assertEqual(manage.checked_path(self.bundle_root,'file'),self.bundle_root/'file')

    def test_preflight_refuses_existing_job_plist_and_occupied_port(self):
        with patch.object(manage,'service_info',return_value={'pid':111}),patch.object(manage.socket,'socket') as sock:
            with self.assertRaisesRegex(RuntimeError,'existing'): manage.preflight(self.bundle_root,8766,9095)
            sock.assert_not_called()
        source,path = manage.plist_paths(self.bundle_root)[0];path.parent.mkdir(parents=True);path.write_bytes(b'foreign')
        with patch.object(manage,'service_info',return_value=None),self.assertRaisesRegex(RuntimeError,'existing'): manage.preflight(self.bundle_root,8766,9095)
        self.assertEqual(path.read_bytes(),b'foreign');path.unlink()
        with patch.object(manage,'service_info',return_value=None),patch.object(manage.socket,'socket') as sock:
            sock.return_value.__enter__.return_value.bind.side_effect = OSError('address already in use')
            with self.assertRaises(OSError): manage.preflight(self.bundle_root,8766,9095)

    def test_preflight_reuses_time_wait_after_stop_but_refuses_live_listener(self):
        with manage.socket.socket() as listener,manage.socket.socket() as monitor:
            listener.setsockopt(manage.socket.SOL_SOCKET,manage.socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',0));listener.listen(1);port = listener.getsockname()[1]
            monitor.bind(('127.0.0.1',0));monitor_port = monitor.getsockname()[1];monitor.close()
            with patch.object(manage,'service_info',return_value=None):
                with self.assertRaises(OSError): manage.preflight(self.bundle_root,port,monitor_port)
                with manage.socket.create_connection(('127.0.0.1',port),timeout=2) as client:
                    peer,_ = listener.accept();peer.close()
                    self.assertEqual(client.recv(1),b'')
                listener.close()
                self.assertEqual(manage.preflight(self.bundle_root,port,monitor_port)['status'],'verified')

    def test_registered_label_must_use_owned_plist_path(self):
        label = manage.LABELS[0];expected = self.root/'Library/LaunchAgents'/f'{label}.plist'
        state = SimpleNamespace(returncode=0,stdout=f'path = {expected}\n pid = 111\n last exit code = 75: EX_TEMPFAIL\n',stderr='')
        with patch.object(manage,'launchctl',return_value=state):
            self.assertEqual(manage.owned_service_info(self.bundle_root,label)['pid'],111)
            self.assertEqual(manage.owned_service_info(self.bundle_root,label)['last exit code'],75)
        state.stdout = 'path = /foreign/job.plist\n pid = 111\n'
        with patch.object(manage,'launchctl',return_value=state),self.assertRaisesRegex(RuntimeError,'foreign'): manage.owned_service_info(self.bundle_root,label)

    def test_startup_waits_for_first_scrape_and_retains_owned_pids(self):
        empty = {'status':'success','data':{'resultType':'vector','result':[]}}
        pids = set();inventory = {'complete':True}
        with patch.object(manage,'owned_service_info',side_effect=lambda root,label:{'pid':111 if label == manage.LABELS[0] else 211}),\
             patch.object(manage,'child_processes',return_value=self.children),patch.object(manage,'api',side_effect=[self.health,empty,self.targets,self.health,self.up,self.targets]),\
             patch.object(manage.time,'sleep'):
            result = manage.inspect_services(self.bundle_root,self.bundle,8766,9095,timeout=2,owned_pids=pids,inventory=inventory)
        self.assertEqual(result['status'],'ready');self.assertEqual(pids,{111,112,113,211});self.assertTrue(inventory['complete'])

    def test_startup_transient_unready_api_is_retried(self):
        with patch.object(manage,'owned_service_info',return_value={'pid':111}),patch.object(manage,'child_processes',return_value=self.children),\
             patch.object(manage,'api',side_effect=[OSError('503'),self.health,self.up,self.targets]),patch.object(manage.time,'sleep'):
            self.assertEqual(manage.inspect_services(self.bundle_root,self.bundle,8766,9095,timeout=2)['status'],'ready')

    def test_wrong_profile_keeps_inventory_for_rollback(self):
        pids = set();bad = dict(self.health,profileJson=canonical_json(dict(self.profile,runtimeVersion='0.12.1')))
        with patch.object(manage,'owned_service_info',return_value={'pid':111}),patch.object(manage,'child_processes',return_value=self.children),patch.object(manage,'api',return_value=bad):
            with self.assertRaises((RuntimeError,ValueError)): manage.inspect_services(self.bundle_root,self.bundle,8766,9095,owned_pids=pids)
        self.assertEqual(pids,{111,112,113})

    def test_stop_inventory_does_not_depend_on_health_or_scrape(self):
        with patch.object(manage,'owned_service_info',return_value={'pid':111}),patch.object(manage,'child_processes',return_value=self.children),patch.object(manage,'api') as api:
            self.assertEqual(manage.inspect_services(self.bundle_root,self.bundle,8766,9095,include_api=False)['status'],'observed')
            api.assert_not_called()

    def test_child_inventory_failure_cannot_report_clean_rollback(self):
        pids = set();inventory = {'complete':True}
        with patch.object(manage,'owned_service_info',return_value={'pid':111}),patch.object(manage,'child_processes',side_effect=RuntimeError('unknown child')):
            manage.inspect_services(self.bundle_root,self.bundle,8766,9095,owned_pids=pids,inventory=inventory,include_api=False)
        self.assertEqual(pids,{111});self.assertFalse(inventory['complete'])
        with patch.object(manage,'gone',return_value=True),self.assertRaisesRegex(RuntimeError,'incomplete'):
            manage.rollback(self.bundle_root,[],[],pids,inventory['complete'])

    def test_partial_owned_write_is_removed_and_changed_or_replaced_file_is_preserved(self):
        path = self.root/'new.plist';path.write_bytes(b'ab');record = manage.file_record(path,b'abcdef',complete=False)
        manage.remove_owned_file(record);self.assertFalse(path.exists())
        created = [];manage.exclusive_file(path,b'abcdef',created);path.write_bytes(b'foreign')
        self.addCleanup(manage.close_file_record,created[0])
        with self.assertRaises(RuntimeError): manage.remove_owned_file(created[0])
        self.assertEqual(path.read_bytes(),b'foreign');path.unlink()
        replacement = self.root/'replacement';replacement.write_bytes(b'abcdef');replacement.replace(path)
        with self.assertRaises(RuntimeError): manage.remove_owned_file(created[0])
        self.assertTrue(path.exists())

    def test_reused_inode_with_identical_bytes_cannot_pass_ownership(self):
        path = self.root/'reused.plist';path.write_bytes(b'owned');record = manage.file_record(path)
        self.addCleanup(manage.close_file_record,record)
        real_stat = path.stat()
        reused = SimpleNamespace(st_dev=real_stat.st_dev,st_ino=real_stat.st_ino,st_ctime_ns=real_stat.st_ctime_ns+1)
        with patch.object(Path,'is_file',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(Path,'stat',return_value=reused):
            with self.assertRaisesRegex(RuntimeError,'changed'): manage.remove_owned_file(record)
        self.assertEqual(path.read_bytes(),b'owned')

    def test_interrupted_unbuffered_write_retains_identity_for_partial_cleanup(self):
        path = self.root/'partial.plist';created = [];real_write = os.write
        def partial(fd,data):
            real_write(fd,data[:2]);raise KeyboardInterrupt('write interrupted')
        with patch.object(manage.os,'write',side_effect=partial),self.assertRaises(KeyboardInterrupt): manage.exclusive_file(path,b'abcdef',created)
        self.assertEqual(path.read_bytes(),b'ab');manage.remove_owned_file(created[0]);self.assertFalse(path.exists())

    def test_bootstrap_failure_rolls_out_only_owned_label_and_new_files(self):
        source,path = manage.plist_paths(self.bundle_root)[0];path.parent.mkdir(parents=True)
        created = [];manage.exclusive_file(path,source.read_bytes(),created);pids = set()
        with patch.object(manage,'owned_service_info',return_value={'pid':111}),patch.object(manage,'child_processes',return_value=self.children),\
             patch.object(manage,'launchctl') as ctl,patch.object(manage,'wait_for_removal',return_value=True),patch.object(manage,'gone',return_value=True) as gone:
            checks = manage.rollback(self.bundle_root,[source.stem],created,pids)
        self.assertTrue(all(checks.values()));self.assertEqual(pids,{111,112,113});self.assertFalse(path.exists())
        ctl.assert_called_once_with('bootout',f'gui/{os.getuid()}/{source.stem}');gone.assert_called_once_with([111,112,113],timeout=8)

    def test_counter_requires_finite_integral_computed_sample(self):
        def result(value): return {'status':'success','data':{'resultType':'vector','result':[{'metric':{},'value':[1,value]}]}}
        for value in ('NaN','+Inf','-1','0.5'):
            with self.subTest(value=value),patch.object(manage,'api',return_value=result(value)),self.assertRaises(RuntimeError): manage.wait_scored_counter(9095)
        with patch.object(manage,'api',side_effect=[result('0'),result('1')]),patch.object(manage.time,'sleep'):
            self.assertEqual(manage.wait_scored_counter(9095)['data']['result'][0]['value'][1],'1')

    def test_old_scrape_cannot_confirm_a_new_installation(self):
        with patch.object(manage,'owned_service_info',return_value={'pid':111}),patch.object(manage,'child_processes',return_value=self.children),\
             patch.object(manage,'api',side_effect=[self.health,self.up,self.targets]):
            result = manage.inspect_services(self.bundle_root,self.bundle,8766,9095,scrape_after=time.time()+1)
        self.assertEqual(result['status'],'unavailable')

    def test_existing_counter_requires_a_new_increment(self):
        def result(value): return {'status':'success','data':{'resultType':'vector','result':[{'metric':{},'value':[1,value]}]}}
        with patch.object(manage,'api',side_effect=[result('7'),result('8')]) as api,patch.object(manage.time,'sleep'):
            self.assertEqual(manage.wait_scored_counter(9095,minimum=8)['data']['result'][0]['value'][1],'8')
            self.assertEqual(api.call_count,2)

    def test_foreign_registration_marker_is_rejected_before_stop(self):
        marker = sealed({'schemaVersion':'agat.decision.resident-registration.v1','ownerUid':os.getuid(),
                         'bundleSeal':'c'*64,'labels':list(manage.LABELS),'routingEnabled':False,'qualification':'not_assessed'})
        (self.bundle_root/'registration.json').write_text(canonical_json(marker))
        with self.assertRaisesRegex(RuntimeError,'Foreign'): manage.registered_ownership(self.bundle_root,self.bundle)

    def lifecycle(self,fail_second=False):
        request = self.root/'docs/qualification/local-decisions/request.example.json';request.parent.mkdir(parents=True)
        request.write_bytes((ROOT/'docs/qualification/local-decisions/request.example.json').read_bytes())
        jobs = {};commands = [];outputs = []
        def ctl(*arguments):
            commands.append(arguments)
            if arguments[0] == 'bootstrap':
                label = Path(arguments[2]).stem;jobs[label] = {'pid':111 if label == manage.LABELS[0] else 211}
                if fail_second and label == manage.LABELS[1]: raise KeyboardInterrupt('interrupted bootstrap')
            elif arguments[0] == 'bootout': jobs.pop(arguments[1].rsplit('/',1)[1],None)
            return SimpleNamespace(returncode=0)
        stack = contextlib.ExitStack();self.addCleanup(stack.close)
        def api(port,path,parameters=None):
            if port == 8766: return self.health
            if path == '/api/v1/targets':
                self.targets['data']['activeTargets'][0]['lastScrape'] = datetime.now(timezone.utc).isoformat()
                return self.targets
            return self.up
        patches = [patch.object(manage,'ROOT',self.root),patch.object(manage.platform,'system',return_value='Darwin'),
                   patch.object(manage,'source_identity',return_value=('a'*40,{'fixture':'a'*64})),
                   patch.object(manage,'validate_bundle',return_value=(self.bundle,8766,9095)),
                   patch.object(manage,'service_info',side_effect=lambda target:jobs.get(target.rsplit('/',1)[1])),
                   patch.object(manage,'owned_service_info',side_effect=lambda root,label:jobs.get(label)),
                   patch.object(manage,'launchctl',side_effect=ctl),patch.object(manage,'child_processes',return_value=self.children),
                   patch.object(manage,'wait_for_removal',side_effect=lambda target,path:target.rsplit('/',1)[1] not in jobs),
                   patch.object(manage,'gone',return_value=True),patch.object(manage.socket,'socket'),
                   patch.object(manage,'api',side_effect=api),patch.object(manage,'computed_counter',return_value=({'fixture':'zero'},0)),
                   patch.object(manage.OwnedRuntime,'score',return_value={'httpStatus':200,'result':{'status':'ok'}}),
                   patch.object(manage,'validate_result'),patch.object(manage,'wait_scored_counter',return_value={'fixture':'computed'})]
        for item in patches: stack.enter_context(item)
        gate = self.evidence()
        def run(action):
            output = self.root/'docs/private'/f'{action}-{len(outputs)}.json';outputs.append(output)
            code = manage.main([action,'--bundle',str(self.bundle_root),'--expected-seal',self.bundle['sha256'],
                                '--verification',str(gate),'--output',str(output)])
            return code,json.loads(output.read_text())
        return run,jobs,commands

    def test_check_has_no_registration_or_scoring_effect(self):
        run,jobs,commands = self.lifecycle()
        code,report = run('check')
        self.assertEqual(code,0);self.assertEqual(report['status'],'verified');self.assertEqual(jobs,{})
        self.assertEqual(commands,[]);self.assertFalse((self.bundle_root/'registration.json').exists())

    def test_interrupted_second_bootstrap_preserves_failure_and_removes_both_owned_jobs(self):
        run,jobs,commands = self.lifecycle(fail_second=True)
        code,report = run('install')
        self.assertEqual(code,1);self.assertEqual(report['status'],'failed');self.assertEqual(report['failure']['type'],'KeyboardInterrupt')
        self.assertIsNone(report['cleanupFailure']);self.assertTrue(all(report['result']['cleanup'].values()))
        self.assertEqual(jobs,{});self.assertEqual([c[1].rsplit('/',1)[1] for c in commands if c[0] == 'bootout'],list(reversed(manage.LABELS)))
        self.assertTrue(all(not p.exists() for _,p in manage.plist_paths(self.bundle_root)))

    def test_real_file_lifecycle_archives_marker_allows_reinstall_and_preserves_bundle(self):
        run,jobs,_ = self.lifecycle();self.assertEqual(run('install')[0],0)
        marker = json.loads((self.bundle_root/'registration.json').read_text());self.assertEqual(marker['observedPids'],[111,112,113,211])
        for _,path in manage.plist_paths(self.bundle_root): self.assertEqual(path.stat().st_mode & 0o777,0o600)
        with patch.object(manage,'api',side_effect=AssertionError('stop must not need HTTP')):
            code,report = run('stop')
        self.assertEqual(code,0);self.assertEqual(report['status'],'stopped');self.assertTrue(all(report['result']['cleanup'].values()))
        self.assertEqual(jobs,{});self.assertFalse((self.bundle_root/'registration.json').exists())
        self.assertEqual(len(list(self.bundle_root.glob('registration.stopped.*.json'))),1)
        self.assertTrue(all(source.exists() and not path.exists() for source,path in manage.plist_paths(self.bundle_root)))
        self.assertEqual(run('install')[0],0);self.assertEqual(set(jobs),set(manage.LABELS))

    def test_changed_installed_plist_stops_before_bootout(self):
        run,jobs,commands = self.lifecycle();self.assertEqual(run('install')[0],0)
        _,path = manage.plist_paths(self.bundle_root)[0];path.write_bytes(b'changed by owner')
        before = len(commands);code,report = run('stop')
        self.assertEqual(code,1);self.assertEqual(report['status'],'failed');self.assertEqual(len(commands),before)
        self.assertEqual(set(jobs),set(manage.LABELS));self.assertEqual(path.read_bytes(),b'changed by owner')


if __name__ == '__main__': unittest.main()
