"""Protocol smoke test ONLY. This is not an Agent/LLM baseline."""
import json
import sys
request=json.loads(sys.stdin.buffer.read().decode('utf-8'))
assert request['protocol']=='geotask19.harness.v2'
response={'labels':[],
          'class_predictions':[{'class_id':c['id'],'selected':False,
                                'rationale':'Protocol fixture only: no model was called and no class was selected.',
                                'experiments':[]} for c in request['label_space']],
          'plan':'Protocol smoke test: no model was called.'}
sys.stdout.buffer.write(json.dumps(response,ensure_ascii=False).encode('utf-8'))
