"""Run a trusted external Harness using a whitelist-only JSON stdin contract.

The adapter receives no sample filename, source bibliography, O/E/F or gold labels.
This is data minimization, not an OS security sandbox for an untrusted executable.
"""
import argparse
import copy
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from .metrics import LABELS, load_jsonl, label_set

INPUT_FIELDS = ('hypothesis','initial_information','available_conditions')
PROTOCOL = 'geotask19.harness.v2'
EXPERIMENT_FIELDS = ('name','design','why_needed','expected_evidence',
                     'supports_hypothesis_if','weakens_hypothesis_if','limitations')

def response_schema():
    """A shared JSON Schema describing outputs, never task-specific answers."""
    text = {'type':'string','minLength':1}
    experiment = {'type':'object','additionalProperties':False,
                  'required':list(EXPERIMENT_FIELDS),
                  'properties':{key:dict(text) for key in EXPERIMENT_FIELDS}}
    decision = {'type':'object','additionalProperties':False,
                'required':['class_id','selected','rationale','experiments'],
                'properties':{'class_id':{'type':'string','enum':list(LABELS)},
                              'selected':{'type':'boolean'}, 'rationale':dict(text),
                              'experiments':{'type':'array','items':experiment}},
                'allOf':[{'if':{'properties':{'selected':{'const':True}}},
                          'then':{'properties':{'experiments':{'minItems':1}}},
                          'else':{'properties':{'experiments':{'maxItems':0}}}}]}
    return {'type':'object','additionalProperties':False,
            'required':['labels','class_predictions'],
            'properties':{'labels':{'type':'array','uniqueItems':True,
                                    'items':{'type':'string','enum':list(LABELS)}},
                          'class_predictions':{'type':'array','minItems':len(LABELS),
                                               'maxItems':len(LABELS),'items':decision},
                          'plan':{'type':'string'}}}

def project_input(row):
    """Reconstruct a fresh, minimal DTO; never serialize the original record."""
    task = row.get('task_id')
    if not isinstance(task,str) or not task:
        raise ValueError('nonempty task_id required')
    if row.get('eligibility',{}).get('evaluable') is not True:
        raise ValueError('excluded or unreviewed record cannot be sent to a Harness')
    projected = {}
    for field in INPUT_FIELDS:
        item=row['input'][field]
        if item.get('status') != 'source_supported' or not isinstance(item.get('text'),str) or not item['text'].strip():
            raise ValueError('missing/source-unverified input: '+field)
        projected[field]=item['text']
    return {'task_id':task,'input':projected}

def make_request(row,taxonomy):
    projected=project_input(row)
    classes=taxonomy['classes']
    if {c['ID'] for c in classes} != set(LABELS) or len(classes)!=len(LABELS):
        raise ValueError('taxonomy must contain each of the 19 IDs exactly once')
    return {'protocol':PROTOCOL, **projected,
            'instruction':(
                'Plan experiments to test the stated hypothesis using the supplied initial information and available conditions. '
                'Return one JSON object matching response_schema. Give exactly one class_predictions entry for each of the 19 GeoTask classes: '
                'class_id, selected (a boolean), rationale (a concise justification for selecting or excluding this class), and experiments. '
                'labels must contain exactly the IDs whose selected value is true. Do not select every class by default. '
                'For each selected class, provide at least one concrete experiment with all seven fields: name; '
                'design (materials or samples, method, procedure, comparisons and quality controls); '
                'why_needed (why this experiment is needed for this hypothesis under the given conditions); '
                'expected_evidence (the observations or measurements to obtain, NOT fabricated results); '
                'supports_hypothesis_if and weakens_hypothesis_if (conditional interpretation criteria, including competing explanations); '
                'and limitations (assumptions, confounds, unverified prerequisites and what the experiment cannot establish). '
                'If a direct supporting or weakening criterion is not applicable, explain why in that field. '
                'Do not invent known measurements, sample availability, equipment or budgets; identify additional requirements as proposals, not given facts. '
                'For unselected classes, use experiments=[] and explain exclusion in rationale; do not invent irrelevant experiments. '
                'An optional plan string may summarize ordering or shared experiments. Classification scores use labels only; '
                'designs, rationales and evidence statements are retained for later judging and error analysis, not scored now.'),
            'label_space':[{'id':c['ID'],'process':c['Process'],'name':c['GeoTask'],
                            **({'definition':c['definition']} if c.get('definition') else {})} for c in classes],
            'response_schema':response_schema()}

def normalize_response(response,task_id):
    """Validate structure/consistency, not the scientific merit of any text."""
    if not isinstance(response,dict):
        raise ValueError('Harness response must be one JSON object')
    if set(response)-{'labels','class_predictions','plan'}:
        raise ValueError('unexpected response fields; see response_schema')
    labels=label_set(response.get('labels'))
    decisions=response.get('class_predictions')
    if not isinstance(decisions,list) or len(decisions)!=len(LABELS):
        raise ValueError('class_predictions must contain exactly 19 class decisions')
    by_class={}
    for decision in decisions:
        if not isinstance(decision,dict) or set(decision)!={'class_id','selected','rationale','experiments'}:
            raise ValueError('each class decision needs class_id, selected, rationale and experiments')
        class_id=decision['class_id']
        if not isinstance(class_id,str) or class_id not in LABELS or class_id in by_class:
            raise ValueError('class_predictions class IDs must be known, unique and cover all 19 classes')
        if not isinstance(decision['selected'],bool):
            raise ValueError('selected must be a boolean')
        if not isinstance(decision['rationale'],str) or not decision['rationale'].strip():
            raise ValueError('each class needs a nonempty rationale')
        experiments=decision['experiments']
        if not isinstance(experiments,list):
            raise ValueError('experiments must be a list')
        if decision['selected'] and not experiments:
            raise ValueError('a selected class needs at least one experiment')
        if not decision['selected'] and experiments:
            raise ValueError('an unselected class must have experiments=[]')
        for experiment in experiments:
            if not isinstance(experiment,dict) or set(experiment)!=set(EXPERIMENT_FIELDS):
                raise ValueError('each experiment needs all seven documented design/evidence fields')
            if any(not isinstance(experiment[key],str) or not experiment[key].strip() for key in EXPERIMENT_FIELDS):
                raise ValueError('experiment fields must be nonempty strings')
        by_class[class_id]=decision
    selected={key for key,value in by_class.items() if value['selected']}
    if labels!=selected:
        raise ValueError('labels must exactly match selected class_predictions; no silent repair')
    if 'plan' in response and not isinstance(response['plan'],str):
        raise ValueError('plan must be a string when provided')
    return {'schema_version':'geotask19.prediction.v2','task_id':task_id,
            'labels':[c for c in LABELS if c in labels],
            'class_predictions':[copy.deepcopy(by_class[c]) for c in LABELS],
            **({'plan':response['plan']} if 'plan' in response else {})}

def call_command(command,request,timeout=300,audit=None):
    """Optionally audit the exact public request and stdout, never stderr/keys."""
    if not isinstance(command,list) or not command or any(not isinstance(x,str) for x in command):
        raise ValueError('command must be a JSON array of argument strings')
    wire=json.dumps(request,ensure_ascii=False).encode('utf-8')
    started=time.perf_counter()
    event={'schema_version':'geotask19.exchange.v1','task_id':request['task_id'],
           'request':copy.deepcopy(request),'status':'failed','returncode':None,'raw_response_text':''}
    try:
        result=subprocess.run(command,input=wire,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False)
        event.update(returncode=result.returncode,raw_response_text=result.stdout.decode('utf-8-sig',errors='replace'))
        if result.returncode:
            # Do not echo/log stderr: adapters may accidentally print API keys there.
            raise RuntimeError('Harness failed with exit code '+str(result.returncode))
        response=json.loads(result.stdout.decode('utf-8-sig'))
        normalized=normalize_response(response,request['task_id'])
        event['status']='completed'
        return normalized
    except subprocess.TimeoutExpired as exc:
        event['raw_response_text']=(exc.stdout or b'').decode('utf-8-sig',errors='replace')
        event['error_type']=type(exc).__name__
        raise
    except Exception as exc:
        event['error_type']=type(exc).__name__
        raise
    finally:
        event['elapsed_seconds']=time.perf_counter()-started
        if audit is not None:
            audit(event)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',default='data/benchmark.jsonl')
    p.add_argument('--taxonomy',default='data/taxonomy.json')
    p.add_argument('--command-json',required=True,help='JSON array, e.g. ["python","my_harness.py"]')
    p.add_argument('--harness-id',required=True)
    p.add_argument('--model-id',required=True)
    p.add_argument('--seed',type=int,default=0,help='Recorded only; configure model seed in your adapter')
    p.add_argument('--timeout',type=float,default=300)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    rows=load_jsonl(a.data)
    if not rows:
        raise ValueError('no eligible samples; complete source-grounded inputs before running models')
    if len({r['task_id'] for r in rows})!=len(rows):
        raise ValueError('duplicate task IDs')
    taxonomy=json.loads(Path(a.taxonomy).read_text(encoding='utf-8-sig'))
    # Validate the entire split before launching the first external process.
    requests=[make_request(row,taxonomy) for row in rows]
    command=json.loads(a.command_json)
    output=Path(a.output)
    trace=Path(str(output)+'.exchanges.jsonl')
    if output.exists() or Path(str(output)+'.run.json').exists() or trace.exists():
        raise FileExistsError('refusing to overwrite a previous run')
    output.parent.mkdir(parents=True,exist_ok=True)
    meta={'schema_version':'geotask19.run.v2','protocol':PROTOCOL,
          'harness_id':a.harness_id,'model_id':a.model_id,'seed_metadata':a.seed,
          'started_at':datetime.now(timezone.utc).isoformat(),'dataset_sha256':hashlib.sha256(Path(a.data).read_bytes()).hexdigest(),
          'taxonomy_sha256':hashlib.sha256(Path(a.taxonomy).read_bytes()).hexdigest(),'status':'running','completed':0,
          'input_contract':'three source-supported text fields, neutral task_id, common taxonomy; no sample labels or bibliography',
          'output_contract':'All 19 class decisions; selected-class experiments with design, rationale and conditional evidence. Only labels are scored.',
          'exchange_file':trace.name,'text_quality_scored':False}
    sidecar=Path(str(output)+'.run.json')
    try:
        with output.open('x',encoding='utf-8') as f, trace.open('x',encoding='utf-8') as audit_file:
            def save_exchange(event):
                audit_file.write(json.dumps(event,ensure_ascii=False)+'\n')
                audit_file.flush()
            for request in requests:
                response=call_command(command,request,a.timeout,audit=save_exchange)
                f.write(json.dumps(response,ensure_ascii=False)+'\n'); f.flush()
                meta['completed']+=1
        meta['status']='completed'
    except Exception as exc:
        meta['status']='failed'; meta['error_type']=type(exc).__name__
        raise
    finally:
        meta['ended_at']=datetime.now(timezone.utc).isoformat()
        sidecar.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':
    main()
