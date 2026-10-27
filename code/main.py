"""
Ghost Data Still Exists: HackerRank Orchestrate Autonomous Evidence-Grounded Audit Agent.
Main executable entry point for processing deletion requests across multi-system evidence.
"""

import argparse
import csv
import json
import os
import sys
from typing import Dict, List, Optional, Any

# Ensure project root is in sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from code.evidence_store.store import EvidenceStore
from code.identity.resolver import IdentityResolver
from code.policy.engine import PolicyEngine
from code.audit_engine.engine import AuditEngine
from code.audit_engine.intent import is_non_deletion_request
from code.audit_engine.step_builder import StepBuilder
from code.verifier.verifier import AuditVerifier
from code.evaluation.scorer import score_predictions, print_metrics

# Backward-compatible DELETION_RULES dictionary
DELETION_RULES = {
    'USER_PROFILE': 'DELETE',
    'COMMUNICATION_ATTACHMENT': 'DELETE',
    'SEARCH_INDEX_ENTRY': 'DELETE',
    'CUSTOMER_EXPORT': 'DELETE',
    'ACCOUNT_TOMBSTONE': 'REDACT',
    'SUPPORT_MESSAGE': 'REDACT',
    'ANALYTICS_PROFILE': 'ANONYMIZE',
    'DATA_FLOW': 'DISABLE',
    'RESTORE_ENTRY': 'BLOCK',
    'SECURITY_AUDIT_LOG': 'RETAIN',
    'FINANCIAL_LEDGER_RECORD': 'RETAIN',
    'TAX_COMPLIANCE_RECORD': 'RETAIN',
    'LEGAL_HOLD_RECORD': 'RETAIN',
    'REGULATORY_ARCHIVE_ENTRY': 'RETAIN',
    'ACCESS_AUTHORIZATION_RECORD': 'RETAIN'
}


def get_identity_info(req_email, audit_at, identities=None, audit_logs=None):
    """
    Backward-compatible wrapper around IdentityResolver.
    """
    dataset_dir = os.path.join(REPO_ROOT, "dataset")
    resolver = IdentityResolver(dataset_dir)
    resolved = resolver.resolve_identity(req_email, audit_at)
    if not resolved:
        return None
    return {
        'user_id': resolved.user_id,
        'name': resolved.name,
        'email': resolved.email_at_cutoff,
        'all_emails': resolved.active_emails,
        'phone': resolved.phone,
        'email_hash': resolved.email_hash
    }


def build_case_steps(resource_list, primary_evidence_id=""):
    """
    Backward-compatible step builder wrapper.
    """
    policy = PolicyEngine(os.path.join(REPO_ROOT, "dataset", "deletion_rules.csv"))
    builder = StepBuilder()
    is_complete = all(r.get('is_compliant', False) and r.get('evidence_id') for r in resource_list)

    if is_complete:
        status = "COMPLETE"
        for r in resource_list:
            sys_id = r['system_id']
            res_id = r['resource_id']
            eid = r.get('evidence_id', '')
            target = policy.get_target_state(sys_id, res_id)
            op, st = policy.get_verification_operation(sys_id, res_id, target)
            builder.add_step("VERIFICATION", sys_id, res_id, op.value, st.value, eid)
    else:
        status = "INCOMPLETE"
        for r in resource_list:
            sys_id = r['system_id']
            res_id = r['resource_id']
            eid = r.get('evidence_id', '')
            is_attach = r.get('is_attachment', False)
            if not r.get('is_compliant', False):
                cat = 'SUPPORT_ATTACHMENT' if is_attach else r.get('category')
                find_op, find_st = policy.get_finding_operation(sys_id, res_id, r.get('state', 'PRESENT'), category=cat)
                rem_op, rem_st = policy.get_remediation_operation(sys_id, res_id, category=cat)
                builder.add_step("FINDING", sys_id, res_id, find_op.value, find_st.value, eid or primary_evidence_id)
                builder.add_step("REMEDIATION", sys_id, res_id, rem_op.value, rem_st.value, "")

        for r in resource_list:
            sys_id = r['system_id']
            res_id = r['resource_id']
            eid = ""  # Future verification steps in INCOMPLETE status must have empty evidence_id
            target = policy.get_target_state(sys_id, res_id)
            ver_op, ver_st = policy.get_verification_operation(sys_id, res_id, target)
            builder.add_step("VERIFICATION", sys_id, res_id, ver_op.value, ver_st.value, eid)

    steps = builder.build()
    return status, steps


def query_case_resources(user_info, audit_at, evidence_manifest=None, system_activities=None, ocr_cache=None):
    """
    Backward-compatible resource query wrapper.
    """
    dataset_dir = os.path.join(REPO_ROOT, "dataset")
    store = EvidenceStore(dataset_dir)
    store.load_all_evidence()
    obs = store.get_observations_for_user(
        user_id=user_info.get('user_id'),
        emails=user_info.get('all_emails', []),
        audit_at=audit_at
    )
    res_list = []
    for o in obs:
        if o.resource_id:
            res_list.append({
                'system_id': o.system_id,
                'resource_id': o.resource_id,
                'state': o.state,
                'is_compliant': (o.system_id == 'security_service'),
                'evidence_id': o.evidence_id,
                'is_attachment': False
            })
    return res_list, ""


def load_ocr_cache():
    """
    Backward-compatible stub for OCR cache loader.
    OCR text extraction is handled within EvidenceStore parsers.
    """
    return {}


def build_engine(dataset_dir: str, deletion_requests: Optional[List[Dict[str, Any]]] = None) -> AuditEngine:
    """
    Initializes and wires the EvidenceStore, IdentityResolver, PolicyEngine,
    and AuditEngine for full repository audit execution.
    """
    store = EvidenceStore(dataset_dir)
    store.load_all_evidence()

    resolver = IdentityResolver(dataset_dir)
    policy = PolicyEngine(os.path.join(dataset_dir, "deletion_rules.csv"))

    activity_path = os.path.join(dataset_dir, "system_activity.csv")
    activities = []
    if os.path.exists(activity_path):
        with open(activity_path, "r", encoding="utf-8") as f:
            activities = list(csv.DictReader(f))

    engine = AuditEngine(
        evidence_store=store,
        identity_resolver=resolver,
        policy_engine=policy,
        system_activities=activities,
        deletion_requests=deletion_requests or []
    )
    return engine


def run_pipeline(
    requests_csv: str,
    output_csv: str,
    dataset_dir: str,
    verify: bool = True
) -> List[Dict[str, Any]]:
    """
    Executes the audit pipeline on input deletion requests, writes output CSV,
    and optionally executes the independent quality gate verifier.
    """
    if not os.path.exists(requests_csv):
        raise FileNotFoundError(f"Requests file not found: {requests_csv}")

    with open(requests_csv, "r", encoding="utf-8") as f:
        requests = list(csv.DictReader(f))

    print(f"Loaded {len(requests)} deletion requests from {requests_csv}.")

    engine = build_engine(dataset_dir, deletion_requests=requests)

    results = []
    for req in requests:
        pred = engine.process_request(req)
        results.append(pred)

    # Deterministic output: preserve case order
    if output_csv:
        os.makedirs(os.path.dirname(os.path.abspath(output_csv)), exist_ok=True)
        with open(output_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["case_id", "deletion_status", "steps_json"])
            writer.writeheader()
            writer.writerows(results)
        print(f"Generated {len(results)} output rows written to {output_csv}.")

    if verify:
        print("Running independent verifier quality gates...")
        verifier = AuditVerifier(dataset_dir, engine.store)
        res = verifier.verify_all(requests, results)
        errors = [f for f in res.findings if f.severity == 'ERROR']
        warnings = [f for f in res.findings if f.severity == 'WARNING']
        print(f"Verifier status: {'PASSED' if res.is_valid else 'FAILED'}. Passed: {res.passed_cases}/{res.total_cases}, Errors: {len(errors)}, Warnings: {len(warnings)}")
        if not res.is_valid:
            print(f"WARNING: Verifier detected {len(errors)} errors in generated output!")
            for err in errors[:10]:
                print(f"  [{err.check_name}] Case {err.case_id}: {err.message}")

    return results


def process_all_requests(output_csv: Optional[str] = None):
    """
    Legacy backward-compatible entrypoint.
    Processes all requests in dataset/deletion_requests.csv and writes to output.csv.
    """
    dataset_dir = os.path.join(REPO_ROOT, "dataset")
    requests_csv = os.path.join(dataset_dir, "deletion_requests.csv")
    out_path = output_csv or os.path.join(REPO_ROOT, "output.csv")
    return run_pipeline(requests_csv, out_path, dataset_dir, verify=True)


def main():
    parser = argparse.ArgumentParser(
        description="Ghost Data Still Exists: Evidence-Grounded Audit Pipeline"
    )
    parser.add_argument(
        "--requests-csv",
        type=str,
        default=os.path.join(REPO_ROOT, "dataset", "deletion_requests.csv"),
        help="Path to input deletion requests CSV"
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default=os.path.join(REPO_ROOT, "output.csv"),
        help="Path to write output CSV"
    )
    parser.add_argument(
        "--dataset-dir",
        type=str,
        default=os.path.join(REPO_ROOT, "dataset"),
        help="Path to dataset directory"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="Run independent quality gates verifier on generated output"
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Run against sample requests and evaluate against sample gold output"
    )

    args = parser.parse_args()

    if args.sample:
        sample_reqs = os.path.join(args.dataset_dir, "sample_deletion_requests.csv")
        sample_gold = os.path.join(args.dataset_dir, "sample_output.csv")
        sample_out = os.path.join(REPO_ROOT, "scratch", "sample_output_eval.csv")
        preds = run_pipeline(sample_reqs, sample_out, args.dataset_dir, verify=True)
        metrics = score_predictions(preds)
        print_metrics(metrics, title="SAMPLE CASES EVALUATION")
    else:
        run_pipeline(args.requests_csv, args.output_csv, args.dataset_dir, verify=args.verify)


if __name__ == "__main__":
    main()
