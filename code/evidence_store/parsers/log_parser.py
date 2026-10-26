import json
import os
import re
from typing import List, Tuple, Dict, Any, Optional

from code.evidence_store.models import Observation, CoveringSnapshot, ParseReport
from code.evidence_store.parsers.base import (
    BaseEvidenceParser,
    normalize_system_id,
    PREFIX_TO_SYSTEM,
    RESOURCE_ID_REGEX,
    USER_ID_REGEX,
    EMAIL_REGEX
)

TIMESTAMP_REGEX = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)')

class LogEvidenceParser(BaseEvidenceParser):
    """
    Parses structured and unstructured logs across all 6 repository log families.
    Extracts timestamps, systems, resources, user identifiers, and operational states.
    """

    def can_parse(self, file_path: str) -> bool:
        return file_path.lower().endswith('.log')

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
                file_type="log",
                status="FAILED",
                observation_count=0,
                error_message=f"File not found: {abs_path}"
            )
            return observations, None, report

        try:
            with open(abs_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception as e:
            report = ParseReport(
                evidence_id=eid,
                file_path=rel_path,
                file_type="log",
                status="FAILED",
                observation_count=0,
                error_message=f"Could not read log file: {e}"
            )
            return observations, None, report

        for line_no, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue

            # Extract line timestamp
            ts_match = TIMESTAMP_REGEX.match(line)
            obs_time = ts_match.group(1) if ts_match else manifest_time

            # Check if JSON line
            obs = None
            if '{' in line and '}' in line:
                json_part = line[line.find('{'):line.rfind('}') + 1]
                try:
                    data = json.loads(json_part)
                    obs = self._parse_json_log_line(data, line, line_no, eid, rel_path, obs_time)
                except Exception:
                    pass

            if not obs:
                obs = self._parse_text_log_line(line, line_no, eid, rel_path, obs_time)

            if obs:
                observations.append(obs)

        report = ParseReport(
            evidence_id=eid,
            file_path=rel_path,
            file_type="log",
            status="SUCCESS",
            observation_count=len(observations)
        )
        # Log files are streaming activity/trace logs, not full store snapshots
        return observations, None, report

    def _parse_json_log_line(
        self,
        data: Dict[str, Any],
        full_line: str,
        line_no: int,
        eid: str,
        rel_path: str,
        obs_time: str
    ) -> Optional[Observation]:
        data_str = json.dumps(data)
        res_matches = RESOURCE_ID_REGEX.findall(data_str)
        user_matches = USER_ID_REGEX.findall(data_str)
        email_matches = EMAIL_REGEX.findall(data_str)

        if not res_matches and not user_matches and not email_matches:
            return None

        res_id = res_matches[0] if res_matches else None
        user_id = user_matches[0] if user_matches else None
        emails = [e.lower() for e in email_matches]

        service_raw = data.get('service') or data.get('logger') or data.get('system')
        sys_id = normalize_system_id(service_raw)
        if not sys_id and res_id:
            pfx = res_id.split('_')[0]
            sys_id = PREFIX_TO_SYSTEM.get(pfx, 'account_service')
        if not sys_id:
            sys_id = 'account_service'

        state = self._determine_state_from_text(sys_id, data_str)

        return Observation(
            evidence_id=eid,
            file_path=rel_path,
            locator=f"line:{line_no}, res:{res_id}",
            observed_at=obs_time,
            system_id=sys_id,
            resource_id=res_id,
            user_id=user_id,
            identifiers=emails,
            state=state,
            raw_excerpt=full_line[:200],
            raw_record=data,
            parser_confidence=1.0,
            validation_status="VALID",
            raw_system=service_raw or sys_id
        )

    def _parse_text_log_line(
        self,
        line: str,
        line_no: int,
        eid: str,
        rel_path: str,
        obs_time: str
    ) -> Optional[Observation]:
        res_matches = RESOURCE_ID_REGEX.findall(line)
        user_matches = USER_ID_REGEX.findall(line)
        email_matches = EMAIL_REGEX.findall(line)

        if not res_matches and not user_matches and not email_matches:
            return None

        res_id = res_matches[0] if res_matches else None
        user_id = user_matches[0] if user_matches else None
        emails = [e.lower() for e in email_matches]

        # Extract service name
        sys_id = None
        svc_match = re.search(r'\b(?:service|system|function)=([a-zA-Z0-9_]+)\b', line)
        if svc_match:
            sys_id = normalize_system_id(svc_match.group(1))

        if not sys_id and res_id:
            pfx = res_id.split('_')[0]
            sys_id = PREFIX_TO_SYSTEM.get(pfx)

        if not sys_id:
            for s in ['account_service', 'search_service', 'support_platform', 'analytics_platform',
                      'security_service', 'backup_service', 'integration_service', 'identity_service', 'export_service']:
                if s in line:
                    sys_id = s
                    break

        if not sys_id:
            sys_id = 'account_service'

        state = self._determine_state_from_text(sys_id, line)

        return Observation(
            evidence_id=eid,
            file_path=rel_path,
            locator=f"line:{line_no}, res:{res_id}",
            observed_at=obs_time,
            system_id=sys_id,
            resource_id=res_id,
            user_id=user_id,
            identifiers=emails,
            state=state,
            raw_excerpt=line[:200],
            parser_confidence=1.0,
            validation_status="VALID",
            raw_system=sys_id
        )

    def _determine_state_from_text(self, sys_id: str, text: str) -> str:
        t = text.lower()
        if sys_id in ['account_service', 'search_service', 'export_service']:
            if 'deleted' in t or 'not_found' in t or '404' in t or 'absent' in t:
                return 'ABSENT'
            return 'PRESENT'
        elif sys_id == 'identity_service':
            if 'redacted' in t or 'null' in t:
                return 'REDACTED'
            return 'PRESENT'
        elif sys_id == 'support_platform':
            if 'redacted' in t:
                return 'REDACTED'
            if 'attachment' in t and ('deleted' in t or 'absent' in t or 'purged' in t):
                return 'ABSENT'
            return 'PRESENT'
        elif sys_id == 'analytics_platform':
            if 'anonymized' in t:
                return 'ANONYMIZED'
            return 'PRESENT'
        elif sys_id == 'security_service':
            return 'PRESENT'
        elif sys_id == 'backup_service':
            if 'blocked' in t:
                return 'BLOCKED'
            return 'ACTIVE'
        elif sys_id == 'integration_service':
            if 'disabled' in t or 'inactive' in t:
                return 'DISABLED'
            return 'ACTIVE'

        return 'PRESENT'
