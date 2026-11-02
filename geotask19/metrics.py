"""Dependency-free, strict multi-label evaluation; no model calls."""
import argparse
import json
from pathlib import Path
from .artifacts import digest, write_json, scoring_bundle, now

LABELS = ('S1','S2','S3','S4','T1','T2','T3','T4','T5','D1','D2','D3','D4','M1','M2','M3','P1','P2','P3')
PROCESSES = dict(zip('STDMP', ('Source','Transport','Deposition','Modification','Preservation')))

def load_jsonl(path):
    with open(path, encoding='utf-8-sig') as f:
        return [json.loads(line) for line in f if line.strip()]

def label_set(values):
    if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
        raise ValueError('labels must be a list of class-ID strings')
    if len(values) != len(set(values)):
        raise ValueError('duplicate labels are not accepted')
    unknown = set(values) - set(LABELS)
    if unknown:
        raise ValueError('unknown labels: '+str(sorted(unknown)))
    return set(values)

def ratio(a,b):
    return a/b if b else 0.0

def prf(tp,fp,fn):
    return {'precision': ratio(tp,tp+fp), 'recall': ratio(tp,tp+fn), 'f1': ratio(2*tp,2*tp+fp+fn)}

def index(rows, gold=False):
    result = {}
    for row in rows:
        if gold and row.get('eligibility',{}).get('evaluable') is not True:
            raise ValueError('excluded or unreviewed gold record cannot be scored')
        task = row.get('task_id')
        if not isinstance(task,str) or not task:
            raise ValueError('every row needs a nonempty task_id')
        if task in result:
            raise ValueError('duplicate task_id: '+task)
        values = row['reference']['class_ids'] if gold else row['labels']
        result[task] = label_set(values)
    return result

def summarize_group(labels, gold, pred, per_class):
    """Project label columns, never filter rows or collapse a process to binary."""
    label_ids = tuple(labels)
    mask = set(label_ids)
    values = [per_class[c] for c in label_ids]
    total = {k:sum(v[k] for v in values) for k in ('tp','fp','fn','tn')}
    macro = {k:sum(v[k] for v in values)/len(label_ids) for k in ('precision','recall','f1')}
    supported = [v for v in values if v['support']]
    supported_macro = {k:ratio(sum(v[k] for v in supported),len(supported)) for k in macro}
    exact = positive = predicted_positive = 0
    for task, truth in gold.items():
        actual, guess = truth & mask, pred[task] & mask
        exact += actual == guess
        positive += bool(actual)
        predicted_positive += bool(guess)
    n = len(gold)
    micro = prf(total['tp'],total['fp'],total['fn'])
    return {**total, **micro,
            'label_ids':list(label_ids), 'n_samples':n, 'n_labels':len(label_ids),
            'n_supported_labels':len(supported), 'support':total['tp']+total['fn'],
            'positive_samples':positive, 'predicted_positive_samples':predicted_positive,
            'exact_matches':exact, 'acc':exact/n, 'subset_accuracy':exact/n,
            'micro_f1':micro['f1'], 'macro_f1':macro['f1'], 'macro':macro,
            'macro_supported_classes_only':supported_macro,
            'hamming_accuracy':(total['tp']+total['tn'])/(n*len(label_ids)),
            'hamming_loss':(total['fp']+total['fn'])/(n*len(label_ids))}

def evaluate(gold_rows, prediction_rows, missing='error'):
    if missing not in ('error','empty'):
        raise ValueError('missing policy must be error or empty')
    gold, pred = index(gold_rows,True), index(prediction_rows)
    if not gold:
        raise ValueError('no eligible samples; scores are undefined, not zero')
    extra = set(pred)-set(gold)
    absent = set(gold)-set(pred)
    if extra:
        raise ValueError('predictions for unknown/excluded task IDs: '+str(sorted(extra)))
    if absent and missing == 'error':
        raise ValueError('missing predictions: '+str(sorted(absent)))
    for task in absent:
        pred[task] = set()
    counts = {c: {'tp':0,'fp':0,'fn':0,'tn':0} for c in LABELS}
    examples = []
    for task, truth in gold.items():
        guess = pred[task]
        tp,fp,fn = len(truth&guess),len(guess-truth),len(truth-guess)
        for c in LABELS:
            counts[c]['tp' if c in truth and c in guess else 'fn' if c in truth else 'fp' if c in guess else 'tn'] += 1
        examples.append({'task_id':task, **prf(tp,fp,fn), 'exact_match': int(truth==guess),
                         'jaccard': len(truth&guess)/len(truth|guess) if truth|guess else 1.0,
                         'gold': sorted(truth), 'predicted': sorted(guess),
                         'missed': sorted(truth-guess), 'extra': sorted(guess-truth)})
    n = len(gold)
    per_class = {c:{**v, 'support':v['tp']+v['fn'], **prf(v['tp'],v['fp'],v['fn'])} for c,v in counts.items()}
    overall = summarize_group(LABELS,gold,pred,per_class)
    total = {k:overall[k] for k in ('tp','fp','fn','tn')}
    process = {}
    for prefix,name in PROCESSES.items():
        process[name] = {'process_id':prefix,
                         **summarize_group([c for c in LABELS if c.startswith(prefix)],gold,pred,per_class)}
    process_average = {k:sum(v[k] for v in process.values())/len(process)
                       for k in ('acc','precision','recall','micro_f1','macro_f1')}
    return {'schema_version':'geotask19.metrics.v2','n_samples':n, 'n_labels':len(LABELS),
            'submitted_predictions':len(prediction_rows), 'missing_predictions':sorted(absent), 'missing_policy':missing,
            'overall':overall, 'process_average':process_average,
            'subset_accuracy':overall['acc'],
            'hamming_accuracy':overall['hamming_accuracy'],
            'hamming_loss':overall['hamming_loss'],
            'sample_jaccard':sum(x['jaccard'] for x in examples)/n,
            'micro':{**total,**prf(total['tp'],total['fp'],total['fn'])}, 'macro':overall['macro'],
            'macro_supported_classes_only':overall['macro_supported_classes_only'],
            'samples':{k:sum(v[k] for v in examples)/n for k in ('precision','recall','f1')},
            'per_class':per_class, 'per_process':process, 'per_sample':examples,
            'zero_division':0, 'empty_empty_jaccard':1,
            'metric_definitions':{
                'acc':'Subset accuracy: exact equality of gold and predicted label sets after projection to group labels; all samples retained, including empty/empty matches.',
                'precision':'Micro precision: sum(TP) / (sum(TP) + sum(FP)) within the label group.',
                'recall':'Micro recall: sum(TP) / (sum(TP) + sum(FN)) within the label group.',
                'micro_f1':'2*sum(TP) / (2*sum(TP) + sum(FP) + sum(FN)); legacy f1 is the same value.',
                'macro_f1':'Unweighted mean of per-label F1 over the fixed label group, including labels with zero support; zero division is 0.',
                'process_average':'Unweighted mean of the five process scores, not the overall 19-label score.',
                'support':'Number of positive sample-label pairs; positive_samples counts rows with at least one gold label in the group.',
                'scale':'All metric scores are proportions in [0, 1], not percentages.'},
            'interpretation':'Diagnostic agreement with paper-observed GeoTask categories; not expert-certified experimental necessity, plan quality, or scientific validity.'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',default='data/benchmark.jsonl')
    p.add_argument('--predictions',required=True)
    p.add_argument('--missing',choices=('error','empty'),default='error')
    p.add_argument('--output',required=True)
    p.add_argument('--artifacts-dir',help='Fresh scoring bundle directory; default: OUTPUT.artifacts')
    a=p.parse_args()
    folder=Path(a.artifacts_dir) if a.artifacts_dir else Path(str(a.output)+'.artifacts')
    if folder.exists() or Path(a.output).exists():
        raise FileExistsError('refusing to overwrite a previous scoring run')
    if folder.resolve()==Path(a.output).resolve() or folder.resolve() in Path(a.output).resolve().parents:
        raise ValueError('--output must be outside --artifacts-dir')
    try:
        gold=load_jsonl(a.data);predictions=load_jsonl(a.predictions)
        report=evaluate(gold,predictions,a.missing)
        scoring_bundle(folder,report,gold,predictions,
                       {'source':'metrics_cli','dataset_sha256':digest(a.data),
                        'predictions_sha256':digest(a.predictions),'scorer_sha256':digest(__file__),
                        'partial_run':bool(report['missing_predictions'])})
        write_json(a.output,report)
    except Exception as exc:
        # Preserve a safe failure receipt even for malformed JSON or strict missing-ID errors.
        write_json(folder/'scoring_error.json',{'error_type':type(exc).__name__,'time':now(),
                   'missing_policy':a.missing,'status':'failed','metrics_file_written':Path(a.output).exists()})
        raise
    print(json.dumps({k:report[k] for k in ('n_samples','overall','per_process','process_average',
                                         'subset_accuracy','sample_jaccard','micro','macro')},ensure_ascii=False))

if __name__=='__main__':
    main()
