from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

@dataclass
class Observation:
    """
    A single factual observation extracted from an evidence file.
    Grounds agent reasoning in verifiable raw source data.
    """
    evidence_id: str                      # Source file_id from evidence manifest (e.g. 'evidence_54')
    file_path: str                        # Relative file path (e.g. 'evidence/databases/...')
    locator: str                          # Precise location (e.g. 'table:customer_profiles_failover_replica, row:1')
    observed_at: str                      # ISO-8601 timestamp at which this state was valid
    system_id: str                        # Target system (e.g. 'account_service', 'search_service', etc.)
    resource_id: Optional[str]            # Extracted resource identifier (e.g. 'account_005')
    user_id: Optional[str]                # User ID (e.g. 'user_05'), if explicitly present
    identifiers: List[str] = field(default_factory=list) # Extracted emails, phone numbers, hashes
    state: str = "PRESENT"                # PRESENT, ABSENT, REDACTED, ANONYMIZED, ACTIVE, DISABLED, BLOCKED
    raw_excerpt: str = ""                 # Exact raw snippet, SQL row, or log line
    raw_record: Optional[Dict[str, Any]] = None # Structured dict if parsed from JSON/SQL
    is_replica: bool = False              # True if from a read, regional, or failover replica
    replica_type: Optional[str] = None    # 'primary', 'read_replica', 'regional_replica', 'failover_replica'
    parser_confidence: float = 1.0        # Extraction confidence [0.0 - 1.0]
    validation_status: str = "VALID"      # 'VALID', 'UNCERTAIN', 'MALFORMED'
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_system: Optional[str] = None      # Raw system/data_store string from original source

@dataclass
class CoveringSnapshot:
    """
    Metadata about an evidence file that serves as a full or partial snapshot.
    Used for absence-by-omission reasoning.
    """
    evidence_id: str
    file_path: str
    captured_at: str
    covered_systems: List[str]            # Systems guaranteed to be covered
    is_full_store_snapshot: bool          # True if covers entire data store, not a partial export
    replica_coverage: List[str]           # Replicas included (e.g. ['primary', 'failover_replica'])
    total_records: int = 0
    records_by_system: Dict[str, int] = field(default_factory=dict)

@dataclass
class ParseReport:
    """
    Report detailing the results of parsing an evidence file or full dataset.
    """
    evidence_id: str
    file_path: str
    file_type: str
    status: str                           # 'SUCCESS', 'PARTIAL', 'FAILED'
    observation_count: int = 0
    error_message: Optional[str] = None
    unparsed_records: List[Dict[str, Any]] = field(default_factory=list)
