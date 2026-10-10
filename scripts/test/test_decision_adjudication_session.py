from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from decision_runtime.annotations import finalize_reviews
from decision_runtime.artifacts import sealed
from scripts.lib.decision_adjudication_session import (
    interact_adjudication, validate_adjudication_progress, validate_handoff, verify_adjudication_values)
from scripts.lib.decision_review_adjudication import encoded, prepare_handoff
from scripts.lib.decision_review_pair import compare_pair
import scripts.test.test_decision_review_pair as pair_fixtures
import scripts.test.test_decision_blind_review as terminal_fixtures

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('adjudication_session_cli', ROOT / 'scripts/adjudicate-decision-reviews.py')
cli = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(cli)
sha = lambda raw: hashlib.sha256(raw).hexdigest()
reseal = lambda value: sealed({key: item for key, item in value.items() if key != 'sha256'})


class AdjudicationSessionTest(unittest.TestCase):
    def setUp(self):
        self.helper = pair_fixtures.ReviewPairTest(); self.helper.setUp(); self.addCleanup(self.helper.doCleanups)
        self.root = self.helper.root.resolve(); self.private = self.root / 'docs/private'; self.private.mkdir(parents=True)
        self.first = self.helper.binding('first', self.helper.a, answers=['yes', 'yes', 'yes'])
        self.second = self.helper.binding('second', self.helper.b, answers=['no', 'yes', 'no'])
        comparison = self.root / 'comparison.json'; comparison.write_bytes(encoded(compare_pair(self.first, self.second)))
        self.outputs = prepare_handoff(self.first, self.second, comparison, sha(comparison.read_bytes()))
        self.paths = {}
        for name, value in self.outputs.items():
            path = self.private / name; path.write_bytes(encoded(value)); self.paths[name] = path
        self.packet = self.outputs['packet.json']; self.blank = self.outputs['adjudication.review.blank.json']
        self.notes = self.outputs['case-notes.json']
        self.identity = ('a' * 40, {name: 'b' * 64 for name in cli.SOURCES})

    def handoff(self, packet=None, blank=None, notes=None):
        blank = blank if blank is not None else self.blank; notes = notes if notes is not None else self.notes
        return validate_handoff(packet if packet is not None else self.packet, blank, notes,
                                blank_raw=encoded(blank), notes_raw=encoded(notes))

    def repin(self, packet, blank, notes):
        packet = deepcopy(packet)
        packet['outputFiles'] = {name: {'sha256': sha(encoded(value)), 'bytes': len(encoded(value))}
                                for name, value in (('adjudication.review.blank.json', blank), ('case-notes.json', notes))}
        return reseal(packet)

    def invoke(self, answers, name='session', review=None, reviewer='fixture-adjudicator', tty=True, identity=None, pin_override=None):
        review = review or self.paths['adjudication.review.blank.json']; directory = self.private / name
        bindings = {'packet': self.paths['packet.json'], 'blank-review': self.paths['adjudication.review.blank.json'],
                    'case-notes': self.paths['case-notes.json'], 'review': review}
        args = ['--reviewer-id', reviewer, '--output-dir', str(directory)]
        for key, path in bindings.items():
            args += ['--' + key, str(path), '--' + key + '-file-sha256',
                     pin_override[1] if pin_override and pin_override[0] == key else sha(path.read_bytes())]
        output = terminal_fixtures.Terminal()
        with patch.object(cli, 'ROOT', self.root), patch.object(cli, 'source_identity',
            side_effect=identity, return_value=self.identity):
            code = cli.main(args, input_stream=terminal_fixtures.Terminal(answers) if tty else io.StringIO(answers),
                            output_stream=output)
        receipt = json.loads((directory / 'session.json').read_bytes()) if (directory / 'session.json').is_file() else None
        saved = json.loads((directory / 'review.json').read_bytes()) if (directory / 'review.json').is_file() else None
        return code, directory, receipt, saved, output.getvalue()

    def verify(self, receipt, initial, saved):
        return verify_adjudication_values(receipt, initial, saved, self.packet, self.blank,
            input_sha=sha(encoded(initial)), output_sha=sha(encoded(saved)),
            packet_sha=sha(self.paths['packet.json'].read_bytes()), blank_sha=sha(encoded(self.blank)), notes_sha=sha(encoded(self.notes)))

    def test_projected_tasks_and_checkpoints_preserve_whole_pool(self):
        self.handoff(); review = deepcopy(self.blank); review['reviewerId'] = 'fixture-adjudicator'
        output = terminal_fixtures.Terminal(); checkpoints = []
        saved, reason = interact_adjudication(review, self.packet, self.notes,
            terminal_fixtures.Terminal('1\nSynthetic first reason\ny\n2\nSynthetic second reason\ny\n:submit\ny\n'),
            output, checkpoints.append)
        self.assertEqual(reason, 'submitted'); self.assertEqual(len(checkpoints), 2)
        for value in (*checkpoints, saved):
            self.assertEqual(value['pool'], self.blank['pool']); self.assertEqual(value['poolSha256'], self.blank['poolSha256'])
            self.assertEqual([item['id'] for item in value['labels']], ['fixture-0', 'fixture-2'])
        self.assertIn('Task 2/2: fixture-2', output.getvalue()); self.assertNotIn('Task 2/2: fixture-1', output.getvalue())
        self.assertIn('FIRST RATIONALE:', output.getvalue()); self.assertIn('SECOND RATIONALE:', output.getvalue())

    def test_partial_resume_has_net_counts_and_explicit_submission(self):
        code, directory, receipt, draft, _ = self.invoke('1\nSynthetic first reason\ny\n:quit\n')
        self.assertEqual(code, 2); self.assertEqual((receipt['newAnswers'], receipt['remainingAnswers']), (1, 1))
        self.assertIsNone(draft['reviewedAt']); self.assertFalse(self.verify(receipt, self.blank, draft)['completeAdjudicationArtifact'])
        code, final_dir, receipt, final, text = self.invoke('2\nSynthetic second reason\ny\n:submit\ny\n', name='resume', review=directory/'review.json')
        self.assertEqual(code, 0); self.assertEqual((receipt['existingAnswers'], receipt['newAnswers']), (1, 1))
        self.assertNotIn('Task 1/2:', text); self.assertEqual(final['labels'][0], draft['labels'][0])
        self.assertTrue(self.verify(receipt, draft, final)['completeAdjudicationArtifact'])
        self.assertEqual(final_dir.stat().st_mode & 0o777, 0o700)
        for path in final_dir.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_complete_quit_is_editable_and_cannot_finalize(self):
        code, directory, receipt, draft, _ = self.invoke('1\nSynthetic A\ny\n2\nSynthetic B\ny\n:quit\n')
        self.assertEqual(code, 2); self.assertEqual(receipt['remainingAnswers'], 0)
        verification = self.verify(receipt, self.blank, draft)
        self.assertTrue(verification['allAnswersPresent']); self.assertFalse(verification['completeAdjudicationArtifact'])
        with self.assertRaises(ValueError): finalize_reviews(json.loads(self.first[4].read_bytes()), json.loads(self.second[4].read_bytes()), draft)
        code, _, receipt, saved, _ = self.invoke(':submit\ny\n', name='submit-full', review=directory/'review.json')
        self.assertEqual(code, 0); self.assertTrue(self.verify(receipt, draft, saved)['completeAdjudicationArtifact'])

    def test_final_submitted_subset_interoperates_with_finalize_on_synthetic_fixture(self):
        _, _, receipt, final, _ = self.invoke('1\nSynthetic A\ny\n2\nSynthetic B\ny\n:submit\ny\n')
        self.assertTrue(self.verify(receipt, self.blank, final)['completeAdjudicationArtifact'])
        dataset = finalize_reviews(json.loads(self.first[4].read_bytes()), json.loads(self.second[4].read_bytes()), final)
        self.assertEqual(len(dataset['cases']), 3); self.assertEqual(final['pool'], self.blank['pool'])
        for key in ('humanExecutionVerified', 'reviewerIdentityVerified', 'independentReviewVerified', 'expertQualificationsVerified', 'routingEnabled'):
            self.assertIs(receipt[key], False)

    def test_edit_and_clear_are_saved_without_default_labels(self):
        _, directory, _, draft, _ = self.invoke('1\nSynthetic A\ny\n2\nSynthetic B\ny\n:quit\n')
        code, _, receipt, cleared, _ = self.invoke(':clear 1\ny\n:quit\n', name='cleared', review=directory/'review.json')
        self.assertEqual(code, 2); self.assertEqual(receipt['clearedAnswers'], 1)
        self.assertIsNone(cleared['labels'][0]['expectedOptionId']); self.assertIsNone(cleared['labels'][0]['rationale'])
        code, _, receipt, revised, _ = self.invoke(':edit 2\n1\nSynthetic corrected B\ny\n:quit\n', name='edited', review=directory/'review.json')
        self.assertEqual(code, 2); self.assertEqual(receipt['revisedAnswers'], 1)
        self.assertEqual(revised['labels'][0], draft['labels'][0]); self.verify(receipt, draft, revised)

    def test_prior_rationale_controls_are_literal_and_declined_choice_is_unsaved(self):
        notes = deepcopy(self.notes); notes['cases'][0]['firstRationale'] = 'Synthetic\x1b[2J\u202e'
        packet = self.repin(self.packet, self.blank, notes); self.handoff(packet=packet, notes=notes)
        output = terminal_fixtures.Terminal(); review = deepcopy(self.blank); review['reviewerId'] = 'fixture-adjudicator'
        saved, _ = interact_adjudication(review, packet, notes,
            terminal_fixtures.Terminal('1\nSynthetic decline\nn\n:quit\n'), output,
            lambda value: self.fail('Declined answer must not be saved'))
        self.assertNotIn('\x1b', output.getvalue()); self.assertNotIn('\u202e', output.getvalue())
        self.assertIn('Synthetic\\x1b[2J\\u202e', output.getvalue())
        self.assertTrue(all(item['expectedOptionId'] is None for item in saved['labels']))

    def test_original_reviewers_bad_pins_and_nonterminal_refuse_before_output(self):
        for index, reviewer in enumerate(self.packet['declaredReviewerIds']):
            code, directory, _, _, text = self.invoke('', name='reviewer-'+str(index), reviewer=reviewer)
            self.assertEqual(code, 1); self.assertFalse(directory.exists()); self.assertFalse(text)
        for key in ('packet', 'blank-review', 'case-notes', 'review'):
            code, directory, _, _, text = self.invoke('', name='pin-'+key, pin_override=(key, '0'*64))
            self.assertEqual(code, 1); self.assertFalse(directory.exists()); self.assertFalse(text)
        code, directory, _, _, _ = self.invoke('', name='nonterminal', tty=False)
        self.assertEqual(code, 1); self.assertFalse(directory.exists())

    def test_resume_refuses_wrong_pool_inventory_ownership_or_submission_stamp(self):
        mutations = [lambda value: value['pool']['cases'][1]['request'].update(state='Changed undisputed source'),
                     lambda value: value['labels'].reverse(), lambda value: value['labels'].append(deepcopy(value['labels'][0])),
                     lambda value: value.update(reviewerId='someone-else'), lambda value: value.update(reviewedAt='2026-10-10T10:00:00Z')]
        for mutate in mutations:
            review = deepcopy(self.blank); mutate(review)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                validate_adjudication_progress(review, 'fixture-adjudicator', self.packet, self.blank)

    def test_resealed_packet_cannot_grant_authority_or_forge_case_counts(self):
        for key, value in (('humanExecutionVerified', True), ('ownersAppointed', True), ('resolvedCases', 1),
                           ('sourceCaseCount', True), ('disputedCaseCount', 3), ('disputedGroupCount', 99), ('adjudicatorId', 'appointed')):
            packet = deepcopy(self.packet); packet[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.handoff(packet=reseal(packet))

    def test_resealed_notes_and_blank_semantic_changes_are_refused(self):
        notes = deepcopy(self.notes); notes['cases'][0]['secondOptionId'] = notes['cases'][0]['firstOptionId']
        with self.assertRaises(ValueError): self.handoff(packet=self.repin(self.packet, self.blank, notes), notes=notes)
        notes = deepcopy(self.notes); notes['cases'][0]['case']['request']['state'] = 'Changed prior source'
        with self.assertRaises(ValueError): self.handoff(packet=self.repin(self.packet, self.blank, notes), notes=notes)
        blank = deepcopy(self.blank); blank['labels'][0].update(expectedOptionId='yes', rationale='Prefilled synthetic decision')
        with self.assertRaises(ValueError): self.handoff(packet=self.repin(self.packet, blank, self.notes), blank=blank)

    def test_handoff_bytes_are_canonical_and_match_output_pins(self):
        with self.assertRaises(ValueError):
            validate_handoff(self.packet, self.blank, self.notes, blank_raw=encoded(self.blank)+b' ', notes_raw=encoded(self.notes))
        packet = deepcopy(self.packet); packet['disputedCaseIds'].reverse()
        with self.assertRaises(ValueError): self.handoff(packet=reseal(packet))

    def test_changed_input_keeps_last_checkpoint_and_produces_failed_receipt(self):
        def changed(review, packet, notes, stream, output, save):
            review['labels'][0].update(expectedOptionId='yes', rationale='Synthetic checkpoint')
            save(review); self.paths['case-notes.json'].write_bytes(self.paths['case-notes.json'].read_bytes()+b' ')
            return review, 'quit'
        with patch.object(cli, 'interact_adjudication', side_effect=changed):
            code, _, receipt, saved, _ = self.invoke('', name='changed-input')
        self.assertEqual(code, 1); self.assertEqual(receipt['status'], 'failed')
        self.assertIsNone(saved['reviewedAt']); self.assertEqual(saved['labels'][0]['expectedOptionId'], 'yes')
        self.assertIsNone(receipt['remainingAnswers']); self.assertFalse(receipt['submissionConfirmed'])

    def test_changed_sources_before_checkpoint_save_no_answer(self):
        changed_identity = ('a'*40, {**self.identity[1], 'scripts/adjudicate-decision-reviews.py': 'c'*64})
        code, _, receipt, saved, _ = self.invoke('1\nSynthetic A\ny\n', name='changed-source', identity=[self.identity, changed_identity])
        self.assertEqual(code, 1); self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(all(item['expectedOptionId'] is None for item in saved['labels']))
        self.assertFalse(self.verify(receipt, self.blank, saved)['completeAdjudicationArtifact'])

    def test_checkpoint_failure_preserves_previous_answers_and_is_not_submission(self):
        original_checkpoint = cli.checkpoint; calls = []
        def interrupted(directory, review):
            calls.append(1)
            if len(calls) > 1: raise OSError('Synthetic checkpoint interruption')
            return original_checkpoint(directory, review)
        with patch.object(cli, 'checkpoint', side_effect=interrupted):
            code, _, receipt, saved, _ = self.invoke('1\nSynthetic A\ny\n', name='failed-checkpoint')
        self.assertEqual(code, 1); self.assertEqual(receipt['failureType'], 'OSError')
        self.assertTrue(all(item['expectedOptionId'] is None for item in saved['labels']))
        self.assertFalse(self.verify(receipt, self.blank, saved)['completeAdjudicationArtifact'])

    def test_saved_receipt_counts_timestamps_pins_and_authority_are_rechecked(self):
        _, _, receipt, saved, _ = self.invoke('1\nSynthetic A\ny\n2\nSynthetic B\ny\n:submit\ny\n')
        self.verify(receipt, self.blank, saved)
        for key, value in (('newAnswers', 0), ('remainingAnswers', True), ('submissionConfirmed', False),
                           ('packetFileSha256', '0'*64), ('handoffSourcesRevalidated', True),
                           ('humanExecutionVerified', True), ('finishedAt', '2020-01-01T00:00:00Z')):
            changed = deepcopy(receipt); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify(reseal(changed), self.blank, saved)

    def test_existing_output_directory_is_never_reused(self):
        _, directory, _, _, _ = self.invoke(':quit\n')
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        self.assertEqual(self.invoke('anything')[0], 1)
        self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})


if __name__ == '__main__': unittest.main()
