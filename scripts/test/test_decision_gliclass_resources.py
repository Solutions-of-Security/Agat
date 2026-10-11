import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.artifacts import sealed
from scripts.lib import decision_gliclass_resources as resources
from scripts.lib import decision_gliclass_baseline as baseline
from scripts.test import test_decision_gliclass_baseline as fixtures


def samples(platform='darwin'):
    multiplier=1 if platform=='darwin' else 1024
    return [{'phase':phase,'pid':111,'platform':platform,'capturedAt':'2026-10-11T00:00:00+00:00',
        'monotonicBeforeNs':(index+1)*1_000_000_000,'monotonicAfterNs':(index+1)*1_000_000_000+1000,
        'rawMaxRss':(index+1)*1024,'rawMaxRssToBytesMultiplier':multiplier,'maxRssBytes':(index+1)*1024*multiplier,
        'userCpuSeconds':index*.5,'systemCpuSeconds':index*.1,
        'minorFaults':index,'majorFaults':0,'voluntaryContextSwitches':index,'involuntaryContextSwitches':index}
        for index,phase in enumerate(resources.PHASES)]


def report(identity=None):
    identity=identity or {'sourceCommit':'1'*40,'sourceFiles':{'fixture.py':'2'*64}}
    captured=iter(samples())
    return resources.measure(fixtures.dataset(),Path('/synthetic-model'),identity,
        backend_factory=lambda path:fixtures.backend(),capture=lambda phase:next(captured))


def reseal(value):return sealed({key:item for key,item in value.items()if key!='sha256'})


class GLiClassResourcesTest(unittest.TestCase):
    def test_platform_units_cumulative_high_water_and_cpu_deltas(self):
        for platform,multiplier in [('darwin',1),('linux',1024)]:
            summary=resources.summarize(samples(platform))
            self.assertEqual(summary['maxRssBytesAfterWholeScoring'],3072*multiplier)
            self.assertEqual(summary['phases']['load-and-scoring']['maxRssHighWaterIncreaseBytes'],2048*multiplier)
            self.assertEqual(summary['phases']['load-and-scoring']['userCpuSecondsDelta'],1.0)
            self.assertAlmostEqual(summary['phases']['load-and-scoring']['wallTimeLowerMs'],1999.999)
            self.assertAlmostEqual(summary['phases']['load-and-scoring']['wallTimeUpperMs'],2000.001)
        with self.assertRaises(ValueError):resources.rss_multiplier('unsupported')

    def test_wrong_conversion_pid_order_and_backward_or_nonfinite_counter_are_refused(self):
        mutations=[lambda s:s[1].update(rawMaxRssToBytesMultiplier=1024),lambda s:s[1].update(pid=112),
            lambda s:s.reverse(),lambda s:s[1].update(userCpuSeconds=-1),lambda s:s[1].update(userCpuSeconds=float('nan')),
            lambda s:s[2].update(rawMaxRss=1,maxRssBytes=1),lambda s:s[1].update(minorFaults=True),
            lambda s:s[1].update(monotonicBeforeNs=0),lambda s:s[1].update(capturedAt='2026-10-11T00:00:00')]
        for mutation in mutations:
            changed=samples();mutation(changed)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):resources.summarize(changed)

    def test_whole_native_baseline_remains_typed_and_scope_bound(self):
        measured=report()
        self.assertEqual(measured['baseline']['summary']['nativeInferenceAttempts'],6)
        self.assertIs(measured['baseline']['evaluation']['cases'][1]['result']['value'],False)
        self.assertEqual(resources.verify_profile(fixtures.dataset(),measured),measured)

    def test_resealed_resource_summary_or_human_authority_cannot_be_promoted(self):
        original=report()
        mutations=[lambda r:r['resourceSummary'].update(maxRssBytesAfterWholeScoring=999),
            lambda r:r.update(routingEnabled=True),lambda r:r.update(humanLabelsVerified=0),
            lambda r:r['resourceSamples'].pop(),lambda r:r['sourceIdentity'].update(sourceCommit='9'*40),
            lambda r:r['resourceSamples'][0].update(maxRssBytes=1024*1024)]
        for mutation in mutations:
            changed=copy.deepcopy(original);mutation(changed)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):resources.verify_profile(fixtures.dataset(),reseal(changed))

    def test_model_is_not_constructed_for_training_calibration_or_holdout(self):
        for split in ('train','calibration','holdout'):
            data=fixtures.dataset()
            for case in data['cases']:case['split']=split
            calls=[]
            with self.assertRaises(ValueError):resources.measure(data,Path('/unused'),{},backend_factory=lambda path:calls.append(path))
            self.assertEqual(calls,[])

    def test_nested_native_mutation_is_rejected_even_when_both_reports_are_resealed(self):
        changed=report();changed['baseline']['evaluation']['cases'][1]['result']['value']=0
        changed['baseline']=reseal(changed['baseline'])
        with self.assertRaises(ValueError):resources.verify_profile(fixtures.dataset(),reseal(changed))

    def test_extra_missing_or_numeric_boolean_resource_field_is_refused(self):
        for mutation in (lambda s:s[0].update(unknown=1),lambda s:s[0].pop('rawMaxRss'),lambda s:s[0].update(pid=True)):
            changed=samples();mutation(changed)
            with self.assertRaises(ValueError):resources.summarize(changed)

    def test_real_cli_decodes_pinned_profile_and_replays_without_optional_model_import(self):
        spec=importlib.util.spec_from_file_location('gliclass_resource_cli_unit_fixture',baseline.ROOT/'scripts/profile-gliclass-development.py')
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory()as temporary:
            root=Path(temporary);source_raw=b'synthetic resource CLI source fixture'
            identity={'sourceCommit':'1'*40,'sourceFiles':{'fixture.py':hashlib.sha256(source_raw).hexdigest()}}
            data=root/'dataset.json';data.write_text(json.dumps(fixtures.dataset()))
            output,verified=root/'docs/private/measured',root/'docs/private/verified'
            args=['--model-dir',str(root/'fake-model'),'--dataset',str(data),'--dataset-file-sha256',hashlib.sha256(data.read_bytes()).hexdigest()]
            with patch.object(cli,'ROOT',root),patch.object(cli,'frozen_sources',return_value=identity), \
                 patch.object(cli,'verify_local',return_value={'sha256':'3'*64}),patch.object(cli,'measure',return_value=report(identity)):
                self.assertEqual(cli.main(['measure',*args,'--output-dir',str(output)]),0)
                path=output/'report.json';raw=path.read_bytes()
                with patch.object(cli.subprocess,'check_output',return_value=source_raw):
                    self.assertEqual(cli.main(['verify',*args,'--report',str(path),'--report-file-sha256',hashlib.sha256(raw).hexdigest(),
                                               '--output-dir',str(verified)]),0)
                self.assertEqual((verified/'verified-report.json').read_bytes(),raw)
                self.assertEqual(json.loads((verified/'execution.json').read_bytes())['modelCalls'],0)


if __name__=='__main__':unittest.main()
