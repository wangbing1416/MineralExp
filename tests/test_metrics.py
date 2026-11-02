import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from geotask19.metrics import evaluate, LABELS

def gold(task,labels):
    return {'task_id':task,'eligibility':{'evaluable':True},'reference':{'class_ids':labels}}

class MetricsTests(unittest.TestCase):
    def test_four_of_ten_is_recall(self):
        truth=list(LABELS[:10]); predicted=truth[:4]
        r=evaluate([gold('x',truth)],[{'task_id':'x','labels':predicted}])
        self.assertEqual(r['micro']['recall'],.4)
        self.assertEqual(r['micro']['precision'],1)
        self.assertEqual(r['sample_jaccard'],.4)
        self.assertEqual(r['subset_accuracy'],0)
        self.assertAlmostEqual(r['hamming_accuracy'],13/19)

    def test_false_positive_penalty(self):
        r=evaluate([gold('x',['S1'])],[{'task_id':'x','labels':['S1','S2']}])
        self.assertEqual(r['micro']['precision'],.5)
        self.assertEqual(r['micro']['recall'],1)
        self.assertEqual(r['micro']['fp'],1)

    def test_perfect(self):
        r=evaluate([gold('x',['S1','T1'])],[{'task_id':'x','labels':['T1','S1']}])
        self.assertEqual(r['subset_accuracy'],1)
        self.assertEqual(r['micro']['f1'],1)
        self.assertEqual(r['macro_supported_classes_only']['f1'],1)
        self.assertAlmostEqual(r['macro']['f1'],2/19)

    def test_missing_default_error(self):
        with self.assertRaises(ValueError): evaluate([gold('x',['S1'])],[])
        r=evaluate([gold('x',['S1'])],[],missing='empty')
        self.assertEqual(r['micro']['recall'],0)
        self.assertEqual(r['missing_predictions'],['x'])

    def test_empty_truth_policy(self):
        r=evaluate([gold('x',[])],[{'task_id':'x','labels':[]}])
        self.assertEqual(r['subset_accuracy'],1)
        self.assertEqual(r['sample_jaccard'],1)
        self.assertEqual(r['micro']['f1'],0)

    def test_no_samples_not_a_zero_score(self):
        with self.assertRaises(ValueError): evaluate([],[])

    def test_reject_duplicates_unknowns_and_invalid_types(self):
        g=[gold('x',['S1'])]
        for preds in ([{'task_id':'x','labels':['S1','S1']}],
                      [{'task_id':'x','labels':['new']}],
                      [{'task_id':'x','labels':'S1'}],
                      [{'task_id':'x','labels':[None]}],
                      [{'task_id':'x','labels':[]},{'task_id':'x','labels':[]}],
                      [{'task_id':'extra','labels':[]}]):
            with self.subTest(preds=preds), self.assertRaises(ValueError): evaluate(g,preds)

    def test_duplicate_gold_and_exclusion(self):
        with self.assertRaises(ValueError): evaluate([gold('x',[]),gold('x',[])],[])
        excluded=gold('x',['S1']); excluded['eligibility']['evaluable']=False
        with self.assertRaises(ValueError): evaluate([excluded],[{'task_id':'x','labels':[]}])

    def test_macro_and_sample_average(self):
        r=evaluate([gold('a',['S1','S2']),gold('b',['S1'])],
                   [{'task_id':'a','labels':['S1']},{'task_id':'b','labels':['T1']}])
        self.assertEqual(r['micro']['tp'],1)
        self.assertEqual(r['micro']['fn'],2)
        self.assertEqual(r['micro']['fp'],1)
        self.assertAlmostEqual(r['samples']['recall'],.25)
        self.assertEqual(r['per_class']['S1']['support'],2)
        self.assertEqual(r['per_process']['Transport']['fp'],1)

    def test_complete_overall_and_process_metrics_hand_calculated(self):
        rows=[gold('a',['S1','S2','T1']),gold('b',['S1','M1']),gold('c',['M1'])]
        preds=[{'task_id':'a','labels':['S1','T1']},
               {'task_id':'b','labels':['S2','M1']},
               {'task_id':'c','labels':['M1','P1']}]
        r=evaluate(rows,preds)
        # Every expected score is independently hand-calculated, not an LLM result.
        expected={
            'overall':(0,2/3,2/3,2/3,8/57),
            'Source':(1/3,1/2,1/3,2/5,1/6),
            'Transport':(1,1,1,1,1/5),
            'Deposition':(1,0,0,0,0),
            'Modification':(1,1,1,1,1/3),
            'Preservation':(2/3,0,0,0,0)}
        keys=('acc','precision','recall','micro_f1','macro_f1')
        for name,scores in expected.items():
            group=r['overall'] if name=='overall' else r['per_process'][name]
            for key,score in zip(keys,scores):
                with self.subTest(group=name,metric=key):self.assertAlmostEqual(group[key],score)
            self.assertEqual(group['n_samples'],3)
            self.assertEqual(group['acc'],group['subset_accuracy'])
            self.assertEqual(group['micro_f1'],group['f1'])
        self.assertEqual(r['overall']['support'],6)
        self.assertEqual(r['per_process']['Source']['positive_samples'],2)
        self.assertEqual(r['per_process']['Source']['n_supported_labels'],2)
        self.assertAlmostEqual(r['per_process']['Source']['macro_supported_classes_only']['f1'],1/3)
        for key,score in zip(keys,(4/5,1/2,7/15,12/25,7/50)):
            self.assertAlmostEqual(r['process_average'][key],score)

    def test_process_negative_rows_still_penalize_false_positives(self):
        r=evaluate([gold('a',['S1']),gold('b',['M1'])],
                   [{'task_id':'a','labels':['S1']},{'task_id':'b','labels':['M1','S1']}])
        source=r['per_process']['Source']
        self.assertEqual(source['n_samples'],2)
        self.assertEqual(source['positive_samples'],1)
        self.assertEqual(source['fp'],1)
        self.assertEqual(source['acc'],.5)
        self.assertEqual(source['precision'],.5)
        self.assertAlmostEqual(source['micro_f1'],2/3)

    def test_zero_support_process_and_fixed_macro_denominator(self):
        r=evaluate([gold('a',['S1'])],[{'task_id':'a','labels':['S1']}])
        source=r['per_process']['Source']
        self.assertEqual(source['n_labels'],4)
        self.assertEqual(source['macro_f1'],.25)
        self.assertEqual(source['macro_supported_classes_only']['f1'],1)
        empty=r['per_process']['Preservation']
        self.assertEqual(empty['acc'],1)
        self.assertEqual(empty['positive_samples'],0)
        self.assertEqual(empty['micro_f1'],0)
        self.assertEqual(empty['macro_f1'],0)
        self.assertEqual(empty['macro_supported_classes_only']['f1'],0)

    def test_missing_empty_applies_to_every_group(self):
        r=evaluate([gold('x',['S1','M1'])],[],missing='empty')
        for name in ('Source','Modification'):
            group=r['per_process'][name]
            self.assertEqual(group['acc'],0)
            self.assertEqual(group['recall'],0)
            self.assertEqual(group['fn'],1)
        self.assertEqual(r['per_process']['Transport']['acc'],1)

    def test_legacy_fields_and_overall_are_identical(self):
        r=evaluate([gold('x',['S1','M1'])],[{'task_id':'x','labels':['M1','P1']}])
        overall=r['overall']
        self.assertEqual(r['subset_accuracy'],overall['acc'])
        self.assertEqual(r['hamming_accuracy'],overall['hamming_accuracy'])
        self.assertEqual(r['hamming_loss'],overall['hamming_loss'])
        self.assertEqual(r['macro'],overall['macro'])
        self.assertEqual(r['macro_supported_classes_only'],overall['macro_supported_classes_only'])
        for key in ('tp','fp','fn','tn','precision','recall','f1'):
            self.assertEqual(r['micro'][key],overall[key])

    def test_cli_outputs_all_requested_scores(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'gold.jsonl').write_text(json.dumps(gold('x',['S1']))+'\n',encoding='utf-8')
            (root/'pred.jsonl').write_text(json.dumps({'task_id':'x','labels':['S1']})+'\n',encoding='utf-8')
            result=subprocess.run([sys.executable,'-B','-m','geotask19.metrics',
                                   '--data',str(root/'gold.jsonl'),'--predictions',str(root/'pred.jsonl'),
                                   '--output',str(root/'report.json')],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            report=json.loads((root/'report.json').read_text(encoding='utf-8'))
            console=json.loads(result.stdout)
            for key in ('overall','per_process','process_average'):
                self.assertEqual(console[key],report[key])
            self.assertEqual(report['schema_version'],'geotask19.metrics.v2')
            self.assertEqual(len(report['per_process']),5)
            for group in [report['overall'],*report['per_process'].values()]:
                self.assertTrue({'acc','precision','recall','micro_f1','macro_f1'}<=group.keys())

if __name__=='__main__': unittest.main()
