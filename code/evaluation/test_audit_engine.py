"""
Unit tests for Audit Engine (Phase 4).
"""

import csv
import json
import os
import unittest

from code.audit_engine import AuditEngine, StepBuilder, is_non_deletion_request
from code.evidence_store.store import EvidenceStore
from code.identity.resolver import IdentityResolver
from code.policy.engine import PolicyEngine

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")


class TestAuditEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = EvidenceStore(DATASET_DIR)
        cls.store.load_all_evidence()
        cls.resolver = IdentityResolver(DATASET_DIR)
        cls.policy = PolicyEngine(os.path.join(DATASET_DIR, "deletion_rules.csv"))

        with open(os.path.join(DATASET_DIR, "system_activity.csv"), "r", encoding="utf-8") as f:
            cls.activities = list(csv.DictReader(f))

        cls.engine = AuditEngine(
            evidence_store=cls.store,
            identity_resolver=cls.resolver,
            policy_engine=cls.policy,
            system_activities=cls.activities
        )

    def test_intent_detection(self):
        # Explicit deletion
        self.assertFalse(is_non_deletion_request("Please delete my personal data completely."))
        self.assertFalse(is_non_deletion_request("Je demande la suppression définitive de mon compte."))
        
        # Explicit non-deletion requests
        self.assertTrue(is_non_deletion_request("The mobile number on my profile is out of date. Please replace it with the new one and leave the account active."))
        self.assertTrue(is_non_deletion_request("I'd like to download my account information in a portable format. Please keep the account as it is."))
        self.assertTrue(is_non_deletion_request("I can't access my profile and need help resetting the login. Do not close the account."))
        self.assertTrue(is_non_deletion_request("Please stop the promotional emails. I still need the account and my order history, so I am not asking for deletion."))

    def test_step_builder_strict_ordering(self):
        builder = StepBuilder()
        builder.add_step("VERIFICATION", "search_service", "search_01", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_01")
        builder.add_step("REMEDIATION", "search_service", "search_01", "DELETE_RECORD", "ABSENT", "")
        builder.add_step("REMEDIATION", "integration_service", "data_01", "DISABLE_DATA_FLOW", "DISABLED", "")
        builder.add_step("FINDING", "integration_service", "data_01", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_02")
        builder.add_step("FINDING", "search_service", "search_01", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_02")

        steps = builder.build()
        self.assertEqual(len(steps), 5)
        # Check 1-indexed sequential step_no
        self.assertEqual([s["step_no"] for s in steps], [1, 2, 3, 4, 5])

        # Check phase sequence
        self.assertEqual([s["phase"] for s in steps], ["FINDING", "FINDING", "REMEDIATION", "REMEDIATION", "VERIFICATION"])

        # Check remediation priority: DISABLE_DATA_FLOW before DELETE_RECORD
        rem_ops = [s["operation"] for s in steps if s["phase"] == "REMEDIATION"]
        self.assertEqual(rem_ops, ["DISABLE_DATA_FLOW", "DELETE_RECORD"])

    def test_case_01_complete_execution(self):
        req = {
            'case_id': 'case_01',
            'email': 'amina.elmasri@gmail.com',
            'requested_at': '2026-08-18T09:14:00Z',
            'reported_status': 'COMPLETE',
            'reported_at': '2026-08-20T16:42:00Z',
            'audit_at': '2026-09-03T11:30:00Z',
            'request_text': 'Hi, I haven’t used this account in years and would like it closed. Please remove the profile and the personal details attached to it.'
        }
        res = self.engine.process_request(req)
        self.assertEqual(res['case_id'], 'case_01')
        self.assertEqual(res['deletion_status'], 'COMPLETE')
        steps = json.loads(res['steps_json'])
        self.assertTrue(len(steps) >= 1)
        self.assertEqual(steps[0]['operation'], 'VERIFY_RECORD_ABSENT')
        self.assertEqual(steps[0]['state'], 'ABSENT')
        self.assertTrue(steps[0]['evidence_id'].startswith('evidence_'))

    def test_non_deletion_case_not_applicable(self):
        req = {
            'case_id': 'case_535',
            'email': 'user535@example.com',
            'requested_at': '2026-09-29T12:00:00Z',
            'reported_status': 'NOT_APPLICABLE',
            'reported_at': '2026-09-29T12:00:00Z',
            'audit_at': '2026-10-01T12:00:00Z',
            'request_text': "I'd like to download my account information in a portable format. Please keep the account as it is."
        }
        res = self.engine.process_request(req)
        self.assertEqual(res['case_id'], 'case_535')
        self.assertEqual(res['deletion_status'], 'NOT_APPLICABLE')
        self.assertEqual(res['steps_json'], '[]')


if __name__ == '__main__':
    unittest.main()
