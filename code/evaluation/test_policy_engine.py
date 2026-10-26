"""
Unit tests for Policy and State Engine (Phase 3).
"""

import os
import unittest

from code.policy import (
    PolicyAction,
    PolicyEngine,
    PolicyOperation,
    PolicyState,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RULES_CSV = os.path.join(BASE_DIR, "dataset", "deletion_rules.csv")


class TestPolicyEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PolicyEngine(RULES_CSV)

    def test_load_all_15_rules(self):
        self.assertEqual(len(self.engine.rules), 15)
        # Check rule 01 (USER_PROFILE -> DELETE)
        r1 = self.engine.rules["rule_01"]
        self.assertEqual(r1.data_category, "USER_PROFILE")
        self.assertEqual(r1.required_action, PolicyAction.DELETE)
        self.assertEqual(r1.target_state, PolicyState.ABSENT)

        # Check rule 05 (SUPPORT_MESSAGE -> REDACT)
        r5 = self.engine.rules["rule_05"]
        self.assertEqual(r5.data_category, "SUPPORT_MESSAGE")
        self.assertEqual(r5.required_action, PolicyAction.REDACT)
        self.assertEqual(r5.target_state, PolicyState.REDACTED)

        # Check rule 07 (ANALYTICS_PROFILE -> ANONYMIZE)
        r7 = self.engine.rules["rule_07"]
        self.assertEqual(r7.data_category, "ANALYTICS_PROFILE")
        self.assertEqual(r7.required_action, PolicyAction.ANONYMIZE)
        self.assertEqual(r7.target_state, PolicyState.ANONYMIZED)

        # Check rule 12 (SECURITY_AUDIT_LOG -> RETAIN)
        r12 = self.engine.rules["rule_12"]
        self.assertEqual(r12.data_category, "SECURITY_AUDIT_LOG")
        self.assertEqual(r12.required_action, PolicyAction.RETAIN)
        self.assertEqual(r12.target_state, PolicyState.PRESENT)

        # Check rule 14 (BACKUP_ARCHIVE -> RETAIN with blocked restore)
        r14 = self.engine.rules["rule_14"]
        self.assertEqual(r14.data_category, "BACKUP_ARCHIVE")
        self.assertEqual(r14.required_action, PolicyAction.RETAIN)
        self.assertEqual(r14.target_state, PolicyState.BLOCKED)

        # Check rule 15 (ACCOUNT_TOMBSTONE -> REDACT)
        r15 = self.engine.rules["rule_15"]
        self.assertEqual(r15.data_category, "ACCOUNT_TOMBSTONE")
        self.assertEqual(r15.required_action, PolicyAction.REDACT)
        self.assertEqual(r15.target_state, PolicyState.REDACTED)

    def test_integration_and_backup_operational_policies(self):
        # integration_service -> DISABLE -> DISABLED
        op, state = self.engine.get_finding_operation("integration_service", "data_flow_101", "ACTIVE")
        self.assertEqual(op, PolicyOperation.REPORT_ACTIVE_DATA_FLOW)
        self.assertEqual(state, PolicyState.ACTIVE)

        rem_op, rem_state = self.engine.get_remediation_operation("integration_service", "data_flow_101")
        self.assertEqual(rem_op, PolicyOperation.DISABLE_DATA_FLOW)
        self.assertEqual(rem_state, PolicyState.DISABLED)

        ver_op, ver_state = self.engine.get_verification_operation("integration_service", "data_flow_101", PolicyState.DISABLED)
        self.assertEqual(ver_op, PolicyOperation.VERIFY_DATA_FLOW_DISABLED)
        self.assertEqual(ver_state, PolicyState.DISABLED)

        # backup_service -> BLOCK -> BLOCKED
        op, state = self.engine.get_finding_operation("backup_service", "backup_202", "ACTIVE")
        self.assertEqual(op, PolicyOperation.REPORT_RESTORE_RISK)
        self.assertEqual(state, PolicyState.ACTIVE)

        rem_op, rem_state = self.engine.get_remediation_operation("backup_service", "backup_202")
        self.assertEqual(rem_op, PolicyOperation.BLOCK_RESTORE)
        self.assertEqual(rem_state, PolicyState.BLOCKED)

        ver_op, ver_state = self.engine.get_verification_operation("backup_service", "backup_202", PolicyState.BLOCKED)
        self.assertEqual(ver_op, PolicyOperation.VERIFY_RESTORE_BLOCKED)
        self.assertEqual(ver_state, PolicyState.BLOCKED)

    def test_support_platform_message_vs_attachment(self):
        # Message (PII exposed) -> REDACT
        op, st = self.engine.get_finding_operation("support_platform", "support_864", "PRESENT", category="SUPPORT_MESSAGE")
        self.assertEqual(op, PolicyOperation.REPORT_EXPOSED_PII)
        self.assertEqual(st, PolicyState.PRESENT)

        rem_op, rem_st = self.engine.get_remediation_operation("support_platform", "support_864", category="SUPPORT_MESSAGE")
        self.assertEqual(rem_op, PolicyOperation.REDACT_RECORD)
        self.assertEqual(rem_st, PolicyState.REDACTED)

        # Attachment -> DELETE
        op, st = self.engine.get_finding_operation("support_platform", "support_001", "PRESENT", category="SUPPORT_ATTACHMENT")
        self.assertEqual(op, PolicyOperation.REPORT_REMAINING_RECORD)
        self.assertEqual(st, PolicyState.PRESENT)

        rem_op, rem_st = self.engine.get_remediation_operation("support_platform", "support_001", category="SUPPORT_ATTACHMENT")
        self.assertEqual(rem_op, PolicyOperation.DELETE_RECORD)
        self.assertEqual(rem_st, PolicyState.ABSENT)

    def test_security_audit_log_retention(self):
        target = self.engine.get_target_state("security_service", "audit_record_123")
        self.assertEqual(target, PolicyState.PRESENT)

        ver_op, ver_st = self.engine.get_verification_operation("security_service", "audit_record_123", target)
        self.assertEqual(ver_op, PolicyOperation.VERIFY_RECORD_PRESENT)
        self.assertEqual(ver_st, PolicyState.PRESENT)


if __name__ == "__main__":
    unittest.main()
