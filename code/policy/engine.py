"""
Policy engine driven by deletion_rules.csv.
Evaluates data categories, applicable actions, target states, and operations.
"""

import csv
import os
from typing import Dict, Optional, Tuple

from code.policy.models import (
    PolicyAction,
    PolicyOperation,
    PolicyPhase,
    PolicyRule,
    PolicyState,
)


class PolicyEngine:
    def __init__(self, rules_csv_path: str):
        self.rules_csv_path = rules_csv_path
        self.rules: Dict[str, PolicyRule] = {}
        self.category_rules: Dict[str, PolicyRule] = {}
        self._load_rules()

    def _load_rules(self) -> None:
        if not os.path.exists(self.rules_csv_path):
            raise FileNotFoundError(f"Policy rules file not found: {self.rules_csv_path}")

        action_target_map = {
            PolicyAction.DELETE: PolicyState.ABSENT,
            PolicyAction.REDACT: PolicyState.REDACTED,
            PolicyAction.ANONYMIZE: PolicyState.ANONYMIZED,
            PolicyAction.RETAIN: PolicyState.PRESENT,
        }

        with open(self.rules_csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rule_id = row["rule_id"]
                category = row["data_category"]
                condition = row["condition"]
                action_str = row["required_action"].strip().upper()
                action = PolicyAction(action_str)

                # Special case: BACKUP_ARCHIVE retain condition is restore blocked
                if category == "BACKUP_ARCHIVE":
                    target = PolicyState.BLOCKED
                else:
                    target = action_target_map.get(action, PolicyState.ABSENT)

                rule = PolicyRule(
                    rule_id=rule_id,
                    data_category=category,
                    condition=condition,
                    required_action=action,
                    target_state=target,
                )
                self.rules[rule_id] = rule
                self.category_rules[category] = rule

    def get_rule_by_category(self, category: str) -> Optional[PolicyRule]:
        return self.category_rules.get(category)

    def resolve_resource_category(
        self,
        system_id: str,
        resource_id: str,
        resource_type: Optional[str] = None,
        is_pii_exposed: bool = False,
    ) -> str:
        """
        Resolves the challenge data_category for a given system and resource.
        """
        if resource_type:
            rt_upper = resource_type.upper()
            if rt_upper in self.category_rules:
                return rt_upper

        if system_id == "account_service":
            return "USER_PROFILE"
        elif system_id == "search_service":
            return "SEARCH_INDEX_ENTRY"
        elif system_id == "export_service":
            return "USER_EXPORT"
        elif system_id == "security_service":
            return "SECURITY_AUDIT_LOG"
        elif system_id == "identity_service":
            if "tombstone" in resource_id:
                return "ACCOUNT_TOMBSTONE"
            return "SECONDARY_IDENTIFIER" if is_pii_exposed else "USER_PROFILE"
        elif system_id == "support_platform":
            if "attachment" in resource_id.lower() or "attachment" in str(resource_type).lower():
                return "SUPPORT_ATTACHMENT"
            # If PII exposed or conversation/message
            if is_pii_exposed or "message" in str(resource_type).lower():
                return "SUPPORT_MESSAGE"
            # Fallback based on resource id naming if applicable
            return "SUPPORT_MESSAGE"
        elif system_id == "analytics_platform":
            if is_pii_exposed:
                return "ANALYTICS_PROFILE"
            return "ANALYTICS_PROFILE"
        elif system_id == "integration_service":
            return "DATA_FLOW"
        elif system_id == "backup_service":
            return "BACKUP_ARCHIVE"

        return "USER_PROFILE"

    def get_target_state(
        self,
        system_id: str,
        resource_id: str,
        category: Optional[str] = None,
        is_pii_exposed: bool = False,
    ) -> PolicyState:
        """
        Determines the required compliant state under challenge deletion rules.
        """
        if system_id == "integration_service":
            return PolicyState.DISABLED
        if system_id == "backup_service":
            return PolicyState.BLOCKED
        if system_id == "security_service":
            return PolicyState.PRESENT

        cat = category or self.resolve_resource_category(system_id, resource_id, is_pii_exposed=is_pii_exposed)
        rule = self.get_rule_by_category(cat)
        if rule:
            return rule.target_state

        if system_id in ("identity_service", "support_platform") and is_pii_exposed:
            return PolicyState.REDACTED
        if system_id == "analytics_platform" and is_pii_exposed:
            return PolicyState.ANONYMIZED

        return PolicyState.ABSENT

    def get_finding_operation(
        self,
        system_id: str,
        resource_id: str,
        observed_state: str,
        category: Optional[str] = None,
    ) -> Tuple[PolicyOperation, PolicyState]:
        """
        Returns (operation, state) for a non-compliant finding.
        """
        if system_id == "integration_service":
            return (PolicyOperation.REPORT_ACTIVE_DATA_FLOW, PolicyState.ACTIVE)
        if system_id == "backup_service":
            return (PolicyOperation.REPORT_RESTORE_RISK, PolicyState.ACTIVE)

        cat = category or self.resolve_resource_category(system_id, resource_id)

        # Exposed PII findings: identity tombstone with PII, support message with PII, analytics profile with PII
        if cat in ("SUPPORT_MESSAGE", "ACCOUNT_TOMBSTONE", "ANALYTICS_PROFILE", "PRODUCT_TELEMETRY", "EMAIL_DELIVERY_LOG"):
            # Check if this specific resource was marked as a remaining unpurged record vs exposed PII
            # For support attachments or pure unpurged records: REPORT_REMAINING_RECORD
            if cat == "SUPPORT_ATTACHMENT":
                return (PolicyOperation.REPORT_REMAINING_RECORD, PolicyState.PRESENT)
            return (PolicyOperation.REPORT_EXPOSED_PII, PolicyState.PRESENT)

        return (PolicyOperation.REPORT_REMAINING_RECORD, PolicyState.PRESENT)

    def get_remediation_operation(
        self,
        system_id: str,
        resource_id: str,
        category: Optional[str] = None,
        target_state: Optional[PolicyState] = None,
    ) -> Tuple[PolicyOperation, PolicyState]:
        """
        Returns (operation, state) for proposing a remediation step.
        """
        if system_id == "integration_service":
            return (PolicyOperation.DISABLE_DATA_FLOW, PolicyState.DISABLED)
        if system_id == "backup_service":
            return (PolicyOperation.BLOCK_RESTORE, PolicyState.BLOCKED)

        target = target_state or self.get_target_state(system_id, resource_id, category=category)

        if target == PolicyState.ABSENT:
            return (PolicyOperation.DELETE_RECORD, PolicyState.ABSENT)
        elif target == PolicyState.REDACTED:
            return (PolicyOperation.REDACT_RECORD, PolicyState.REDACTED)
        elif target == PolicyState.ANONYMIZED:
            return (PolicyOperation.ANONYMIZE_RECORD, PolicyState.ANONYMIZED)

        return (PolicyOperation.DELETE_RECORD, PolicyState.ABSENT)

    def get_verification_operation(
        self,
        system_id: str,
        resource_id: str,
        target_state: PolicyState,
    ) -> Tuple[PolicyOperation, PolicyState]:
        """
        Returns (operation, state) for the verification phase.
        """
        if system_id == "integration_service" or target_state == PolicyState.DISABLED:
            return (PolicyOperation.VERIFY_DATA_FLOW_DISABLED, PolicyState.DISABLED)
        if system_id == "backup_service" or target_state == PolicyState.BLOCKED:
            return (PolicyOperation.VERIFY_RESTORE_BLOCKED, PolicyState.BLOCKED)

        if target_state == PolicyState.ABSENT:
            return (PolicyOperation.VERIFY_RECORD_ABSENT, PolicyState.ABSENT)
        elif target_state == PolicyState.PRESENT:
            return (PolicyOperation.VERIFY_RECORD_PRESENT, PolicyState.PRESENT)
        elif target_state == PolicyState.REDACTED:
            return (PolicyOperation.VERIFY_RECORD_REDACTED, PolicyState.REDACTED)
        elif target_state == PolicyState.ANONYMIZED:
            return (PolicyOperation.VERIFY_RECORD_ANONYMIZED, PolicyState.ANONYMIZED)

        return (PolicyOperation.VERIFY_RECORD_ABSENT, PolicyState.ABSENT)
