import re
from typing import List, Tuple, Dict, Any, Optional
from code.evidence_store.models import Observation, CoveringSnapshot, ParseReport

CANONICAL_SYSTEMS = {
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

PREFIX_TO_SYSTEM = {
    'account': 'account_service',
    'search': 'search_service',
    'support': 'support_platform',
    'export': 'export_service',
    'analytics': 'analytics_platform',
    'audit_record': 'security_service',
    'audit': 'security_service',
    'backup': 'backup_service',
    'data_flow': 'integration_service',
    'data': 'integration_service',
    'tombstone': 'identity_service',
    'marketing': 'account_service',
}

RESOURCE_ID_REGEX = re.compile(
    r'\b(account_\d+|search_\d+|support_\d+|export_\d+|analytics_\d+|audit_record_\d+|backup_\d+|data_flow_\d+|tombstone_\d+|marketing_\d+)\b',
    re.IGNORECASE
)
USER_ID_REGEX = re.compile(r'\b(user_\d+)\b', re.IGNORECASE)
EMAIL_REGEX = re.compile(r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b')
SHA256_REGEX = re.compile(r'\b[a-f0-9]{64}\b', re.IGNORECASE)

def normalize_system_id(name: str) -> Optional[str]:
    """Map arbitrary system/service labels to canonical challenge systems."""
    if not name:
        return None
    name_clean = name.strip().lower()
    if name_clean in CANONICAL_SYSTEMS:
        return name_clean

    mapping = {
        'customer_profiles': 'account_service',
        'customer_accounts': 'account_service',
        'account': 'account_service',
        'profile_service': 'account_service',
        'customer_search': 'search_service',
        'search': 'search_service',
        'search_index_service': 'search_service',
        'support': 'support_platform',
        'helpdesk': 'support_platform',
        'support_storage_service': 'support_platform',
        'exports': 'export_service',
        'export': 'export_service',
        'customer_exports': 'export_service',
        'customer_export_service': 'export_service',
        'user_export_store': 'export_service',
        'analytics': 'analytics_platform',
        'customer_analytics': 'analytics_platform',
        'analytics_identity_store': 'analytics_platform',
        'security': 'security_service',
        'security_audit': 'security_service',
        'security_audit_service': 'security_service',
        'legal_service': 'security_service',
        'backup': 'backup_service',
        'backups': 'backup_service',
        'backup_restore_service': 'backup_service',
        'connectors': 'integration_service',
        'connector': 'integration_service',
        'integration': 'integration_service',
        'integration_management_service': 'integration_service',
        'deleted_customer_references': 'identity_service',
        'identity': 'identity_service',
        'tombstones': 'identity_service',
        'privacy_treatment_service': 'identity_service'
    }
    return mapping.get(name_clean)

class BaseEvidenceParser:
    """Base class for evidence file parsers."""

    def can_parse(self, file_path: str) -> bool:
        raise NotImplementedError

    def parse(self, abs_path: str, manifest_entry: Dict[str, Any]) -> Tuple[List[Observation], Optional[CoveringSnapshot], ParseReport]:
        """
        Parses an evidence file.
        Returns:
            - List of Observation instances
            - Optional CoveringSnapshot metadata
            - ParseReport detailing execution and unparsed items
        """
        raise NotImplementedError
