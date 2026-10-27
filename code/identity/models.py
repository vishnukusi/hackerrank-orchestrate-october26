from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class LinkedResource:
    """
    Represents a specific system resource proven to belong to or be linked to a user.
    Preserves provenance and discovery confidence.
    """
    resource_id: str
    system_id: str
    link_type: str            # 'USER_ID_EXACT', 'EMAIL_EXACT', 'SYSTEM_ACTIVITY_LINK', 'EVIDENCE_RECORD'
    provenance: str           # evidence_id, activity_id, or audit_id
    confidence: float = 1.0
    discovered_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ResolvedIdentity:
    """
    Time-reconstructed identity profile for a deletion subject as of audit_at.
    Maintains active identifiers, historical alias validity, and linked resources.
    """
    user_id: str
    name: str
    current_email: str
    email_at_cutoff: str
    active_emails: List[str]  # All emails valid for the user at or before audit_at
    phone: str
    email_hash: str
    audit_at: str
    active_hashes: List[str] = field(default_factory=list)
    email_changes: List[Dict[str, Any]] = field(default_factory=list)
    linked_resources: Dict[str, LinkedResource] = field(default_factory=dict)
