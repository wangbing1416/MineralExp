"""Artifact integrity, trace transport, automatic scoring and failure coverage."""
import base64
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from geotask19.artifacts import digest
from geotask19.runner import call_command, make_request, save_case, unpack_response, main
from geotask19.metrics import evaluate, load_jsonl
from test_runner import sample, taxonomy, structured_response

ROOT=Path(__file__).resolve().parents[1]


def read(path):return json.loads(path.read_text(encoding='utf-8'))


class ArtifactTests(unittest.TestCase):
    def setup_files(self,path,n=1):
        rows=[sample() for _ in range(n)]
        for i,row in enumerate(rows):row['task_id']='case-'+str(i+1)
        (path/'data.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
        (path/'taxonomy.json').write_text(json.dumps(taxonomy()),encoding='utf-8')
        return rows

    def command(self,path,adapter,*extra):
        return [sys.executable,'-B','-X','utf8','-m','geotask19.runner',
                '--data',str(path/'data.jsonl'),'--taxonomy',str(path/'taxonomy.json'),
                '--command-json',json.dumps(adapter),'--harness-id','fixture-not-model','--model-id','no-model',
                '--output',str(path/'pred.jsonl'),*extra]

    def assert_manifest(self,folder):
        report=read(folder/'manifest.json')
        expected={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p!=folder/'manifest.json'}
        self.assertEqual({r['path'] for r in report['files']},expected)
        for row in report['files']:
            self.assertEqual(digest(folder/row['path']),row['sha256'])
            self.assertEqual((folder/row['path']).stat().st_size,row['bytes'])

    def test_optional_envelope_preserves_returned_trajectory_usage_and_provider_body(self):
        final=structured_response()
        envelope={'response':final,'trajectory':[
            {'type':'message','role':'assistant','content':'Public plan summary'},
            {'type':'tool_call','name':'fixture_tool','arguments':{'x':1}},
            {'type':'tool_result','name':'fixture_tool','content':{'result':'fixture'}}],
            'usage':{'input_tokens':123,'output_tokens':456},'provider_response':{'id':'fixture-response'}}
        wire=json.dumps(envelope,ensure_ascii=False).encode('utf-8')
        events=[]
        with patch('geotask19.runner.subprocess.run',return_value=subprocess.CompletedProcess([],0,wire,b'PRIVATE_STDERR')):
            prediction=call_command(['fixture'],make_request(sample(),taxonomy()),audit=events.append)
        self.assertEqual(prediction['labels'],['S1'])
        event=events[0]
        self.assertEqual(event['trajectory'],envelope['trajectory'])
        self.assertEqual(event['usage'],envelope['usage'])
        self.assertEqual(event['parsed_response'],envelope)
        self.assertEqual(base64.b64decode(event['raw_stdout_base64']),wire)
        self.assertNotIn('PRIVATE_STDERR',json.dumps(event))
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            save_case(folder,1,event)
            case=folder/'samples/000001'
            self.assertEqual(read(case/'trajectory.json'),{'provided_by_adapter':True,'events':envelope['trajectory']})
            self.assertEqual(read(case/'usage.json')['value'],envelope['usage'])
            self.assertEqual(read(case/'provider_response.json'),envelope['provider_response'])
            self.assertEqual((case/'stdout.bin').read_bytes(),wire)

    def test_envelope_does_not_relax_final_response_or_trace_structure(self):
        for value in ({'response':{},'trajectory':'not-a-list'},
                      {'response':{},'usage':None},{'response':{},'unknown':'field'}):
            with self.assertRaises(ValueError):unpack_response(value)
        with patch('geotask19.runner.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'{"response":{"labels":[]},"trajectory":[]}',b'')):
            with self.assertRaises(ValueError):call_command(['fixture'],make_request(sample(),taxonomy()))

    def test_invalid_utf8_and_nonzero_exit_keep_exact_bytes_and_error_stage(self):
        raw=b'partial\xff\xfe'
        events=[]
        with patch('geotask19.runner.subprocess.run',return_value=subprocess.CompletedProcess([],7,raw,b'PRIVATE_STDERR')):
            with self.assertRaises(RuntimeError):call_command(['fixture'],make_request(sample(),taxonomy()),audit=events.append)
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);save_case(folder,1,events[0])
            self.assertEqual((folder/'samples/000001/stdout.bin').read_bytes(),raw)
            self.assertEqual(read(folder/'samples/000001/status.json')['error_stage'],'process')

    def test_successful_cli_exports_all_score_views_and_replay_inputs_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);rows=self.setup_files(path,2)
            command=self.command(path,[sys.executable,str(ROOT/'examples/structured_harness.py')])
            result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            folder=path/'pred.jsonl.artifacts';score=folder/'scores'
            pred=load_jsonl(path/'pred.jsonl')
            self.assertEqual(read(score/'metrics.json'),evaluate(rows,pred))
            self.assertEqual(load_jsonl(score/'predictions.jsonl'),pred)
            self.assertEqual(evaluate(load_jsonl(score/'gold_labels.jsonl'),load_jsonl(score/'predictions.jsonl')),read(score/'metrics.json'))
            self.assertEqual(len(load_jsonl(folder/'requests.jsonl')),2)
            self.assertEqual(len(load_jsonl(folder/'cases.jsonl')),2)
            self.assertEqual(read(folder/'run.json')['pending'],0)
            self.assertEqual(read(folder/'run.json')['scoring_status'],'completed')
            self.assertEqual(read(folder/'samples/000001/trajectory.json'),{'provided_by_adapter':False,'events':[]})
            self.assertEqual(len(load_jsonl(score/'errors.jsonl')),0)
            for file,n in (('overall.csv',2),('per_process.csv',5),('per_class.csv',19),('per_sample.csv',2)):
                with (score/file).open(encoding='utf-8-sig',newline='') as stream:
                    self.assertEqual(len(list(csv.DictReader(stream))),n)
            self.assert_manifest(score);self.assert_manifest(folder)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_fail_fast_and_continue_keep_failed_responses_and_full_denominator(self):
        for keep_going in (False,True):
            with self.subTest(keep_going=keep_going),tempfile.TemporaryDirectory() as directory:
                path=Path(directory);self.setup_files(path,3)
                script="import json,sys; r=json.load(sys.stdin); print('bad-json') if r['task_id']=='case-2' else print("+repr(json.dumps(structured_response()))+")"
                result=subprocess.run(self.command(path,[sys.executable,'-c',script],*(['--continue-on-error'] if keep_going else [])),
                                      capture_output=True,text=True,encoding='utf-8')
                self.assertNotEqual(result.returncode,0)
                folder=path/'pred.jsonl.artifacts';meta=read(folder/'run.json')
                self.assertEqual(meta['completed'],2 if keep_going else 1)
                self.assertEqual(meta['failed'],1)
                self.assertEqual(meta['pending'],0 if keep_going else 1)
                self.assertEqual(meta['status'],'completed_with_errors' if keep_going else 'failed')
                self.assertFalse((folder/'scores').exists())
                report=read(folder/'scores_partial/metrics.json')
                self.assertEqual(report['n_samples'],3)
                self.assertEqual(report['missing_policy'],'empty')
                self.assertIn('case-2',report['missing_predictions'])
                self.assertTrue(read(folder/'scores_partial/provenance.json')['partial_run'])
                self.assertEqual((folder/'samples/000002/raw_response.txt').read_text(encoding='utf-8'),'bad-json\n')
                self.assertEqual(read(folder/'samples/000002/status.json')['error_stage'],'decode')
                self.assert_manifest(folder)

    def test_timeout_partial_output_and_interrupt_checkpoint_are_saved(self):
        for exception,status in ((subprocess.TimeoutExpired(['fixture'],1,output=b'partial',stderr=b'PRIVATE_STDERR'),'failed'),
                                 (KeyboardInterrupt(),'interrupted')):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as directory:
                path=Path(directory);self.setup_files(path,2)
                args=self.command(path,['fixture'])[6:]
                # Use argparse directly, and mock only the external adapter process.
                with patch.object(sys,'argv',['runner',*args]),patch('geotask19.runner.subprocess.run',side_effect=exception):
                    with self.assertRaises(type(exception)):main()
                folder=path/'pred.jsonl.artifacts'
                self.assertEqual(read(folder/'run.json')['status'],status)
                self.assertEqual(read(folder/'run.json')['completed'],0)
                self.assertEqual(read(folder/'scores_partial/metrics.json')['n_samples'],2)
                self.assertEqual(read(folder/'samples/000001/status.json')['error_type'],type(exception).__name__)
                self.assert_manifest(folder)

    def test_offline_cli_saves_score_bundle_errors_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);self.setup_files(path)
            (path/'pred.jsonl').write_text(json.dumps({'task_id':'case-1','labels':['M1']})+'\n',encoding='utf-8')
            command=[sys.executable,'-B','-m','geotask19.metrics','--data',str(path/'data.jsonl'),
                     '--predictions',str(path/'pred.jsonl'),'--output',str(path/'metrics.json')]
            result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            folder=path/'metrics.json.artifacts'
            self.assertEqual(read(folder/'metrics.json'),read(path/'metrics.json'))
            error=load_jsonl(folder/'errors.jsonl')[0]
            self.assertEqual(error['missed'],['S1']);self.assertEqual(error['extra'],['M1'])
            self.assert_manifest(folder)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_offline_strict_failure_writes_receipt_not_fake_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);self.setup_files(path)
            (path/'pred.jsonl').write_text('',encoding='utf-8')
            result=subprocess.run([sys.executable,'-B','-m','geotask19.metrics','--data',str(path/'data.jsonl'),
                                   '--predictions',str(path/'pred.jsonl'),'--output',str(path/'metrics.json')],capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((path/'metrics.json').exists())
            error=read(path/'metrics.json.artifacts/scoring_error.json')
            self.assertEqual(error['status'],'failed')
            self.assertEqual(error['error_type'],'ValueError')


if __name__=='__main__':unittest.main()
