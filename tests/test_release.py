import copy
import json
import sys
import unittest
from pathlib import Path
from geotask19.metrics import evaluate,load_jsonl
from geotask19.runner import make_request,call_command
from geotask19.validate import validate

ROOT=Path(__file__).resolve().parents[1]

class FinalReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=load_jsonl(ROOT/'data/benchmark.jsonl')
        cls.taxonomy=json.loads((ROOT/'data/taxonomy.json').read_text(encoding='utf-8'))

    def test_whole_release(self):
        result=validate(ROOT)
        self.assertEqual(result['evaluable_samples'],277)
        self.assertEqual(result['r15_mapped_to'],'M1 / O05')

    def test_r15_only_actual_upb_is_positive(self):
        row=next(r for r in self.rows if r['task_id']=='G19-0161')
        self.assertEqual(row['reference']['class_ids'],['M1'])
        self.assertEqual([(o['o_id'],o['class_ids']) for o in row['reference']['O']],[('O05',['M1'])])
        self.assertNotIn('S1',row['reference']['class_ids'])
        self.assertEqual(row['reference']['omitted_unscored_o_count'],5)

    def test_current_expert_review_status_is_recorded_without_invented_statistics(self):
        review=json.loads((ROOT/'data/expert_review_status.json').read_text(encoding='utf-8'))
        self.assertEqual(review['review_status'],'expert_reviewed_gold')
        self.assertEqual(review['evidence_type'],'maintainer_confirmation_in_conversation')
        self.assertIsNone(review['reviewer_identities'])
        self.assertIsNone(review['agreement_statistics'])
        self.assertTrue(review['historical_extraction_fields_preserved'])
        self.assertFalse(review['sample_inputs_and_reference_label_sets_changed'])

    def test_no_other_scope_rejected_case_is_in_dataset(self):
        decision=json.loads((ROOT/'data/expert_scope_decision.json').read_text(encoding='utf-8'))
        ids={r['task_id'] for r in self.rows}
        self.assertEqual(len(decision['excluded_input_complete_task_ids']),22)
        self.assertFalse(ids&set(decision['excluded_input_complete_task_ids']))

    def test_definition_is_common_not_task_answer(self):
        spaces=[make_request(row,self.taxonomy)['label_space'] for row in self.rows]
        self.assertTrue(all(space==spaces[0] for space in spaces))
        text=json.dumps(spaces[0],ensure_ascii=False)
        for forbidden in ('G19-0161','R15','O05'):self.assertNotIn(forbidden,text)
        self.assertIn('U–Pb',text)

    def test_all_sample_request_isolation(self):
        for row in self.rows:
            expected=make_request(row,self.taxonomy)
            mutated=copy.deepcopy(row)
            mutated['reference']={'secret':'FORBIDDEN_CANARY'}
            mutated['source']={'secret':'FORBIDDEN_CANARY'}
            mutated['expert_mapping']={'secret':'FORBIDDEN_CANARY'}
            for field in mutated['input'].values():field['evidence']=[{'secret':'FORBIDDEN_CANARY'}]
            self.assertEqual(expected,make_request(mutated,self.taxonomy))
            self.assertNotIn('FORBIDDEN_CANARY',json.dumps(expected))

    def test_protocol_fixture_and_metric_sanity_not_model_result(self):
        responses=[]
        for task in (self.rows[0],next(r for r in self.rows if r['task_id']=='G19-0161')):
            request=make_request(task,self.taxonomy)
            response=call_command([sys.executable,str(ROOT/'examples/empty_harness.py')],request,10)
            self.assertEqual(response['labels'],[]);responses.append(response)
        subset=[self.rows[0],next(r for r in self.rows if r['task_id']=='G19-0161')]
        report=evaluate(subset,responses)
        self.assertEqual(report['micro']['recall'],0)
        self.assertEqual(report['n_samples'],2)
        # An oracle below is a deterministic scorer self-check, NOT an LLM result.
        oracle=[{'task_id':r['task_id'],'labels':r['reference']['class_ids']} for r in self.rows]
        oracle_report=evaluate(self.rows,oracle)
        self.assertEqual(oracle_report['subset_accuracy'],1)
        for group in [oracle_report['overall'],*oracle_report['per_process'].values()]:
            self.assertEqual(group['n_samples'],277)
            self.assertEqual(group['acc'],1)
            self.assertEqual(group['precision'],1)
            self.assertEqual(group['recall'],1)
            self.assertEqual(group['micro_f1'],1)
            self.assertAlmostEqual(group['macro_f1'],group['n_supported_labels']/group['n_labels'])

if __name__=='__main__':unittest.main()
