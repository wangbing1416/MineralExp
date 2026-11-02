"""Run a trusted external Harness using a whitelist-only JSON stdin contract.

The adapter receives no sample filename, source bibliography, O/E/F or gold labels.
This is data minimization, not an OS security sandbox for an untrusted executable.
"""
import argparse
import base64
import copy
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from .metrics import LABELS, load_jsonl, label_set, evaluate
from .artifacts import now, digest, write_json, append_jsonl, write_jsonl, scoring_bundle, manifest

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

def unpack_response(value):
    """Optional adapter envelope; the model's final response schema is unchanged."""
    if isinstance(value,dict) and 'response' in value:
        if set(value)-{'response','trajectory','usage','provider_response'}:
            raise ValueError('unexpected adapter envelope fields')
        if 'trajectory' in value and not isinstance(value['trajectory'],list):
            raise ValueError('adapter trajectory must be a list of returned events')
        if 'usage' in value and not isinstance(value['usage'],dict):
            raise ValueError('adapter usage must be an object')
        return value['response'],{key:copy.deepcopy(value[key])
                                  for key in ('trajectory','usage','provider_response') if key in value}
    return value,{}


def call_command(command,request,timeout=300,audit=None):
    """Audit returned stdout/optional adapter events; never collect stderr/env."""
    if not isinstance(command,list) or not command or any(not isinstance(x,str) for x in command):
        raise ValueError('command must be a JSON array of argument strings')
    wire=json.dumps(request,ensure_ascii=False).encode('utf-8')
    started=time.perf_counter()
    event={'schema_version':'geotask19.exchange.v1','task_id':request['task_id'],
           'request':copy.deepcopy(request),'status':'failed','returncode':None,'raw_response_text':'',
           'raw_stdout_base64':'','started_at':now(),'error_stage':'process'}
    def capture(raw):
        raw=raw or b''
        if isinstance(raw,str):raw=raw.encode('utf-8')
        event['raw_stdout_base64']=base64.b64encode(raw).decode('ascii')
        event['raw_response_text']=raw.decode('utf-8',errors='replace')
        try:
            value=json.loads(raw.decode('utf-8-sig'))
        except (ValueError,UnicodeError):
            return
        event['parsed_response']=value
        # Even nonzero-exit responses can contain useful partial adapter traces.
        if isinstance(value,dict) and 'response' in value:
            for key in ('trajectory','usage','provider_response'):
                if key in value:event[key]=copy.deepcopy(value[key])
    try:
        result=subprocess.run(command,input=wire,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False)
        event['returncode']=result.returncode
        capture(result.stdout)
        if result.returncode:
            # Do not echo/log stderr: adapters may accidentally print API keys there.
            raise RuntimeError('Harness failed with exit code '+str(result.returncode))
        event['error_stage']='decode'
        value=json.loads(result.stdout.decode('utf-8-sig'))
        event['error_stage']='validation'
        response,extra=unpack_response(value)
        event.update(extra)
        normalized=normalize_response(response,request['task_id'])
        event.update(status='completed',prediction=normalized)
        event.pop('error_stage',None)
        return normalized
    except subprocess.TimeoutExpired as exc:
        capture(exc.stdout)
        event['error_type']=type(exc).__name__
        raise
    except BaseException as exc:
        if isinstance(exc,KeyboardInterrupt):event['status']='interrupted'
        event['error_type']=type(exc).__name__
        raise
    finally:
        event['ended_at']=now()
        event['elapsed_seconds']=time.perf_counter()-started
        if audit is not None:
            audit(event)


def save_case(folder,number,event):
    """Numeric paths avoid treating untrusted task IDs as filesystem paths."""
    case=folder/'samples'/f'{number:06d}'
    case.mkdir(parents=True,exist_ok=False)
    write_json(case/'request.json',event['request'])
    (case/'raw_response.txt').write_text(event['raw_response_text'],encoding='utf-8',newline='')
    (case/'stdout.bin').write_bytes(base64.b64decode(event['raw_stdout_base64']))
    write_json(case/'exchange.json',event)
    for key in ('parsed_response','prediction','provider_response'):
        if key in event:write_json(case/(key+'.json'),event[key])
    write_json(case/'trajectory.json',{'provided_by_adapter':'trajectory' in event,
                                      'events':event.get('trajectory',[])})
    write_json(case/'usage.json',{'provided_by_adapter':'usage' in event,'value':event.get('usage')})
    status={key:event[key] for key in ('task_id','status','returncode','started_at','ended_at',
                                      'elapsed_seconds','error_type','error_stage') if key in event}
    status['trajectory_provided_by_adapter']='trajectory' in event
    write_json(case/'status.json',status)
    append_jsonl(folder/'cases.jsonl',{'directory':case.relative_to(folder).as_posix(),**status})

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
    p.add_argument('--artifacts-dir',help='Fresh result directory; default: OUTPUT.artifacts')
    p.add_argument('--continue-on-error',action='store_true',help='Keep failed cases and attempt the remaining cases; exit nonzero if any failed')
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
    if not isinstance(command,list) or not command or any(not isinstance(x,str) for x in command):
        raise ValueError('command must be a JSON array of argument strings')
    if a.timeout<=0:raise ValueError('timeout must be positive')
    output=Path(a.output)
    trace=Path(str(output)+'.exchanges.jsonl')
    folder=Path(a.artifacts_dir) if a.artifacts_dir else Path(str(output)+'.artifacts')
    if output.exists() or Path(str(output)+'.run.json').exists() or trace.exists() or folder.exists():
        raise FileExistsError('refusing to overwrite a previous run')
    # An output located inside its own fresh artifact tree is ambiguous and unsafe.
    if folder.resolve()==output.resolve() or folder.resolve() in output.resolve().parents:
        raise ValueError('--output must be outside --artifacts-dir')
    output.parent.mkdir(parents=True,exist_ok=True)
    folder.mkdir(parents=True,exist_ok=False)
    meta={'schema_version':'geotask19.run.v2','protocol':PROTOCOL,
          'harness_id':a.harness_id,'model_id':a.model_id,'seed_metadata':a.seed,
          'started_at':datetime.now(timezone.utc).isoformat(),'dataset_sha256':hashlib.sha256(Path(a.data).read_bytes()).hexdigest(),
          'taxonomy_sha256':hashlib.sha256(Path(a.taxonomy).read_bytes()).hexdigest(),'status':'running','completed':0,
          'expected':len(rows),'attempted':0,'failed':0,'pending':len(rows),'timeout_seconds':a.timeout,
          'continue_on_error':a.continue_on_error,'python_version':sys.version.split()[0],
          'runner_sha256':digest(Path(__file__)),'scorer_sha256':digest(Path(__file__).with_name('metrics.py')),
          'artifacts_directory':str(folder),'automatic_scoring':True,
          'input_contract':'three source-supported text fields, neutral task_id, common taxonomy; no sample labels or bibliography',
          'output_contract':'All 19 class decisions; selected-class experiments with design, rationale and conditional evidence. Only labels are scored.',
          'exchange_file':trace.name,'text_quality_scored':False}
    sidecar=Path(str(output)+'.run.json')
    predictions=[]
    def checkpoint():
        write_json(sidecar,meta)
        write_json(folder/'run.json',meta)
    write_jsonl(folder/'requests.jsonl',requests)
    write_json(folder/'taxonomy.json',taxonomy)
    (folder/'README.md').write_text(
        '# GeoTask19 run artifacts\n\n'
        'run.json: status/coverage/configuration; events.jsonl: runner lifecycle (not model reasoning); '
        'requests.jsonl: exact allowlisted inputs; cases.jsonl: per-case index; '
        'samples/000001/: request, raw stdout text and exact bytes, parsed response, prediction, '
        'adapter-returned trajectory/usage, provider response when supplied, status.\n\n'
        'scores/: complete-run metrics and replay inputs; scores_partial/: explicitly incomplete run, '
        'all scheduled cases scored with missing=empty. Never report partial results as a completed run. '
        'manifest.json: file hashes. Check provided_by_adapter before interpreting an empty trajectory. '
        'No hidden reasoning is requested or synthesized; no stderr, environment or auth headers are collected. '
        'Adapters must remove secrets from all returned content. Keep these runtime files private unless reviewed.\n',
        encoding='utf-8',newline='\n')
    checkpoint()
    append_jsonl(folder/'events.jsonl',{'event':'run_started','time':now(),'expected':len(rows)})
    try:
        with output.open('x',encoding='utf-8') as f, trace.open('x',encoding='utf-8') as audit_file:
            def save_exchange(event):
                audit_file.write(json.dumps(event,ensure_ascii=False)+'\n')
                audit_file.flush()
                save_case(folder,meta['attempted'],event)
            for number,request in enumerate(requests,1):
                meta['attempted']=number;meta['pending']=len(rows)-number
                checkpoint()
                append_jsonl(folder/'events.jsonl',{'event':'case_started','time':now(),'task_id':request['task_id'],'sequence':number})
                try:
                    response=call_command(command,request,a.timeout,audit=save_exchange)
                except BaseException as exc:
                    meta['failed']+=1
                    append_jsonl(folder/'events.jsonl',{'event':'case_failed','time':now(),'task_id':request['task_id'],
                                                      'error_type':type(exc).__name__})
                    checkpoint()
                    if not a.continue_on_error or not isinstance(exc,Exception):raise
                    continue
                f.write(json.dumps(response,ensure_ascii=False)+'\n'); f.flush()
                predictions.append(response)
                meta['completed']+=1
                append_jsonl(folder/'events.jsonl',{'event':'case_completed','time':now(),'task_id':request['task_id']})
                checkpoint()
        meta['status']='completed_with_errors' if meta['failed'] else 'completed'
    except BaseException as exc:
        meta['status']='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed'
        meta['error_type']=type(exc).__name__
        raise
    finally:
        partial=meta['status']!='completed'
        score_folder=folder/('scores_partial' if partial else 'scores')
        try:
            # Only the parent evaluates gold, after all attempted external calls have ended.
            report=evaluate(rows,predictions,missing='empty' if partial else 'error')
            scoring_bundle(score_folder,report,rows,predictions,
                           {'source':'runner','partial_run':partial,'run_status':meta['status'],
                            'dataset_sha256':meta['dataset_sha256'],'taxonomy_sha256':meta['taxonomy_sha256'],
                            'scorer_sha256':meta['scorer_sha256']})
            meta['scoring_status']='completed'
            meta['metrics_file']=str(score_folder.relative_to(folder)/'metrics.json')
        except Exception as exc:
            write_json(folder/'scoring_error.json',{'error_type':type(exc).__name__,'time':now()})
            meta['scoring_status']='failed'
        meta['ended_at']=now()
        checkpoint()
        append_jsonl(folder/'events.jsonl',{'event':'run_finished','time':now(),'status':meta['status'],
                                          'completed':meta['completed'],'failed':meta['failed'],'pending':meta['pending']})
        manifest(folder)
    if meta['status']!='completed' or meta['scoring_status']!='completed':
        raise SystemExit(1)
    print(json.dumps({'status':meta['status'],'completed':meta['completed'],'artifacts_directory':str(folder),
                      'metrics_file':str(folder/meta['metrics_file'])},ensure_ascii=False))

if __name__=='__main__':
    main()
