#!/usr/bin/env python3
"""
Reproducible Scorer for HackerRank Orchestrate: Ghost Data Still Exists.
Evaluates agent predictions against dataset/sample_output.csv.

Computes:
- Status Accuracy: Agreement on COMPLETE / INCOMPLETE / NOT_APPLICABLE.
- Exact-Row Accuracy: Full match on status, step sequence, operations, states, and cited evidence IDs.
- Step Precision and Recall (ignoring evidence IDs): Correctness of discovered resources, phases, operations, and states.
- Step Precision and Recall (with evidence IDs): Strict correctness including cited evidence IDs.
- Evidence-ID Accuracy: Accuracy of evidence citations for identified resources.
"""

import argparse
from collections import Counter
import csv
import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(REPO_ROOT, 'dataset')
SAMPLE_OUTPUT_PATH = os.path.join(DATASET_DIR, 'sample_output.csv')
SAMPLE_REQUESTS_PATH = os.path.join(DATASET_DIR, 'sample_deletion_requests.csv')

def load_gold_sample_output(gold_path=SAMPLE_OUTPUT_PATH):
    if not os.path.exists(gold_path):
        raise FileNotFoundError(f"Golden sample output file not found: {gold_path}")
    with open(gold_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return {r['case_id']: r for r in reader}

def parse_steps(steps_raw):
    if isinstance(steps_raw, list):
        return steps_raw
    if not steps_raw or steps_raw == '[]':
        return []
    try:
        return json.loads(steps_raw)
    except Exception as e:
        return []

def score_predictions(predictions, gold_rows=None):
    """
    Score a collection of prediction rows against gold answers.
    predictions can be a list of dicts or dict keyed by case_id.
    """
    if gold_rows is None:
        gold_rows = load_gold_sample_output()

    if isinstance(predictions, list):
        pred_dict = {r['case_id']: r for r in predictions}
    else:
        pred_dict = predictions

    evaluated_cases = [cid for cid in gold_rows.keys() if cid in pred_dict]
    if not evaluated_cases:
        return {
            'error': 'No matching cases found between predictions and gold set.',
            'total_gold': len(gold_rows),
            'total_pred': len(pred_dict)
        }

    status_correct = 0
    exact_rows = 0

    total_pred_steps = 0
    total_gold_steps = 0
    matched_steps_no_ev = 0
    matched_steps_with_ev = 0

    total_ev_cited_in_pred = 0
    correct_ev_cited_in_pred = 0

    case_details = {}

    for cid in sorted(evaluated_cases, key=lambda x: int(x.split('_')[1]) if '_' in x and x.split('_')[1].isdigit() else x):
        gold = gold_rows[cid]
        pred = pred_dict[cid]

        gold_status = gold['deletion_status'].strip()
        pred_status = pred['deletion_status'].strip()

        status_match = (gold_status == pred_status)
        if status_match:
            status_correct += 1

        g_steps = parse_steps(gold['steps_json'])
        p_steps = parse_steps(pred['steps_json'])

        row_match = (status_match and g_steps == p_steps)
        if row_match:
            exact_rows += 1

        total_pred_steps += len(p_steps)
        total_gold_steps += len(g_steps)

        # Tuples without evidence: (phase, system_id, resource_id, operation, state)
        p_tuples_no_ev = [(s.get('phase'), s.get('system_id'), s.get('resource_id'), s.get('operation'), s.get('state')) for s in p_steps]
        g_tuples_no_ev = [(s.get('phase'), s.get('system_id'), s.get('resource_id'), s.get('operation'), s.get('state')) for s in g_steps]

        p_cnt_no_ev = Counter(p_tuples_no_ev)
        g_cnt_no_ev = Counter(g_tuples_no_ev)
        common_no_ev = sum((p_cnt_no_ev & g_cnt_no_ev).values())
        matched_steps_no_ev += common_no_ev

        # Tuples with evidence: (phase, system_id, resource_id, operation, state, evidence_id)
        p_tuples_with_ev = [(s.get('phase'), s.get('system_id'), s.get('resource_id'), s.get('operation'), s.get('state'), s.get('evidence_id', '')) for s in p_steps]
        g_tuples_with_ev = [(s.get('phase'), s.get('system_id'), s.get('resource_id'), s.get('operation'), s.get('state'), s.get('evidence_id', '')) for s in g_steps]

        p_cnt_with_ev = Counter(p_tuples_with_ev)
        g_cnt_with_ev = Counter(g_tuples_with_ev)
        common_with_ev = sum((p_cnt_with_ev & g_cnt_with_ev).values())
        matched_steps_with_ev += common_with_ev

        # Evidence-ID Accuracy on citations
        for ps in p_steps:
            peid = ps.get('evidence_id', '').strip()
            if peid:
                total_ev_cited_in_pred += 1
                # Check if gold has matching resource & phase with matching evidence_id
                matched = False
                for gs in g_steps:
                    if (gs.get('phase'), gs.get('system_id'), gs.get('resource_id')) == (ps.get('phase'), ps.get('system_id'), ps.get('resource_id')):
                        if gs.get('evidence_id', '').strip() == peid:
                            matched = True
                            break
                if matched:
                    correct_ev_cited_in_pred += 1

        case_details[cid] = {
            'status_match': status_match,
            'pred_status': pred_status,
            'gold_status': gold_status,
            'exact_row_match': row_match,
            'pred_step_count': len(p_steps),
            'gold_step_count': len(g_steps)
        }

    n_cases = len(evaluated_cases)
    p_no_ev = matched_steps_no_ev / total_pred_steps if total_pred_steps else 0.0
    r_no_ev = matched_steps_no_ev / total_gold_steps if total_gold_steps else 0.0
    f1_no_ev = 2 * p_no_ev * r_no_ev / (p_no_ev + r_no_ev) if (p_no_ev + r_no_ev) else 0.0

    p_with_ev = matched_steps_with_ev / total_pred_steps if total_pred_steps else 0.0
    r_with_ev = matched_steps_with_ev / total_gold_steps if total_gold_steps else 0.0
    f1_with_ev = 2 * p_with_ev * r_with_ev / (p_with_ev + r_with_ev) if (p_with_ev + r_with_ev) else 0.0

    ev_acc = correct_ev_cited_in_pred / total_ev_cited_in_pred if total_ev_cited_in_pred else 0.0

    return {
        'total_cases': n_cases,
        'status_correct': status_correct,
        'status_accuracy': status_correct / n_cases,
        'exact_rows_correct': exact_rows,
        'exact_row_accuracy': exact_rows / n_cases,
        'total_pred_steps': total_pred_steps,
        'total_gold_steps': total_gold_steps,
        'step_matches_no_evidence': matched_steps_no_ev,
        'step_precision_no_evidence': p_no_ev,
        'step_recall_no_evidence': r_no_ev,
        'step_f1_no_evidence': f1_no_ev,
        'step_matches_with_evidence': matched_steps_with_ev,
        'step_precision_with_evidence': p_with_ev,
        'step_recall_with_evidence': r_with_ev,
        'step_f1_with_evidence': f1_with_ev,
        'total_evidence_citations': total_ev_cited_in_pred,
        'correct_evidence_citations': correct_ev_cited_in_pred,
        'evidence_id_accuracy': ev_acc,
        'case_details': case_details
    }

def print_metrics(metrics, title="EVALUATION METRICS"):
    print("=" * 70)
    print(f" {title}")
    print("=" * 70)
    if 'error' in metrics:
        print(f"ERROR: {metrics['error']}")
        return

    n = metrics['total_cases']
    sc = metrics['status_correct']
    sa = metrics['status_accuracy']
    ec = metrics['exact_rows_correct']
    ea = metrics['exact_row_accuracy']

    print(f"Total Evaluated Cases:         {n}")
    print(f"Status Accuracy:               {sc}/{n} ({sa * 100:.2f}%)")
    print(f"Exact Row Accuracy:            {ec}/{n} ({ea * 100:.2f}%)")
    print("-" * 70)
    print(f"Total Steps (Pred / Gold):     {metrics['total_pred_steps']} / {metrics['total_gold_steps']}")
    print(f"Step Matches (Ignoring Ev ID): {metrics['step_matches_no_evidence']}")
    print(f"  Precision:                   {metrics['step_precision_no_evidence']:.4f} ({metrics['step_precision_no_evidence'] * 100:.2f}%)")
    print(f"  Recall:                      {metrics['step_recall_no_evidence']:.4f} ({metrics['step_recall_no_evidence'] * 100:.2f}%)")
    print(f"  F1 Score:                    {metrics['step_f1_no_evidence']:.4f}")
    print("-" * 70)
    print(f"Step Matches (Strict Ev ID):   {metrics['step_matches_with_evidence']}")
    print(f"  Strict Precision:            {metrics['step_precision_with_evidence']:.4f} ({metrics['step_precision_with_evidence'] * 100:.2f}%)")
    print(f"  Strict Recall:               {metrics['step_recall_with_evidence']:.4f} ({metrics['step_recall_with_evidence'] * 100:.2f}%)")
    print(f"  Strict F1 Score:             {metrics['step_f1_with_evidence']:.4f}")
    print("-" * 70)
    print(f"Evidence Citations Evaluated:  {metrics['total_evidence_citations']}")
    print(f"Evidence ID Accuracy:          {metrics['correct_evidence_citations']}/{metrics['total_evidence_citations']} ({metrics['evidence_id_accuracy'] * 100:.2f}%)")
    print("=" * 70)

def run_predictions_from_module(module_obj):
    if hasattr(module_obj, 'run_pipeline'):
        return module_obj.run_pipeline(SAMPLE_REQUESTS_PATH, None, DATASET_DIR, verify=False)

    with open(SAMPLE_REQUESTS_PATH, 'r', encoding='utf-8') as f:
        sample_reqs = list(csv.DictReader(f))
    with open(os.path.join(DATASET_DIR, 'user_identities.csv'), 'r', encoding='utf-8') as f:
        identities = list(csv.DictReader(f))
    with open(os.path.join(DATASET_DIR, 'user_audit_log.csv'), 'r', encoding='utf-8') as f:
        audit_logs = list(csv.DictReader(f))
    with open(os.path.join(DATASET_DIR, 'system_activity.csv'), 'r', encoding='utf-8') as f:
        system_activities = list(csv.DictReader(f))
    with open(os.path.join(DATASET_DIR, 'evidence_files.csv'), 'r', encoding='utf-8') as f:
        evidence_manifest = list(csv.DictReader(f))

    ocr_cache = module_obj.load_ocr_cache()

    predictions = []
    sample_reqs_sorted = sorted(sample_reqs, key=lambda x: int(x['case_id'].split('_')[1]))

    for req in sample_reqs_sorted:
        cid = req['case_id']
        email = req['email']
        audit_at = req['audit_at']
        text = req['request_text']

        if module_obj.is_non_deletion_request(text):
            predictions.append({'case_id': cid, 'deletion_status': 'NOT_APPLICABLE', 'steps_json': '[]'})
            continue

        user_info = module_obj.get_identity_info(email, audit_at, identities, audit_logs)
        if not user_info:
            user_info = {
                'user_id': f"user_{cid.split('_')[1]}",
                'name': '',
                'email': email,
                'all_emails': [email.lower()],
                'phone': '',
                'email_hash': ''
            }

        resources, primary_eid = module_obj.query_case_resources(
            user_info, audit_at, evidence_manifest, system_activities, ocr_cache
        )
        status, steps = module_obj.build_case_steps(resources, primary_eid)
        predictions.append({'case_id': cid, 'deletion_status': status, 'steps_json': json.dumps(steps)})

    return predictions

def evaluate_commit(commit_ish):
    import importlib.util
    code_content = subprocess.check_output(['git', 'show', f'{commit_ish}:code/main.py'], cwd=REPO_ROOT, text=True)
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8') as tmp:
        tmp.write(code_content)
        tmp_path = tmp.name

    try:
        spec = importlib.util.spec_from_file_location("commit_main", tmp_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # Ensure DATASET_DIR is correctly anchored
        mod.DATASET_DIR = DATASET_DIR
        mod.REPO_ROOT = REPO_ROOT
        preds = run_predictions_from_module(mod)
        return score_predictions(preds)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

def main():
    parser = argparse.ArgumentParser(description="Score predictions against dataset/sample_output.csv")
    parser.add_argument('--file', type=str, help="Path to a predictions CSV file containing case_id, deletion_status, steps_json")
    parser.add_argument('--eval-current', action='store_true', help="Run evaluation for current code/main.py on sample_deletion_requests.csv")
    parser.add_argument('--eval-commit', type=str, help="Evaluate a specific git commit (e.g. origin/main or HEAD~1)")
    parser.add_argument('--baseline-compare', action='store_true', help="Run comparison between origin/main baseline and current HEAD")

    args = parser.parse_args()

    if args.baseline_compare:
        print("Scoring baseline origin/main commit...")
        main_metrics = evaluate_commit('origin/main')
        print_metrics(main_metrics, title="ORIGIN/MAIN BASELINE (PR #1 / f3ab788)")

        print("\nScoring current HEAD (agent-v2)...")
        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)
        import code.main as cur_main
        cur_preds = run_predictions_from_module(cur_main)
        cur_metrics = score_predictions(cur_preds)
        print_metrics(cur_metrics, title="CURRENT HEAD (agent-v2 / b5f8fcd)")

        print("\n" + "=" * 70)
        print(" SIDE-BY-SIDE BASELINE REPRODUCTION")
        print("=" * 70)
        print(f"{'Metric':<35} | {'origin/main':<15} | {'agent-v2 (HEAD)':<15}")
        print("-" * 70)
        print(f"{'Status Accuracy':<35} | {main_metrics['status_correct']}/{main_metrics['total_cases']:<13} | {cur_metrics['status_correct']}/{cur_metrics['total_cases']:<13}")
        print(f"{'Status Accuracy %':<35} | {main_metrics['status_accuracy']*100:6.2f}%{' '*8} | {cur_metrics['status_accuracy']*100:6.2f}%{' '*8}")
        print(f"{'Exact-Row Accuracy':<35} | {main_metrics['exact_rows_correct']}/{main_metrics['total_cases']:<13} | {cur_metrics['exact_rows_correct']}/{cur_metrics['total_cases']:<13}")
        print(f"{'Step Precision (No Ev ID)':<35} | {main_metrics['step_precision_no_evidence']:6.4f}{' '*9} | {cur_metrics['step_precision_no_evidence']:6.4f}{' '*9}")
        print(f"{'Step Recall (No Ev ID)':<35} | {main_metrics['step_recall_no_evidence']:6.4f}{' '*9} | {cur_metrics['step_recall_no_evidence']:6.4f}{' '*9}")
        print(f"{'Step F1 (No Ev ID)':<35} | {main_metrics['step_f1_no_evidence']:6.4f}{' '*9} | {cur_metrics['step_f1_no_evidence']:6.4f}{' '*9}")
        print(f"{'Evidence ID Accuracy':<35} | {main_metrics['evidence_id_accuracy']*100:6.2f}%{' '*8} | {cur_metrics['evidence_id_accuracy']*100:6.2f}%{' '*8}")
        print("=" * 70)
        return

    if args.eval_commit:
        m = evaluate_commit(args.eval_commit)
        print_metrics(m, title=f"EVALUATION FOR COMMIT: {args.eval_commit}")
        return

    if args.file:
        with open(args.file, 'r', encoding='utf-8') as f:
            preds = list(csv.DictReader(f))
        m = score_predictions(preds)
        print_metrics(m, title=f"EVALUATION FOR FILE: {args.file}")
        return

    # Default to evaluating current code/main.py
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import code.main as cur_main
    preds = run_predictions_from_module(cur_main)
    m = score_predictions(preds)
    print_metrics(m, title="CURRENT HEAD (agent-v2) EVALUATION ON SAMPLE SET")

if __name__ == '__main__':
    main()
