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

HASH_REGEX = re.compile(r'\b[a-fA-F0-9]{64}\b')
TRACE_ID_REGEX = re.compile(r'\b(?:trace_id|requestId|tr_id|trace)=["\']?([a-zA-Z0-9_\-]+)["\']?|\b(tr-[a-zA-Z0-9_\-]+|rb-\d+-\d+)\b')
KEY_VAL_REGEX = re.compile(r'\b(?:lookup_key|correlation_key|subject_ref|subject_reference|customer_reference|customer_email|requester_email|email|owner_email)=["\']?([^"\'\s,]+)["\']?')

class LogEvidenceParser(BaseEvidenceParser):
    """
    Parses structured and unstructured logs across all 6 repository log families.
    Extracts timestamps, systems, resources, user identifiers, and operational states.
    Supports multi-line trace correlation and replica conflict detection.
    """

    def can_parse(self, file_path: str) -> bool:
        return file_path.lower().endswith('.log')

    def parse(self, abs_path: str, manifest_entry: Dict[str, Any]) -> Tuple[List[Observation], Optional[CoveringSnapshot], ParseReport]:
        eid = manifest_entry['file_id']
        rel_path = manifest_entry['file_path']
        manifest_time = manifest_entry.get('captured_at', '')

        observations: List[Observation] = []

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

        traces: Dict[str, List[Dict[str, Any]]] = {}

        for line_no, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue

            ts_match = TIMESTAMP_REGEX.match(line)
            obs_time = ts_match.group(1) if ts_match else manifest_time

            tr_match = TRACE_ID_REGEX.search(line)
            trace_id = (tr_match.group(1) or tr_match.group(2)) if tr_match else None

            res_matches = RESOURCE_ID_REGEX.findall(line)
            user_matches = USER_ID_REGEX.findall(line)
            email_matches = EMAIL_REGEX.findall(line)
            hash_matches = HASH_REGEX.findall(line)
            kv_matches = KEY_VAL_REGEX.findall(line)

            idents = [e.lower() for e in email_matches] + [h.lower() for h in hash_matches]
            for kv in kv_matches:
                kv_clean = kv.strip().lower()
                if '@' in kv_clean or kv_clean.startswith('user_') or len(kv_clean) == 64:
                    if kv_clean not in idents:
                        idents.append(kv_clean)

            # Determine service name
            sys_id = None
            svc_match = re.search(r'\b(?:service|system|logger|function)=["\']?([a-zA-Z0-9_]+)["\']?\b', line)
            if svc_match:
                sys_id = normalize_system_id(svc_match.group(1))

            if not sys_id and res_matches:
                pfx = res_matches[0].split('_')[0]
                sys_id = PREFIX_TO_SYSTEM.get(pfx)

            if not sys_id:
                for s in ['account_service', 'search_service', 'support_platform', 'analytics_platform',
                          'security_service', 'backup_service', 'integration_service', 'identity_service', 'export_service']:
                    if s in line:
                        sys_id = s
                        break

            if not sys_id:
                sys_id = 'account_service'

            entry = {
                'line_no': line_no,
                'time': obs_time,
                'line': line,
                'res': res_matches,
                'users': user_matches,
                'idents': idents,
                'sys_id': sys_id
            }

            if trace_id:
                traces.setdefault(trace_id, []).append(entry)
            else:
                if res_matches or user_matches or idents:
                    res_id = res_matches[0] if res_matches else None
                    u_id = user_matches[0] if user_matches else None
                    state = self._determine_state_from_text(sys_id, line)
                    observations.append(Observation(
                        evidence_id=eid,
                        file_path=rel_path,
                        locator=f"line:{line_no}, res:{res_id}",
                        observed_at=obs_time,
                        system_id=sys_id,
                        resource_id=res_id,
                        user_id=u_id,
                        identifiers=idents,
                        state=state,
                        raw_excerpt=line[:200],
                        parser_confidence=1.0,
                        validation_status="VALID",
                        raw_system=sys_id
                    ))

        # Process grouped traces
        for trace_id, tlines in traces.items():
            all_res = list(dict.fromkeys(r for l in tlines for r in l['res']))
            all_users = list(dict.fromkeys(u for l in tlines for u in l['users']))
            all_idents = list(dict.fromkeys(i for l in tlines for i in l['idents']))
            combined_text = " ".join(l['line'] for l in tlines)
            last_time = max(l['time'] for l in tlines)
            first_line_no = tlines[0]['line_no']
            sys_id = tlines[0]['sys_id']
            for l in tlines:
                if l['sys_id'] and l['sys_id'] != 'account_service':
                    sys_id = l['sys_id']
                    break

            state = self._determine_state_from_text(sys_id, combined_text)

            if all_res:
                for r in all_res:
                    pfx = r.split('_')[0]
                    r_sys = PREFIX_TO_SYSTEM.get(pfx, sys_id)
                    r_state = self._determine_state_from_text(r_sys, combined_text)
                    observations.append(Observation(
                        evidence_id=eid,
                        file_path=rel_path,
                        locator=f"line:{first_line_no}, trace:{trace_id}, res:{r}",
                        observed_at=last_time,
                        system_id=r_sys,
                        resource_id=r,
                        user_id=all_users[0] if all_users else None,
                        identifiers=all_idents,
                        state=r_state,
                        raw_excerpt=combined_text[:200],
                        parser_confidence=1.0,
                        validation_status="VALID",
                        raw_system=r_sys
                    ))
            elif all_users or all_idents:
                observations.append(Observation(
                    evidence_id=eid,
                    file_path=rel_path,
                    locator=f"line:{first_line_no}, trace:{trace_id}",
                    observed_at=last_time,
                    system_id=sys_id,
                    resource_id=None,
                    user_id=all_users[0] if all_users else None,
                    identifiers=all_idents,
                    state=state,
                    raw_excerpt=combined_text[:200],
                    parser_confidence=1.0,
                    validation_status="VALID",
                    raw_system=sys_id
                ))

        report = ParseReport(
            evidence_id=eid,
            file_path=rel_path,
            file_type="log",
            status="SUCCESS",
            observation_count=len(observations)
        )
        return observations, None, report

    def _determine_state_from_text(self, sys_id: str, text: str) -> str:
        t = text.lower()
        if 'marker_found=false' in t:
            return 'PRESENT'
        if sys_id in ['account_service', 'search_service', 'export_service']:
            if 'deleted' in t or 'not_found' in t or '404' in t or 'absent' in t or 'rows=0' in t:
                return 'ABSENT'
            return 'PRESENT'
        elif sys_id == 'identity_service':
            if 'redacted' in t or 'null' in t:
                return 'REDACTED'
            return 'PRESENT'
        elif sys_id == 'support_platform':
            if 'attachment' in t and ('deleted' in t or 'absent' in t or 'purged' in t):
                return 'ABSENT'
            email_match = re.search(r'requester_email=([^ ]+)', t)
            phone_match = re.search(r'requester_phone=([^ ]+)', t)
            has_email = email_match and email_match.group(1).lower() not in ('null', 'none', '')
            has_phone = phone_match and phone_match.group(1).lower() not in ('null', 'none', '')
            if has_email or has_phone:
                return 'PRESENT'
            if ('requester_email=null' in t and 'requester_phone=null' in t) or 'redacted' in t:
                return 'REDACTED'
            return 'PRESENT'
        elif sys_id == 'analytics_platform':
            if 'anonymized' in t or ('email=null' in t and 'phone=null' in t):
                return 'ANONYMIZED'
            return 'PRESENT'
        elif sys_id == 'security_service':
            return 'PRESENT'
        elif sys_id == 'backup_service':
            if 'restore_allowed=false' in t or 'blocked' in t:
                return 'BLOCKED'
            return 'ACTIVE'
        elif sys_id == 'integration_service':
            if 'enabled=false' in t or 'disabled' in t or 'inactive' in t:
                return 'DISABLED'
            return 'ACTIVE'

        return 'PRESENT'
