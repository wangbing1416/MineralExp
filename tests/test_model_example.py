"""Verify a stored real model example without rerunning or altering the solver."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from geotask19.metrics import evaluate,load_jsonl
from geotask19.runner import make_request,normalize_response

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'examples/model_runs/gpt61_sol_G19_0002_20261027'


def read(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


class StoredNativeModelExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.row=next(r for r in load_jsonl(ROOT/'data/benchmark.jsonl') if r['task_id']=='G19-0002')

    def test_exact_allowlisted_request_and_all_19_classes(self):
        request=read(EXAMPLE/'request.json')
        self.assertEqual(request,make_request(self.row,read(ROOT/'data/taxonomy.json')))
        self.assertEqual(len(request['label_space']),19)
        self.assertNotIn('reference',request)
        self.assertNotIn('source',request)
        text=json.dumps(request,ensure_ascii=False)
        self.assertNotIn(self.row['source']['title'],text)
        self.assertNotIn(self.row['source']['doi'],text)

    def test_original_solver_response_is_preserved_without_answer_editing(self):
        prediction=load_jsonl(EXAMPLE/'prediction.jsonl')
        self.assertEqual(len(prediction),1)
        self.assertEqual(prediction[0],normalize_response(read(EXAMPLE/'response.json'),'G19-0002'))
        exchange=read(EXAMPLE/'exchange.json')
        self.assertEqual(exchange['raw_response_text'],(EXAMPLE/'response.json').read_text(encoding='utf-8'))
        self.assertEqual(exchange['request'],read(EXAMPLE/'request.json'))
        self.assertFalse(exchange['gold_supplied_to_solver'])

    def test_metrics_recompute_against_original_dataset_gold(self):
        expected=evaluate([self.row],load_jsonl(EXAMPLE/'prediction.jsonl'))
        self.assertEqual(read(EXAMPLE/'metrics.json'),expected)
        self.assertEqual(expected['n_samples'],1)

    def test_provenance_hashes_model_backend_and_limited_scope(self):
        metadata=read(EXAMPLE/'run.json')
        self.assertEqual(metadata['status'],'completed')
        self.assertEqual(metadata['model_id'],'gpt-6.1-sol')
        self.assertEqual(metadata['execution_backend'],'native_collaboration_spawn_agent')
        self.assertEqual(metadata['fork_turns'],'none')
        self.assertEqual(metadata['n_samples'],1)
        self.assertEqual(metadata['attempts_completed'],1)
        self.assertTrue(metadata['actual_agent_or_llm_run'])
        self.assertFalse(metadata['openrouter_used'])
        self.assertFalse(metadata['os_level_sandbox_enforced'])
        self.assertFalse(metadata['text_quality_scored'])
        self.assertTrue(metadata['agent_id'])
        for key,path in (
            ('dataset_sha256',ROOT/'data/benchmark.jsonl'),
            ('taxonomy_sha256',ROOT/'data/taxonomy.json'),
            # These are historical run hashes, not a requirement to freeze future tooling.
            ('runner_sha256',EXAMPLE/'code_at_run/runner.py'),
            ('scorer_sha256',EXAMPLE/'code_at_run/metrics.py'),
            ('request_sha256',EXAMPLE/'request.json'),('response_sha256',EXAMPLE/'response.json'),
            ('prediction_sha256',EXAMPLE/'prediction.jsonl'),('metrics_sha256',EXAMPLE/'metrics.json')):
            self.assertEqual(metadata[key],sha(path),key)

    def test_cli_replays_only_scoring_not_model_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'gold.jsonl').write_text(json.dumps(self.row,ensure_ascii=False)+'\n',encoding='utf-8')
            result=subprocess.run([sys.executable,'-B','-X','utf8','-m','geotask19.metrics',
                                   '--data',str(path/'gold.jsonl'),
                                   '--predictions',str(EXAMPLE/'prediction.jsonl'),
                                   '--output',str(path/'metrics.json')],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(read(path/'metrics.json'),read(EXAMPLE/'metrics.json'))


if __name__=='__main__':unittest.main()
