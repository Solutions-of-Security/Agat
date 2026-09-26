import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_runtime.__main__ import main
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Policy
from decision_runtime.engine import DecisionEngine
from decision_runtime.evaluation_guard import validate_evaluation_start
from decision_runtime.tests.test_calibration import FixtureBackend, pipeline


class EvaluationGuardTest(unittest.TestCase):
    def test_missing_plan_wrong_split_and_fitted_logits_fail_before_loading_weights(self):
        data, plan, _, fit, _ = pipeline()
        for split, p, a, reverse, calibrated in [
                ("calibration", None, None, False, False), ("holdout", None, None, True, False),
                ("development", plan, None, False, False), ("calibration", plan, fit, False, False),
                ("calibration", plan, None, False, True), ("holdout", plan, None, True, False),
                ("holdout", plan, fit, False, False)]:
            with self.subTest(split=split, reverse=reverse), self.assertRaises(ValueError):
                validate_evaluation_start(data, split, p, a, reverse_options=reverse, calibrated=calibrated)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data.json').write_text(json.dumps(data))
            args = ['decision_runtime', 'evaluate', '--manifest', str(root/'missing.json'),
                    '--dataset', str(root/'data.json'), '--split', 'holdout', '--output', str(root/'out.json')]
            with patch.object(sys, 'argv', args), patch('decision_runtime.mlx_backend.MlxBackend') as backend, \
                 contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main(), 1)
            backend.assert_not_called()
            self.assertIn('--plan before inference', errors.getvalue())
            self.assertFalse((root/'out.json').exists())

    def test_legacy_future_plan_and_changed_fit_provenance_are_rejected(self):
        data, plan, _, fit, _ = pipeline()
        for mutate in (lambda p: p.update(schemaVersion='agat.decision.experiment.v1'),
                       lambda p: p.update(createdAt='9999-01-01T00:00:00+00:00')):
            p = {k: copy.deepcopy(v) for k, v in plan.items() if k != 'sha256'}
            mutate(p)
            if p['schemaVersion'].endswith('v1'):
                del p['executionProfile']; del p['executionProfileSha256']
            with self.assertRaises(ValueError):
                validate_evaluation_start(data, 'calibration', sealed(p), None, reverse_options=False, calibrated=False)
        for mutate in (lambda a: a.update(planSha256='f'*64),
                       lambda a: a.update(fitCaseIds=[]), lambda a: a.update(fitGroupIds=[]),
                       lambda a: a.update(createdAt='9999-01-01T00:00:00+00:00'),
                       lambda a: a.update(createdAt='2000-01-01T00:00:00+00:00'),
                       lambda a: a['model'].update(maxInputTokens=4096)):
            a = {k: copy.deepcopy(v) for k, v in fit.items() if k != 'sha256'}; mutate(a)
            with self.assertRaises(ValueError):
                validate_evaluation_start(data, 'holdout', plan, sealed(a), reverse_options=True, calibrated=False)

    def test_cli_mismatched_profile_never_calls_backend_score_or_writes_results(self):
        data, plan, _, fit, _ = pipeline()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in [('data', data), ('plan', plan), ('fit', fit)]:
                (root/(name+'.json')).write_text(json.dumps(value))
            backend = FixtureBackend()
            backend.identity = {**backend.identity, 'maxInputTokens': 4096}
            for split in ('calibration', 'holdout'):
                args = ['decision_runtime', 'evaluate', '--manifest', 'fixture', '--dataset', str(root/'data.json'),
                        '--plan', str(root/'plan.json'), '--split', split, '--output', str(root/'out.json')]
                if split == 'holdout': args += ['--frozen-calibration', str(root/'fit.json'), '--reverse-options']
                # Match the fixture's frozen policy so the only mismatch is execution identity.
                (root/'policy.json').write_text(json.dumps(plan['policy']))
                args += ['--policy', str(root/'policy.json')]
                with patch.object(sys, 'argv', args), patch('decision_runtime.mlx_backend.MlxBackend', return_value=backend), \
                     patch.object(backend, 'score', side_effect=AssertionError('holdout consumed')) as score, \
                     contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(main(), 1)
                score.assert_not_called()
                self.assertIn('no evaluation inputs were scored', errors.getvalue())
                self.assertFalse((root/'out.json').exists())


if __name__ == '__main__': unittest.main()
