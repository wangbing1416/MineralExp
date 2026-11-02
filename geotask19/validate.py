"""Validate the final evaluation-only release and its fixed expert scope decision."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from .metrics import load_jsonl,label_set,LABELS
from .runner import INPUT_FIELDS,make_request

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def validate(root):
    root=Path(root);data=root/'data';rows=load_jsonl(data/'benchmark.jsonl')
    summary=read(data/'summary.json');taxonomy=read(data/'taxonomy.json');decision=read(data/'expert_scope_decision.json');excluded=read(data/'exclusions.json')
    expert_review=read(data/'expert_review_status.json')
    assert expert_review['review_status']==summary['expert_review_status']=='expert_reviewed_gold'
    assert summary['all_samples_expert_certified'] is True
    assert expert_review['evidence_type']=='maintainer_confirmation_in_conversation'
    assert expert_review['sample_inputs_and_reference_label_sets_changed'] is False
    assert len(rows)==summary['evaluable_samples']==277
    ids={r['task_id'] for r in rows};assert len(ids)==277
    assert len({r['source']['paper_id'] for r in rows})==277
    assert len(taxonomy['classes'])==19 and {c['ID'] for c in taxonomy['classes']}==set(LABELS)
    exclusions={r['task_id'] for r in excluded['records']};assert len(exclusions)==excluded['count']==76
    assert not ids&exclusions
    assert sum(bool(r['missing_fields']) for r in excluded['records'])==54
    rejected=set(decision['excluded_input_complete_task_ids']);assert len(rejected)==22
    assert rejected=={r['task_id'] for r in excluded['records'] if not r['missing_fields']}
    assert not ids&rejected
    count=omitted=0
    for row in rows:
        assert row['eligibility']=={'evaluable':True,'missing_fields':[],'reason_codes':[]}
        assert all(row['input'][f]['status']=='source_supported' and isinstance(row['input'][f]['text'],str) and row['input'][f]['text'].strip() for f in INPUT_FIELDS)
        truth=label_set(row['reference']['class_ids']);assert truth
        union=set();actions=row['reference']['O'];assert actions
        assert len({o['o_id'] for o in actions})==len(actions)
        for o in actions:
            labels=label_set(o['class_ids']);assert labels
            assert o['original_description'] and o['original_o_sha256'] and 'proposed_class' not in o
            union|=labels
        assert union==truth
        count+=len(actions);omitted+=row['reference']['omitted_unscored_o_count']
        request=make_request(row,taxonomy)
        assert set(request['input'])==set(INPUT_FIELDS)
        poisoned=json.loads(json.dumps(row));poisoned['reference']={'secret':'FORBIDDEN_REFERENCE_SENTINEL'};poisoned['source']={'title':'FORBIDDEN_SOURCE_SENTINEL'}
        for f in INPUT_FIELDS:poisoned['input'][f]['evidence']=[{'quote':'FORBIDDEN_QUOTE_SENTINEL'}]
        assert make_request(poisoned,taxonomy)==request
        assert 'FORBIDDEN_' not in json.dumps(request)
        assert all(c['id'] in LABELS for c in request['label_space'])
    assert count==summary['released_scored_O']==1572
    assert omitted==summary['omitted_workflow_or_out_of_scope_O']==356
    target=next(r for r in rows if r['task_id']=='G19-0161')
    assert target['reference']['class_ids']==['M1']
    assert len(target['reference']['O'])==1
    assert target['reference']['O'][0]['o_id']=='O05' and target['reference']['O'][0]['class_ids']==['M1']
    assert target['reference']['O'][0]['original_o_sha256']==decision['accepted']['original_o_sha256']
    assert decision['accepted']['class_ids']==['M1']
    requests=[make_request(r,taxonomy) for r in rows]
    common=requests[0]['label_space'];assert all(r['label_space']==common for r in requests)
    m1=next(c for c in common if c['id']=='M1');assert m1.get('definition')
    assert 'G19-0161' not in json.dumps(common) and 'O05' not in json.dumps(common) and 'R15' not in json.dumps(common)
    class_counts={c:sum(c in r['reference']['class_ids'] for r in rows) for c in LABELS}
    assert class_counts==summary['class_counts']
    assert [c for c,v in class_counts.items() if not v]==summary['classes_without_positive_examples']
    for p in data.iterdir():
        if p.is_file():assert not re.search(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]',p.read_text(encoding='utf-8')),p
    assert not list(root.rglob('*.pdf')) and not list(root.rglob('*.docx')) and not list(root.rglob('*.xlsx'))
    assert not (root/'LICENSE').exists() and not (root/'runtime').exists()
    manifest=root/'MANIFEST.sha256'
    if manifest.exists():
        for line in manifest.read_text(encoding='utf-8').splitlines():
            expected,rel=line.split('  ',1);assert hashlib.sha256((root/rel).read_bytes()).hexdigest()==expected,rel
    return {'status':'passed','evaluable_samples':277,'scored_O':1572,'excluded_records':76,'scope_rejected_cases':22,
            'all_requests_projection_checked':True,'common_taxonomy_definition_checked':True,'r15_mapped_to':'M1 / O05',
            'new_classes_added':0,'actual_agent_or_llm_run':False,
            'expert_review_status':expert_review['review_status'],'expert_review_evidence':expert_review['evidence_type']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='.');a=p.parse_args();print(json.dumps(validate(a.root),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
