import os
from typing import List, Tuple, Dict, Any, Optional

from code.evidence_store.models import Observation, CoveringSnapshot, ParseReport
from code.evidence_store.parsers.base import (
    BaseEvidenceParser,
    normalize_system_id,
    PREFIX_TO_SYSTEM
)

class TextHandoverParser(BaseEvidenceParser):
    """
    Parses shift handover text files containing pipe-delimited operational records:
    - timestamp | system_id | resource_id | category | state | identifier
    """

    def can_parse(self, file_path: str) -> bool:
        return file_path.lower().endswith('.txt')

    def parse(self, abs_path: str, manifest_entry: Dict[str, Any]) -> Tuple[List[Observation], Optional[CoveringSnapshot], ParseReport]:
        eid = manifest_entry['file_id']
        rel_path = manifest_entry['file_path']
        manifest_time = manifest_entry.get('captured_at', '')

        observations: List[Observation] = []
        unparsed_records: List[Dict[str, Any]] = []

        if not os.path.exists(abs_path):
            report = ParseReport(
                evidence_id=eid,
                file_path=rel_path,
                file_type="txt",
                status="FAILED",
                observation_count=0,
                error_message=f"File not found: {abs_path}"
            )
            return observations, None, report

        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            report = ParseReport(
                evidence_id=eid,
                file_path=rel_path,
                file_type="txt",
                status="FAILED",
                observation_count=0,
                error_message=f"Could not read txt file: {e}"
            )
            return observations, None, report

        for line_no, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or not line.startswith('-'):
                continue

            parts = [p.strip() for p in line.lstrip('-').split('|')]
            if len(parts) >= 6:
                ts, sys_raw, res_id, category, state_val, ident = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
                sys_id = normalize_system_id(sys_raw) or sys_raw

                is_attach = (category == 'SUPPORT_ATTACHMENT')
                state = self._map_state(sys_id, state_val, is_attach)

                obs = Observation(
                    evidence_id=eid,
                    file_path=rel_path,
                    locator=f"line:{line_no}, res:{res_id}",
                    observed_at=ts or manifest_time,
                    system_id=sys_id,
                    resource_id=res_id,
                    user_id=None,
                    identifiers=[ident.lower()] if ident else [],
                    state=state,
                    raw_excerpt=line[:200],
                    parser_confidence=1.0,
                    validation_status="VALID",
                    metadata={'category': category, 'raw_state': state_val},
                    raw_system=sys_raw
                )
                observations.append(obs)
            else:
                unparsed_records.append({'line_no': line_no, 'content': line})

        report = ParseReport(
            evidence_id=eid,
            file_path=rel_path,
            file_type="txt",
            status="SUCCESS" if not unparsed_records else "PARTIAL",
            observation_count=len(observations),
            unparsed_records=unparsed_records
        )
        return observations, None, report

    def _map_state(self, sys_id: str, state_val: str, is_attach: bool) -> str:
        s = state_val.upper()
        if sys_id in ['account_service', 'search_service', 'export_service']:
            if s in ['NOT_FOUND', 'ABSENT', 'DELETED']:
                return 'ABSENT'
            return 'PRESENT'
        elif sys_id == 'identity_service':
            if s == 'REDACTED':
                return 'REDACTED'
            return 'PRESENT'
        elif sys_id == 'support_platform':
            if s in ['NOT_FOUND', 'ABSENT', 'DELETED']:
                return 'ABSENT'
            if s == 'REDACTED':
                return 'ABSENT' if is_attach else 'REDACTED'
            return 'PRESENT'
        elif sys_id == 'analytics_platform':
            if s == 'ANONYMIZED':
                return 'ANONYMIZED'
            return 'PRESENT'
        elif sys_id == 'backup_service':
            if s in ['BLOCKED', 'DISABLED']:
                return 'BLOCKED'
            return 'ACTIVE'
        elif sys_id == 'integration_service':
            if s in ['DISABLED', 'INACTIVE']:
                return 'DISABLED'
            return 'ACTIVE'
        elif sys_id == 'security_service':
            return 'PRESENT'

        return 'PRESENT'
