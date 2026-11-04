"""Real review inputs stay whole and blank; synthetic labels are test fixtures only."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_runtime.annotations import finalize_reviews
from decision_runtime.artifacts import sealed
from decision_runtime.contracts import Request
from scripts.lib import decision_public_development_review as review
from scripts.lib.decision_blind_review import task_text, validate_progress
from scripts.test.test_decision_public_load import context_fixture


def reseal(value):
    return sealed({key: item for key, item in value.items() if key != 'sha256'})


class ReviewProjectionTests(unittest.TestCase):
    def setUp(self):
        self.context = context_fixture()

    def outputs(self):
        return review.review_outputs(self.context)

    def test_every_case_full_request_and_attribution_remains_in_original_order(self):
        pool = self.outputs()['pool.json']
        self.assertEqual(len(pool['cases']), len(self.context['inputs']))
        for case, original in zip(pool['cases'], self.context['inputs']):
            self.assertEqual(case['request'], original['request'])
            self.assertEqual(case['provenance'], original['provenance'])
            self.assertEqual(case['groupId'], original['groupId'])
            self.assertEqual(Request.from_dict(case['request']).input_sha256, original['inputSha256'])

    def test_over_limit_case_is_neither_filtered_nor_clipped(self):
        original = copy.deepcopy(self.context)
        original['inputs'][-1]['request']['state'] = 'LONG SYNTHETIC TEST INPUT\n' + 'word ' * 4000
        original['inputs'][-1]['inputSha256'] = Request.from_dict(original['inputs'][-1]['request']).input_sha256
        self.context = reseal(original)
        case = self.outputs()['pool.json']['cases'][-1]
        self.assertFalse(self.context['inputs'][-1]['contextEligible'])
        self.assertEqual(case['request']['state'], self.context['inputs'][-1]['request']['state'])
        self.assertIn(case['request']['state'], task_text(case, 1, 1))

    def test_both_reviewers_start_with_identical_blank_allowed_option_tasks(self):
        outputs = self.outputs(); first = outputs['review.first.blank.json']
        self.assertEqual(first, outputs['review.second.blank.json'])
        self.assertIsNone(first['reviewerId']); self.assertIsNone(first['reviewedAt'])
        self.assertTrue(all(x['expectedOptionId'] is None and x['rationale'] is None for x in first['labels']))
        self.assertEqual(validate_progress(first, 'synthetic-reviewer'), first)

    def test_blank_packets_cannot_finalize_into_reference_labels(self):
        outputs = self.outputs()
        with self.assertRaises(ValueError):
            finalize_reviews(outputs['review.first.blank.json'], outputs['review.second.blank.json'])

    def test_existing_terminal_hides_group_seed_and_model_metadata(self):
        case = self.outputs()['pool.json']['cases'][0]; rendered = task_text(case, 1, 6)
        self.assertNotIn(case['groupId'], rendered); self.assertNotIn(self.context['splitSeed'], rendered)
        self.assertNotIn('inputTokens', rendered); self.assertNotIn('contextEligible', rendered)
        self.assertEqual(set(case), {'id', 'family', 'groupId', 'provenance', 'request'})
        self.assertEqual({o['id'] for o in case['request']['options']}, review.OPTIONS)

    def test_projection_does_not_share_mutable_inputs_or_reviewer_labels(self):
        outputs = self.outputs(); outputs['review.first.blank.json']['labels'][0]['expectedOptionId'] = 'other'
        outputs['pool.json']['cases'][0]['request']['state'] = 'changed test fixture'
        self.assertIsNone(outputs['review.second.blank.json']['labels'][0]['expectedOptionId'])
        self.assertNotEqual(outputs['pool.json']['cases'][0]['request']['state'], self.context['inputs'][0]['request']['state'])

    def test_unsupported_rubric_and_synthetic_public_provenance_fail(self):
        for mutation in (lambda c: c['inputs'][0]['request']['options'][0].update(id='different'),
                         lambda c: c['inputs'][0]['provenance'].update(kind='synthetic')):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(self.context); mutation(changed)
                changed['inputs'][0]['inputSha256'] = Request.from_dict(changed['inputs'][0]['request']).input_sha256
                with self.assertRaises(ValueError): review.review_outputs(reseal(changed))

    def test_resealed_context_cannot_change_split_counts_inputs_or_claim_labels(self):
        for mutation in (lambda c: c.update(selectedSplit='holdout'), lambda c: c['inputs'].pop(),
                         lambda c: c.update(referenceLabels=1), lambda c: c.update(predictions=1),
                         lambda c: c['inputs'][0].update(inputSha256='0' * 64)):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(self.context); mutation(changed)
                with self.assertRaises(ValueError): review.review_outputs(reseal(changed))


class ReviewPacketVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.directory = self.root / 'docs/private/packet'
        self.directory.mkdir(parents=True); self.path = self.directory / 'packet.json'
        self.context = context_fixture(); self.outputs = review.review_outputs(self.context)
        self.context_input = {'path': 'docs/private/context.json', 'sha256': 'c' * 64}
        self.patches = [patch.object(review, 'sources_at', return_value={'fixture.py': b'committed fixture'}),
            patch.object(review, 'reconstruct', return_value=(self.context, self.outputs, 40)),
            patch('socket.create_connection', side_effect=AssertionError('Review projection must not connect'))]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)
        self.packet, outputs = review.create_packet(self.root, self.context_input, 'a' * 40, {'fixture.py': 'b' * 64})
        for name, value in outputs.items(): (self.directory / name).write_bytes(review.encoded(value))

    def verify(self, mutation=None, resealed=True):
        value = copy.deepcopy(self.packet)
        if mutation: mutation(value)
        if resealed: value = reseal(value)
        raw = review.encoded(value); self.path.write_bytes(raw)
        return review.verify_packet(self.root, self.path, hashlib.sha256(raw).hexdigest())

    def test_original_packet_reconstructs_full_inventory_without_model_calls(self):
        receipt = self.verify(); self.assertEqual(receipt['status'], 'pass')
        self.assertEqual(receipt['inventory']['cases'], 6); self.assertEqual(receipt['modelCallsDuringVerification'], 0)
        for name, value in review.FLAGS.items():
            self.assertIs(type(receipt[name]), type(value)); self.assertEqual(receipt[name], value)

    def test_pinned_context_source_and_packet_source_are_both_checked(self):
        with patch.object(review, 'sources_at', side_effect=ValueError('Changed source')):
            with self.assertRaises(ValueError): self.verify()
        # Exercise the actual reconstruction path rather than the packet test projection mock.
        self.patches[1].stop()
        with patch.object(review, 'pinned_input', return_value=review.encoded(self.context)), \
             patch.object(review, 'sources_at', side_effect=ValueError('Changed historical context source')):
            with self.assertRaises(ValueError): review.reconstruct(self.root, self.context_input)

    def test_posthoc_counts_case_order_and_binding_changes_are_rejected(self):
        for mutation in (lambda p: p['inventory'].update(cases=5),
                         lambda p: p['inventory']['caseBindings'].reverse(),
                         lambda p: p['inventory']['caseBindings'][0].update(inputSha256='0' * 64),
                         lambda p: p.update(verifiedContextSourceFiles=True)):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.verify(mutation)

    def test_added_model_outcomes_and_nonblank_labels_cannot_be_resealed_into_packet(self):
        name = 'review.first.blank.json'; changed = copy.deepcopy(self.outputs[name])
        changed['labels'][0].update(expectedOptionId='other', rationale='Synthetic fixture, never human gold')
        raw = review.encoded(changed); (self.directory / name).write_bytes(raw)
        with self.assertRaises(ValueError):
            self.verify(lambda p: p['artifactFileSha256'].update({name: hashlib.sha256(raw).hexdigest()}))
        (self.directory / name).write_bytes(review.encoded(self.outputs[name]))
        with self.assertRaises(ValueError): self.verify(lambda p: p.update(observedOutcome='ok'))

    def test_missing_extra_and_symlinked_review_files_fail_closed(self):
        first = self.directory / 'review.first.blank.json'; raw = first.read_bytes(); first.unlink()
        with self.assertRaises(ValueError): self.verify()
        first.write_bytes(raw); extra = self.directory / 'model-output.json'; extra.write_text('{}')
        with self.assertRaises(ValueError): self.verify()
        extra.unlink(); first.unlink(); target = self.root / 'copy.json'; target.write_bytes(raw); first.symlink_to(target)
        with self.assertRaises(ValueError): self.verify()

    def test_altered_packet_bytes_and_unverified_human_authority_are_rejected(self):
        for field in ('humanExecutionVerified', 'independentReviewVerified', 'expertQualificationsVerified', 'routingEnabled'):
            with self.subTest(field=field), self.assertRaises(ValueError): self.verify(lambda p: p.update({field: True}))
        with self.assertRaises(ValueError): self.verify(lambda p: p.update(referenceLabels=True))
        with self.assertRaises(ValueError): self.verify(lambda p: p['protocol'].update(predictionArtifactsConsumed=True))
        with self.assertRaises(ValueError): self.verify(lambda p: p.update(status='completed'))
        with self.assertRaises(ValueError): self.verify(lambda p: p['inventory'].update(cases=5), resealed=False)

    def test_source_receipt_file_sha_prevents_tampering_before_projection(self):
        raw = review.encoded(self.packet); self.path.write_bytes(raw)
        with self.assertRaises(ValueError): review.verify_packet(self.root, self.path, '0' * 64)

    def test_symlinked_packet_receipt_is_rejected_before_projection(self):
        raw = review.encoded(self.packet); target = self.root / 'outside-packet.json'; target.write_bytes(raw)
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            review.verify_packet(self.root, self.path, hashlib.sha256(raw).hexdigest())

    def test_context_symlink_and_wrong_file_pin_are_rejected_before_source_replay(self):
        self.patches[1].stop()
        raw = review.encoded(self.context); target = self.root / 'original-context.json'; target.write_bytes(raw)
        path = self.root / self.context_input['path']; path.symlink_to(target)
        with self.assertRaises(ValueError): review.reconstruct(self.root, self.context_input)
        path.unlink(); path.write_bytes(raw)
        with self.assertRaises(ValueError): review.reconstruct(self.root, self.context_input)


if __name__ == '__main__':
    unittest.main()
