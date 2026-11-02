import copy
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from geotask19.runner import (project_input, make_request, call_command, normalize_response,
                             INPUT_FIELDS, EXPERIMENT_FIELDS, PROTOCOL)
from geotask19.metrics import LABELS, evaluate

ROOT=Path(__file__).resolve().parents[1]

def sample():
    return {'task_id':'G19-TEST','eligibility':{'evaluable':True},
            'input':{k:{'text':'neutral '+k,'status':'source_supported',
                        'evidence':[{'quote':'PRIVATE_EVIDENCE_CANARY'}]} for k in INPUT_FIELDS},
            'source':{'title':'PRIVATE_SOURCE_CANARY','path':'D:/SECRET.pdf'},
            'reference':{'class_ids':['S1'],'O':[{'id':'O1','result':'PRIVATE_LABEL_CANARY'}],
                         'E':['PRIVATE_E_CANARY'],'F':['PRIVATE_F_CANARY']},
            'evil':{'instruction':'PRIVATE_NESTED_CANARY'}}

def taxonomy():
    return {'classes':[{'ID':x,'Process':x[0],'GeoTask':'common class '+x} for x in LABELS]}

def structured_response():
    return {'labels':['S1'], 'class_predictions':[
        {'class_id':c,'selected':c=='S1','rationale':'A concise fixture justification.',
         'experiments':[{key:'Fixture value for '+key for key in EXPERIMENT_FIELDS}] if c=='S1' else []}
        for c in LABELS], 'plan':'Fixture overview.'}

class RunnerTests(unittest.TestCase):
    def test_whitelist_has_no_private_content(self):
        row=sample(); request=make_request(row,taxonomy())
        wire=json.dumps(request)
        for key in ('PRIVATE_','SECRET.pdf','reference','source','class_ids','O1'):
            self.assertNotIn(key,wire)
        self.assertEqual(set(request['input']),set(INPUT_FIELDS))
        self.assertEqual(len(request['label_space']),19)
        self.assertNotIn('evidence',request['input'])
        self.assertEqual(request['protocol'],PROTOCOL)
        self.assertEqual(request['response_schema']['required'],['labels','class_predictions'])

    def test_projection_is_new_and_not_mutating(self):
        row=sample(); original=copy.deepcopy(row)
        p=project_input(row); p['input']['hypothesis']='CHANGED'
        self.assertEqual(row,original)

    def test_missing_and_excluded_are_never_sent(self):
        for field in INPUT_FIELDS:
            row=sample(); row['input'][field]['status']='unverified'
            with self.assertRaises(ValueError): project_input(row)
        row=sample(); row['eligibility']['evaluable']=False
        with self.assertRaises(ValueError): project_input(row)

    def test_adapter_only_receives_public_request(self):
        row=sample(); row['input']['hypothesis']['text']='假说：不同物源可能共同贡献矿化。'
        request=make_request(row,taxonomy())
        harness=Path(__file__).resolve().parents[1]/'examples/empty_harness.py'
        response=call_command([sys.executable,str(harness)],request,10)
        self.assertEqual(response['labels'],[])
        self.assertEqual(response['task_id'],'G19-TEST')

    def test_invalid_taxonomy(self):
        t=taxonomy(); t['classes'][0]['ID']='WRONG'
        with self.assertRaises(ValueError): make_request(sample(),t)

    def test_all_19_decisions_are_required_and_order_is_canonical(self):
        response=structured_response()
        response['class_predictions'].reverse()
        original=copy.deepcopy(response)
        parsed=normalize_response(response,'x')
        self.assertEqual([c['class_id'] for c in parsed['class_predictions']],list(LABELS))
        self.assertEqual(parsed['labels'],['S1'])
        self.assertEqual(parsed['schema_version'],'geotask19.prediction.v2')
        parsed['class_predictions'][0]['experiments'][0]['design']='Changed copy'
        self.assertEqual(response,original)

    def test_selected_experiment_fields_and_rationales_are_preserved(self):
        request=make_request(sample(),taxonomy())
        exchanges=[]
        result=call_command([sys.executable,str(ROOT/'examples/structured_harness.py')],request,10,audit=exchanges.append)
        self.assertEqual(len(result['class_predictions']),19)
        self.assertEqual(result['labels'],['S1'])
        decision=result['class_predictions'][0]
        self.assertTrue(decision['rationale'])
        self.assertEqual(set(decision['experiments'][0]),set(EXPERIMENT_FIELDS))
        self.assertIn('接口测试实验',decision['experiments'][0]['name'])
        self.assertEqual(len(exchanges),1)
        self.assertEqual(exchanges[0]['status'],'completed')
        self.assertEqual(exchanges[0]['request'],request)
        raw=json.loads(exchanges[0]['raw_response_text'])
        self.assertEqual(raw['class_predictions'],result['class_predictions'])
        self.assertNotIn('PRIVATE_',json.dumps(exchanges))

    def test_missing_duplicate_unknown_and_inconsistent_class_predictions_fail(self):
        cases=[]
        missing=structured_response();missing.pop('class_predictions');cases.append(missing)
        short=structured_response();short['class_predictions'].pop();cases.append(short)
        duplicate=structured_response();duplicate['class_predictions'][-1]['class_id']='S1';cases.append(duplicate)
        unknown=structured_response();unknown['class_predictions'][-1]['class_id']='UNKNOWN';cases.append(unknown)
        inconsistent=structured_response();inconsistent['labels']=[];cases.append(inconsistent)
        bad_bool=structured_response();bad_bool['class_predictions'][0]['selected']=1;cases.append(bad_bool)
        old= {'labels':['S1'],'plan':'Old labels-only adapter'};cases.append(old)
        for number,response in enumerate(cases):
            with self.subTest(case=number),self.assertRaises(ValueError):normalize_response(response,'x')

    def test_text_structure_is_checked_without_scientific_grading(self):
        cases=[]
        no_reason=structured_response();no_reason['class_predictions'][0]['rationale']='  ';cases.append(no_reason)
        no_exp=structured_response();no_exp['class_predictions'][0]['experiments']=[];cases.append(no_exp)
        negative_exp=structured_response();negative_exp['class_predictions'][1]['experiments']=[{}];cases.append(negative_exp)
        no_design=structured_response();no_design['class_predictions'][0]['experiments'][0].pop('design');cases.append(no_design)
        for field in EXPERIMENT_FIELDS:
            blank=structured_response();blank['class_predictions'][0]['experiments'][0][field]=' ';cases.append(blank)
            wrong_type=structured_response();wrong_type['class_predictions'][0]['experiments'][0][field]=[];cases.append(wrong_type)
        no_overview=structured_response();no_overview['plan']=None;cases.append(no_overview)
        for number,response in enumerate(cases):
            with self.subTest(case=number),self.assertRaises(ValueError):normalize_response(response,'x')
        # Nonempty fixture strings pass structure checks without any claim of scientific validity.
        normalize_response(structured_response(),'x')

    def test_multiple_experiments_and_negative_only_output_are_allowed(self):
        response=structured_response()
        response['class_predictions'][0]['experiments']*=2
        self.assertEqual(len(normalize_response(response,'x')['class_predictions'][0]['experiments']),2)
        response['labels']=[]
        response['class_predictions'][0]['selected']=False
        response['class_predictions'][0]['experiments']=[]
        self.assertEqual(normalize_response(response,'x')['labels'],[])

    def test_narrative_content_never_changes_classification_scores(self):
        reference=[{'task_id':'x','eligibility':{'evaluable':True},'reference':{'class_ids':['S1','M1']}}]
        first=normalize_response(structured_response(),'x')
        second=copy.deepcopy(first)
        second['plan']='Entirely different unscored overview.'
        for decision in second['class_predictions']:
            decision['rationale']='Entirely different unscored rationale.'
            for experiment in decision['experiments']:
                for field in EXPERIMENT_FIELDS:experiment[field]='Entirely different unscored text.'
        self.assertEqual(evaluate(reference,[first]),evaluate(reference,[second]))
        self.assertEqual(evaluate(reference,[first]),evaluate(reference,[{'task_id':'x','labels':['S1']}]))

    def test_failed_and_invalid_responses_are_audited_without_stderr(self):
        request=make_request(sample(),taxonomy())
        for command,exception in (([sys.executable,'-c',"print('not-json')"],ValueError),
                                  ([sys.executable,'-c',"import sys;print('partial stdout');sys.stderr.write('DO_NOT_LOG_STDERR');sys.exit(7)"],RuntimeError)):
            exchanges=[]
            with self.assertRaises(exception) as raised:call_command(command,request,10,audit=exchanges.append)
            self.assertEqual(exchanges[0]['status'],'failed')
            self.assertTrue(exchanges[0]['raw_response_text'])
            self.assertNotIn('DO_NOT_LOG_STDERR',json.dumps(exchanges)+str(raised.exception))

    def test_timeout_partial_stdout_is_audited(self):
        request=make_request(sample(),taxonomy())
        exchanges=[]
        error=subprocess.TimeoutExpired(['fixture'],.1,output=b'partial stdout',stderr=b'DO_NOT_LOG_STDERR')
        with patch('geotask19.runner.subprocess.run',side_effect=error),self.assertRaises(subprocess.TimeoutExpired):
            call_command(['fixture'],request,.1,audit=exchanges.append)
        self.assertEqual(exchanges[0]['error_type'],'TimeoutExpired')
        self.assertEqual(exchanges[0]['raw_response_text'],'partial stdout')
        self.assertNotIn('DO_NOT_LOG_STDERR',json.dumps(exchanges))

    def test_cli_persists_structured_predictions_requests_and_raw_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            rows=[sample(),sample()];rows[1]['task_id']='G19-TEST-2'
            (path/'data.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
            (path/'taxonomy.json').write_text(json.dumps(taxonomy()),encoding='utf-8')
            output=path/'predictions.jsonl'
            command=[sys.executable,'-B','-X','utf8','-m','geotask19.runner','--data',str(path/'data.jsonl'),
                     '--taxonomy',str(path/'taxonomy.json'),'--command-json',
                     json.dumps([sys.executable,str(ROOT/'examples/structured_harness.py')]),
                     '--harness-id','fixture-only','--model-id','no-model','--output',str(output)]
            result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            predictions=[json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
            traces=[json.loads(line) for line in Path(str(output)+'.exchanges.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(predictions),2);self.assertEqual(len(traces),2)
            for prediction,trace in zip(predictions,traces):
                self.assertEqual(len(prediction['class_predictions']),19)
                self.assertEqual(trace['request']['protocol'],PROTOCOL)
                self.assertNotIn('PRIVATE_',json.dumps(trace))
                self.assertEqual(json.loads(trace['raw_response_text'])['class_predictions'],prediction['class_predictions'])
            metadata=json.loads(Path(str(output)+'.run.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['completed'],2)
            self.assertEqual(metadata['schema_version'],'geotask19.run.v2')
            self.assertFalse(metadata['text_quality_scored'])
            rerun=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            self.assertNotEqual(rerun.returncode,0)

if __name__=='__main__': unittest.main()
