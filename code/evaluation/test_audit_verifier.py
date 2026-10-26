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
        self.assertTrue(any(f.check_name == "evidence_semantic_support" for f in findings))

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


if __name__ == '__main__':
    unittest.main()

