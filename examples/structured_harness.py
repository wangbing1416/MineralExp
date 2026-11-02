"""Positive structured-output fixture ONLY, not an Agent or scientific baseline."""
import json
import sys

request=json.loads(sys.stdin.buffer.read().decode('utf-8'))
assert request['protocol']=='geotask19.harness.v2'
selected=request['label_space'][0]['id']
experiment={
    'name':'接口测试实验 / Protocol fixture',
    'design':'Fixture placeholder for samples, method, procedure, comparison and quality control; not a real scientific plan.',
    'why_needed':'Only to test preservation of experiment-specific justification, not experimental necessity.',
    'expected_evidence':'No measurements are made. This is a serialization fixture.',
    'supports_hypothesis_if':'Not applicable: this fixture does not test a scientific hypothesis.',
    'weakens_hypothesis_if':'Not applicable: this fixture does not test a scientific hypothesis.',
    'limitations':'No LLM, scientific analysis or physical experiment was performed.'}
response={'labels':[selected],
          'class_predictions':[{
              'class_id':c['id'],'selected':c['id']==selected,
              'rationale':'Selected the first class for protocol testing only.' if c['id']==selected else 'Not selected by this protocol-only fixture.',
              'experiments':[experiment] if c['id']==selected else []} for c in request['label_space']],
          'plan':'Protocol fixture with one selected class; NOT a scientific recommendation.'}
sys.stdout.buffer.write(json.dumps(response,ensure_ascii=False).encode('utf-8'))
