"""
Independent Verifier and Quality Gate Engine for HackerRank Orchestrate.
Performs semantic and evidence validation across all case outputs.
Re-checks evidence existence, cutoff validity, absence coverage, step dependencies,
and ensures completion claims are justified by ground-truth evidence.
"""

import csv
import json
import os
from typing import Any, Dict, List, Optional, Set

from code.audit_engine.step_builder import (
    FINDING_PRIORITY,
    PHASE_PRIORITY,
    REMEDIATION_PRIORITY,
    VERIFICATION_PRIORITY,
    step_sort_key,
)
from code.evidence_store.store import EvidenceStore
from code.verifier.models import VerificationFinding, VerificationResult

VALID_STATUSES = {'COMPLETE', 'INCOMPLETE', 'NOT_APPLICABLE'}
VALID_PHASES = {'FINDING', 'REMEDIATION', 'VERIFICATION'}
VALID_STATES = {'ABSENT', 'PRESENT', 'REDACTED', 'ANONYMIZED', 'DISABLED', 'BLOCKED', 'ACTIVE'}
VALID_SYSTEMS = {
    'account_service',
    'search_service',
    'support_platform',
    'export_service',
    'analytics_platform',
    'security_service',
    'backup_service',
    'integration_service',
    'identity_service'
}


class AuditVerifier:
    def __init__(self, dataset_dir: str, evidence_store: Optional[EvidenceStore] = None):
        self.dataset_dir = dataset_dir
        self.store = evidence_store or EvidenceStore(dataset_dir)
        if not self.store.observations:
            self.store.load_all_evidence()

        self.evidence_manifest: Dict[str, Dict[str, str]] = {}
        self._load_manifest()

    def _load_manifest(self):
        manifest_path = os.path.join(self.dataset_dir, 'evidence_files.csv')
        if os.path.exists(manifest_path):
            with open(manifest_path, 'r', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    self.evidence_manifest[row['file_id']] = row

    def verify_case(
        self,
        request: Dict[str, Any],
        case_output: Dict[str, Any]
    ) -> List[VerificationFinding]:
        """
        Validates a single case output against the request and evidence store.
        """
        findings: List[VerificationFinding] = []
        cid = request['case_id']
        out_cid = case_output.get('case_id')
        status = case_output.get('deletion_status')
        steps_raw = case_output.get('steps_json')
        audit_at = request['audit_at']

        # 1. Basic Schema Validation
        if cid != out_cid:
            findings.append(VerificationFinding(
                case_id=cid,
                check_name="case_id_match",
                severity="ERROR",
                message=f"Case ID mismatch: expected {cid}, got {out_cid}"
            ))

        if status not in VALID_STATUSES:
            findings.append(VerificationFinding(
                case_id=cid,
                check_name="valid_status",
                severity="ERROR",
                message=f"Invalid deletion_status '{status}'. Must be COMPLETE, INCOMPLETE, or NOT_APPLICABLE"
            ))

        try:
            steps = json.loads(steps_raw) if isinstance(steps_raw, str) else steps_raw
            if not isinstance(steps, list):
                raise ValueError("steps_json must parse to a JSON array")
        except Exception as e:
            findings.append(VerificationFinding(
                case_id=cid,
                check_name="steps_json_parse",
                severity="ERROR",
                message=f"Invalid steps_json: {e}"
            ))
            return findings

        # 2. NOT_APPLICABLE Contract
        if status == 'NOT_APPLICABLE':
            if steps:
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="not_applicable_empty_steps",
                    severity="ERROR",
                    message=f"NOT_APPLICABLE status must have empty steps_json, got {len(steps)} steps"
                ))
            return findings

        # 3. Non-empty steps for deletion requests
        if not steps:
            findings.append(VerificationFinding(
                case_id=cid,
                check_name="non_empty_steps",
                severity="ERROR",
                message="Case marked as deletion request has empty steps_json"
            ))
            return findings

        # 4. Step Schema and Sequencing
        expected_step_no = 1
        seen_phases: List[str] = []
        has_finding = False
        has_remediation = False
        has_verification = False

        for idx, s in enumerate(steps):
            step_no = s.get('step_no')
            phase = s.get('phase')
            sys_id = s.get('system_id')
            res_id = s.get('resource_id')
            op = s.get('operation')
            state = s.get('state')
            eid = s.get('evidence_id', '')

            # Check 1-indexed sequential step numbers
            if step_no != expected_step_no:
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="step_number_sequence",
                    severity="ERROR",
                    message=f"Step number out of sequence: expected {expected_step_no}, got {step_no}",
                    step_no=step_no
                ))
            expected_step_no += 1

            # Check required fields
            if phase not in VALID_PHASES:
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="valid_phase",
                    severity="ERROR",
                    message=f"Invalid phase '{phase}' in step {step_no}",
                    step_no=step_no
                ))
            else:
                if not seen_phases or seen_phases[-1] != phase:
                    seen_phases.append(phase)
                if phase == 'FINDING':
                    has_finding = True
                elif phase == 'REMEDIATION':
                    has_remediation = True
                elif phase == 'VERIFICATION':
                    has_verification = True

            if sys_id not in VALID_SYSTEMS:
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="valid_system_id",
                    severity="ERROR",
                    message=f"Invalid system_id '{sys_id}' in step {step_no}",
                    step_no=step_no
                ))

            if state not in VALID_STATES:
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="valid_state",
                    severity="ERROR",
                    message=f"Invalid state '{state}' in step {step_no}",
                    step_no=step_no
                ))

            # 5. Evidence Citations
            if eid:
                if eid not in self.evidence_manifest:
                    findings.append(VerificationFinding(
                        case_id=cid,
                        check_name="evidence_id_exists",
                        severity="ERROR",
                        message=f"Cited evidence_id '{eid}' does not exist in manifest",
                        step_no=step_no
                    ))
                else:
                    ev_meta = self.evidence_manifest[eid]
                    captured_at = ev_meta.get('captured_at', '')
                    # Check temporal cutoff
                    if captured_at > audit_at:
                        findings.append(VerificationFinding(
                            case_id=cid,
                            check_name="evidence_temporal_cutoff",
                            severity="ERROR",
                            message=f"Evidence {eid} captured at {captured_at} is after audit_at {audit_at}",
                            step_no=step_no
                        ))
                    else:
                        # Check semantic grounding of evidence citation
                        obs_for_ev = [o for o in self.store.observations if o.evidence_id == eid]
                        is_covering = any(snap.evidence_id == eid and sys_id in snap.covered_systems for snap in self.store.covering_snapshots)
                        res_obs = [o for o in obs_for_ev if o.resource_id == res_id and o.observed_at <= audit_at]

                        if not res_obs and not is_covering:
                            findings.append(VerificationFinding(
                                case_id=cid,
                                check_name="evidence_semantic_support",
                                severity="ERROR",
                                message=f"Cited evidence '{eid}' in step {step_no} does not observe resource '{res_id}' and is not a covering snapshot for system '{sys_id}'",
                                step_no=step_no
                            ))
                        elif res_obs:
                            if phase == 'FINDING' and all(o.state == 'ABSENT' for o in res_obs):
                                findings.append(VerificationFinding(
                                    case_id=cid,
                                    check_name="evidence_semantic_support",
                                    severity="ERROR",
                                    message=f"Step {step_no} cites evidence '{eid}' as a FINDING of presence/PII, but the evidence observed the resource as ABSENT",
                                    step_no=step_no
                                ))

            # Check proposed remediation steps don't claim past evidence
            if phase == 'REMEDIATION' and eid:
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="remediation_no_evidence",
                    severity="ERROR",
                    message=f"Proposed remediation step {step_no} must have empty evidence_id, got '{eid}'",
                    step_no=step_no
                ))

            # Check absence proof validity
            if op == 'VERIFY_RECORD_ABSENT' and eid:
                snap = next((s for s in self.store.covering_snapshots if s.evidence_id == eid), None)
                if snap and not snap.is_full_store_snapshot:
                    findings.append(VerificationFinding(
                        case_id=cid,
                        check_name="absence_covering_scope",
                        severity="ERROR",
                        message=f"Step {step_no} cites partial snapshot {eid} to prove absence; must be full store",
                        step_no=step_no
                    ))

        # 6. Phase Sequence Check: FINDING -> REMEDIATION -> VERIFICATION
        phase_order = [PHASE_PRIORITY.get(p, 99) for p in seen_phases]
        if phase_order != sorted(phase_order):
            findings.append(VerificationFinding(
                case_id=cid,
                check_name="phase_progression",
                severity="ERROR",
                message=f"Phases progressed out of order: {seen_phases}"
            ))

        # 7. Step Sorting and Priority Check
        sorted_steps = sorted(steps, key=step_sort_key)
        for i, (orig, srt) in enumerate(zip(steps, sorted_steps)):
            if (orig['phase'], orig['operation'], orig['system_id'], orig['resource_id']) != \
               (srt['phase'], srt['operation'], srt['system_id'], srt['resource_id']):
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="step_dependency_ordering",
                    severity="ERROR",
                    message=f"Step {i+1} violates dependency/operation ordering contract: got ({orig['operation']}, {orig['system_id']}), expected ({srt['operation']}, {srt['system_id']})",
                    step_no=orig.get('step_no')
                ))
                break

        # 8. Status Justification
        if status == 'COMPLETE':
            if has_finding or has_remediation:
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="complete_status_purity",
                    severity="ERROR",
                    message="COMPLETE status cannot contain FINDING or REMEDIATION steps"
                ))
            # Every verification step in COMPLETE must have evidence_id
            for s in steps:
                if not s.get('evidence_id'):
                    findings.append(VerificationFinding(
                        case_id=cid,
                        check_name="complete_requires_evidence",
                        severity="ERROR",
                        message=f"Step {s.get('step_no')} in COMPLETE status has empty evidence_id",
                        step_no=s.get('step_no')
                    ))
        elif status == 'INCOMPLETE':
            if not has_verification:
                findings.append(VerificationFinding(
                    case_id=cid,
                    check_name="incomplete_requires_verification",
                    severity="ERROR",
                    message="INCOMPLETE status must contain VERIFICATION steps"
                ))
            # Every future verification step in INCOMPLETE must have empty evidence_id
            for s in steps:
                if s.get('phase') == 'VERIFICATION' and s.get('evidence_id'):
                    findings.append(VerificationFinding(
                        case_id=cid,
                        check_name="incomplete_verification_no_evidence",
                        severity="ERROR",
                        message=f"Step {s.get('step_no')} in INCOMPLETE status is a future verification step and must have empty evidence_id, got '{s.get('evidence_id')}'",
                        step_no=s.get('step_no')
                    ))

        return findings

    def verify_all(
        self,
        requests: List[Dict[str, Any]],
        predictions: List[Dict[str, Any]]
    ) -> VerificationResult:
        """
        Runs quality gates across all predictions.
        """
        req_map = {r['case_id']: r for r in requests}
        all_findings: List[VerificationFinding] = []
        passed = 0
        failed = 0

        for pred in predictions:
            cid = pred.get('case_id')
            req = req_map.get(cid)
            if not req:
                all_findings.append(VerificationFinding(
                    case_id=cid or 'UNKNOWN',
                    check_name="request_exists",
                    severity="ERROR",
                    message=f"Case {cid} has no matching input deletion request"
                ))
                failed += 1
                continue

            findings = self.verify_case(req, pred)
            if any(f.severity == 'ERROR' for f in findings):
                failed += 1
            else:
                passed += 1
            all_findings.extend(findings)

        return VerificationResult(
            is_valid=(failed == 0),
            total_cases=len(predictions),
            passed_cases=passed,
            failed_cases=failed,
            findings=all_findings
        )
