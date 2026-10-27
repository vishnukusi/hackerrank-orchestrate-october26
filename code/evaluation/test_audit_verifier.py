"""
Unit tests for Independent Verifier (Phase 5).
"""

import csv
import json
import os
import unittest

from code.evidence_store.store import EvidenceStore
from code.verifier import AuditVerifier

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")


class TestAuditVerifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = EvidenceStore(DATASET_DIR)
        cls.store.load_all_evidence()
        cls.verifier = AuditVerifier(DATASET_DIR, cls.store)

    def test_verify_all_32_sample_gold_cases_pass(self):
        with open(os.path.join(DATASET_DIR, "sample_deletion_requests.csv"), "r", encoding="utf-8") as f:
            requests = list(csv.DictReader(f))
        with open(os.path.join(DATASET_DIR, "sample_output.csv"), "r", encoding="utf-8") as f:
            sample_output = list(csv.DictReader(f))

        result = self.verifier.verify_all(requests, sample_output)
        self.assertTrue(result.is_valid, f"Verification failed on sample output: {[f.message for f in result.findings]}")
        self.assertEqual(result.failed_cases, 0)
        self.assertEqual(result.passed_cases, 32)

    def test_reject_future_dated_evidence(self):
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-08-01T00:00:00Z',
            'email': 'test@example.com'
        }
        # evidence_54 is captured in late August 2026, which is after 2026-08-01
        output = {
            'case_id': 'case_999',
            'deletion_status': 'COMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'VERIFICATION',
                'system_id': 'account_service',
                'resource_id': 'account_001',
                'operation': 'VERIFY_RECORD_ABSENT',
                'state': 'ABSENT',
                'evidence_id': 'evidence_54'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "evidence_temporal_cutoff" for f in findings))

    def test_reject_fabricated_evidence_id(self):
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        output = {
            'case_id': 'case_999',
            'deletion_status': 'COMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'VERIFICATION',
                'system_id': 'account_service',
                'resource_id': 'account_001',
                'operation': 'VERIFY_RECORD_ABSENT',
                'state': 'ABSENT',
                'evidence_id': 'evidence_fabricated_9999'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "evidence_id_exists" for f in findings))

    def test_reject_complete_without_evidence_id(self):
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        output = {
            'case_id': 'case_999',
            'deletion_status': 'COMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'VERIFICATION',
                'system_id': 'account_service',
                'resource_id': 'account_001',
                'operation': 'VERIFY_RECORD_ABSENT',
                'state': 'ABSENT',
                'evidence_id': ''
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "complete_requires_evidence" for f in findings))

    def test_reject_not_applicable_with_steps(self):
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        output = {
            'case_id': 'case_999',
            'deletion_status': 'NOT_APPLICABLE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'VERIFICATION',
                'system_id': 'account_service',
                'resource_id': 'account_001',
                'operation': 'VERIFY_RECORD_ABSENT',
                'state': 'ABSENT',
                'evidence_id': 'evidence_54'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "not_applicable_empty_steps" for f in findings))

    def test_reject_contradictory_evidence_finding(self):
        """Verifier must reject citing an evidence file as a FINDING of presence if evidence showed ABSENT."""
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-10-01T12:00:00Z',
            'email': 'noor-khalil@proton.me'
        }
        # In evidence_03, account_9335 was observed as ABSENT. Citing it as FINDING must fail.
        output = {
            'case_id': 'case_999',
            'deletion_status': 'INCOMPLETE',
            'steps_json': json.dumps([
                {
                    'step_no': 1,
                    'phase': 'FINDING',
                    'system_id': 'account_service',
                    'resource_id': 'account_9335',
                    'operation': 'REPORT_REMAINING_RECORD',
                    'state': 'PRESENT',
                    'evidence_id': 'evidence_03'
                },
                {
                    'step_no': 2,
                    'phase': 'REMEDIATION',
                    'system_id': 'account_service',
                    'resource_id': 'account_9335',
                    'operation': 'DELETE_RECORD',
                    'state': 'ABSENT',
                    'evidence_id': ''
                },
                {
                    'step_no': 3,
                    'phase': 'VERIFICATION',
                    'system_id': 'account_service',
                    'resource_id': 'account_9335',
                    'operation': 'VERIFY_RECORD_ABSENT',
                    'state': 'ABSENT',
                    'evidence_id': ''
                }
            ])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name in ("evidence_semantic_support", "evidence_state_contradiction") for f in findings))

    def test_reject_unrelated_evidence_citation(self):
        """Verifier must reject citing evidence that has no observation of resource and does not cover system."""
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        # evidence_01 is a log file that doesn't cover search_service for an unrelated resource
        output = {
            'case_id': 'case_999',
            'deletion_status': 'COMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'VERIFICATION',
                'system_id': 'analytics_platform',
                'resource_id': 'analytics_completely_fake_999',
                'operation': 'VERIFY_RECORD_ANONYMIZED',
                'state': 'ANONYMIZED',
                'evidence_id': 'evidence_01'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "evidence_semantic_support" for f in findings))

    def test_reject_foreign_user_evidence_citation(self):
        """Verifier must reject citing evidence observation that belongs to a different user."""
        req = {
            'case_id': 'case_01',
            'audit_at': '2026-10-05T00:00:00Z',
            'email': 'amina.elmasri@gmail.com'
        }
        # evidence_03 has account_9335 belonging to noor-khalil@proton.me (user_02)
        output = {
            'case_id': 'case_01',
            'deletion_status': 'COMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'VERIFICATION',
                'system_id': 'account_service',
                'resource_id': 'account_9335',
                'operation': 'VERIFY_RECORD_ABSENT',
                'state': 'ABSENT',
                'evidence_id': 'evidence_03'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "evidence_user_mismatch" for f in findings))

    def test_reject_evidence_system_mismatch(self):
        """Verifier must reject citing evidence for resource under the wrong system."""
        req = {
            'case_id': 'case_02',
            'audit_at': '2026-10-01T12:00:00Z',
            'email': 'noor-khalil@proton.me'
        }
        # account_9335 is in account_service in evidence_03, not search_service
        output = {
            'case_id': 'case_02',
            'deletion_status': 'INCOMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'FINDING',
                'system_id': 'search_service',
                'resource_id': 'account_9335',
                'operation': 'REPORT_REMAINING_RECORD',
                'state': 'PRESENT',
                'evidence_id': 'evidence_03'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "evidence_system_mismatch" for f in findings))

    def test_reject_state_contradiction_verification_absent(self):
        """Verifier must reject VERIFY_RECORD_ABSENT when cited evidence shows PRESENT."""
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'bcarvalho6517@gmail.com'
        }
        # In evidence_05, analytics_1738 is PRESENT
        output = {
            'case_id': 'case_999',
            'deletion_status': 'COMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'VERIFICATION',
                'system_id': 'analytics_platform',
                'resource_id': 'analytics_1738',
                'operation': 'VERIFY_RECORD_ABSENT',
                'state': 'ABSENT',
                'evidence_id': 'evidence_05'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "evidence_state_contradiction" for f in findings))

    def test_reject_state_contradiction_verification_present(self):
        """Verifier must reject VERIFY_RECORD_PRESENT when cited evidence shows ABSENT."""
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-10-01T12:00:00Z',
            'email': 'noor-khalil@proton.me'
        }
        output = {
            'case_id': 'case_999',
            'deletion_status': 'COMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'VERIFICATION',
                'system_id': 'account_service',
                'resource_id': 'account_9335',
                'operation': 'VERIFY_RECORD_PRESENT',
                'state': 'PRESENT',
                'evidence_id': 'evidence_03'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "evidence_state_contradiction" for f in findings))

    def test_reject_retention_policy_violation(self):
        """Verifier must reject DELETE_RECORD or VERIFY_RECORD_ABSENT on security_service."""
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        output = {
            'case_id': 'case_999',
            'deletion_status': 'INCOMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'REMEDIATION',
                'system_id': 'security_service',
                'resource_id': 'audit_record_999',
                'operation': 'DELETE_RECORD',
                'state': 'ABSENT',
                'evidence_id': ''
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "retention_policy_violation" for f in findings))

    def test_reject_absence_from_incomplete_snapshot(self):
        """Verifier must reject citing partial snapshot to prove absence."""
        from code.evidence_store.models import CoveringSnapshot
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        fake_snap = CoveringSnapshot(
            evidence_id='evidence_partial_mock',
            file_path='mock/path.json',
            captured_at='2026-09-15T00:00:00Z',
            covered_systems=['account_service'],
            is_full_store_snapshot=False,
            replica_coverage=['primary'],
            total_records=10,
            records_by_system={'account_service': 10}
        )
        self.store.covering_snapshots.append(fake_snap)
        self.verifier.evidence_manifest['evidence_partial_mock'] = {
            'file_id': 'evidence_partial_mock',
            'captured_at': '2026-09-15T00:00:00Z'
        }
        try:
            output = {
                'case_id': 'case_999',
                'deletion_status': 'COMPLETE',
                'steps_json': json.dumps([{
                    'step_no': 1,
                    'phase': 'VERIFICATION',
                    'system_id': 'account_service',
                    'resource_id': 'account_001',
                    'operation': 'VERIFY_RECORD_ABSENT',
                    'state': 'ABSENT',
                    'evidence_id': 'evidence_partial_mock'
                }])
            }
            findings = self.verifier.verify_case(req, output)
            self.assertTrue(any(f.check_name == "absence_covering_scope" for f in findings))
        finally:
            self.store.covering_snapshots.remove(fake_snap)
            self.verifier.evidence_manifest.pop('evidence_partial_mock', None)

    def test_reject_absence_system_not_covered(self):
        """Verifier must reject citing a snapshot that does not cover the claimed system."""
        from code.evidence_store.models import CoveringSnapshot
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        fake_snap = CoveringSnapshot(
            evidence_id='evidence_uncovered_mock',
            file_path='mock/path.json',
            captured_at='2026-09-15T00:00:00Z',
            covered_systems=['account_service'],
            is_full_store_snapshot=True,
            replica_coverage=['primary'],
            total_records=10,
            records_by_system={'account_service': 10}
        )
        self.store.covering_snapshots.append(fake_snap)
        self.verifier.evidence_manifest['evidence_uncovered_mock'] = {
            'file_id': 'evidence_uncovered_mock',
            'captured_at': '2026-09-15T00:00:00Z'
        }
        try:
            output = {
                'case_id': 'case_999',
                'deletion_status': 'COMPLETE',
                'steps_json': json.dumps([{
                    'step_no': 1,
                    'phase': 'VERIFICATION',
                    'system_id': 'search_service',
                    'resource_id': 'search_001',
                    'operation': 'VERIFY_RECORD_ABSENT',
                    'state': 'ABSENT',
                    'evidence_id': 'evidence_uncovered_mock'
                }])
            }
            findings = self.verifier.verify_case(req, output)
            self.assertTrue(any(f.check_name == "absence_system_not_covered" for f in findings))
        finally:
            self.store.covering_snapshots.remove(fake_snap)
            self.verifier.evidence_manifest.pop('evidence_uncovered_mock', None)

    def test_reject_disallowed_phase_operation(self):
        """Verifier must reject DELETE_RECORD in FINDING phase."""
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        output = {
            'case_id': 'case_999',
            'deletion_status': 'INCOMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'FINDING',
                'system_id': 'account_service',
                'resource_id': 'account_001',
                'operation': 'DELETE_RECORD',
                'state': 'ABSENT',
                'evidence_id': ''
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "valid_operation" for f in findings))

    def test_reject_operation_state_mismatch(self):
        """Verifier must reject operation with incorrect state (e.g. DELETE_RECORD with PRESENT)."""
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        output = {
            'case_id': 'case_999',
            'deletion_status': 'INCOMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'REMEDIATION',
                'system_id': 'account_service',
                'resource_id': 'account_001',
                'operation': 'DELETE_RECORD',
                'state': 'PRESENT',
                'evidence_id': ''
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "operation_state_match" for f in findings))

    def test_reject_remediation_with_evidence(self):
        """Verifier must reject remediation steps citing evidence."""
        req = {
            'case_id': 'case_999',
            'audit_at': '2026-09-30T00:00:00Z',
            'email': 'test@example.com'
        }
        output = {
            'case_id': 'case_999',
            'deletion_status': 'INCOMPLETE',
            'steps_json': json.dumps([{
                'step_no': 1,
                'phase': 'REMEDIATION',
                'system_id': 'account_service',
                'resource_id': 'account_001',
                'operation': 'DELETE_RECORD',
                'state': 'ABSENT',
                'evidence_id': 'evidence_01'
            }])
        }
        findings = self.verifier.verify_case(req, output)
        self.assertTrue(any(f.check_name == "remediation_no_evidence" for f in findings))


if __name__ == '__main__':
    unittest.main()

