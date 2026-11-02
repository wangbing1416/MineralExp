"""Local evaluation artifacts; no model calls, credentials, or implicit traces."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    # Checkpoints may be updated; an interrupted write must not truncate the last one.
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    temporary.replace(path)


def append_jsonl(path, value):
    with Path(path).open('a',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(value,ensure_ascii=False)+'\n')
        stream.flush()


def write_jsonl(path, rows):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        for row in rows:
            stream.write(json.dumps(row,ensure_ascii=False)+'\n')


def write_csv(path, rows):
    """UTF-8 BOM for spreadsheet readers; compound values are JSON cells."""
    rows=list(rows)
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key:json.dumps(value,ensure_ascii=False) if isinstance(value,(list,dict)) else value
                             for key,value in row.items()})


def manifest(folder):
    folder=Path(folder)
    entries=[{'path':path.relative_to(folder).as_posix(),'bytes':path.stat().st_size,'sha256':digest(path)}
             for path in sorted(folder.rglob('*')) if path.is_file() and path!=folder/'manifest.json']
    write_json(folder/'manifest.json',{'schema_version':'geotask19.artifacts.manifest.v1','files':entries})


def scoring_bundle(folder, report, gold_rows, prediction_rows, provenance):
    """Save replayable label inputs and every score/error view in a fresh folder."""
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=False)
    write_json(folder/'metrics.json',report)
    write_json(folder/'provenance.json',{'created_at':now(),**provenance,
               'missing_policy':report['missing_policy'],'n_samples':report['n_samples'],
               'submitted_predictions':report['submitted_predictions'],
               'missing_predictions':report['missing_predictions'],'text_quality_scored':False})
    write_jsonl(folder/'gold_labels.jsonl',[
        {'task_id':row['task_id'],'eligibility':{'evaluable':True},
         'reference':{'class_ids':row['reference']['class_ids']}} for row in gold_rows])
    # Retain designs/rationales when supplied, even though the scorer ignores them.
    write_jsonl(folder/'predictions.jsonl',prediction_rows)
    write_csv(folder/'overall.csv',[{'group':'overall',**report['overall']},
                                    {'group':'process_average',**report['process_average']}])
    write_csv(folder/'per_process.csv',[{'process':key,**value} for key,value in report['per_process'].items()])
    write_csv(folder/'per_class.csv',[{'class_id':key,**value} for key,value in report['per_class'].items()])
    write_csv(folder/'per_sample.csv',report['per_sample'])
    write_jsonl(folder/'errors.jsonl',[row for row in report['per_sample']
                                      if not row['exact_match'] or row['task_id'] in report['missing_predictions']])
    manifest(folder)
