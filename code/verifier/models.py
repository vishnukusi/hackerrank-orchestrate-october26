"""
Models for the independent audit verifier.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class VerificationFinding:
    case_id: str
    check_name: str
    severity: str  # 'ERROR' or 'WARNING'
    message: str
    step_no: Optional[int] = None


@dataclass
class VerificationResult:
    is_valid: bool
    total_cases: int
    passed_cases: int
    failed_cases: int
    findings: List[VerificationFinding] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'ERROR')

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == 'WARNING')
