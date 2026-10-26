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

class JsonBundleParser(BaseEvidenceParser):
    """
    Parses JSON snapshot bundles, service exports, connector configs, and analytics reports.
    Preserves inner embedded snapshot timestamps and tracks snapshot coverage.
    """

    def can_parse(self, file_path: str) -> bool:
        return file_path.lower().endswith('.json')

    def parse(self, abs_path: str, manifest_entry: Dict[str, Any]) -> Tuple[List[Observation], Optional[CoveringSnapshot], ParseReport]:
        eid = manifest_entry['file_id']
        rel_path = manifest_entry['file_path']
        manifest_captured_at = manifest_entry.get('captured_at', '')

        observations: List[Observation] = []
        unparsed_records: List[Dict[str, Any]] = []

        if not os.path.exists(abs_path):
            report = ParseReport(
                evidence_id=eid,
                file_path=rel_path,
                file_type="json",
                status="FAILED",
                observation_count=0,
                error_message=f"File not found: {abs_path}"
            )
            return observations, None, report

        try:
            with open(abs_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            report = ParseReport(
                evidence_id=eid,
                file_path=rel_path,
                file_type="json",
                status="FAILED",
                observation_count=0,
                error_message=f"JSON decoding error: {e}"
            )
            return observations, None, report

        covering_snapshot = None
        records_by_system: Dict[str, int] = {}

        # Shape 1: customer_data_snapshot_bundle_*.json with 'snapshots' array
        if 'snapshots' in data:
            exported_at = data.get('exported_at') or manifest_captured_at
            covered_systems = []
            for s_idx, snap in enumerate(data['snapshots']):
                store_name = snap.get('data_store', '')
                sys_id = normalize_system_id(store_name) or store_name
                inner_captured_at = snap.get('captured_at') or exported_at
                collection = snap.get('collection', '')
                recs = snap.get('records', [])

                if sys_id:
                    covered_systems.append(sys_id)
                    records_by_system[sys_id] = records_by_system.get(sys_id, 0) + len(recs)

                for r_idx, rec in enumerate(recs):
                    obs = self._parse_json_record(
                        rec, sys_id, r_idx + 1, eid, rel_path, inner_captured_at,
                        locator=f"snapshots[{s_idx}].records[{r_idx}]",
                        raw_store_name=store_name
                    )
                    if obs:
                        observations.append(obs)
                    else:
                        unparsed_records.append({'locator': f"snapshots[{s_idx}].records[{r_idx}]", 'record': rec})

            covering_snapshot = CoveringSnapshot(
                evidence_id=eid,
                file_path=rel_path,
                captured_at=exported_at,
                covered_systems=list(set(covered_systems)),
                is_full_store_snapshot=True,
                replica_coverage=['primary'],
                total_records=len(observations),
                records_by_system=records_by_system
            )

        # Shape 2: service_resource_export_*.json with 'service_exports' dict
        elif 'service_exports' in data:
            exported_at = data.get('exported_at') or manifest_captured_at
            covered_systems = []
            for sys_key, content in data['service_exports'].items():
                sys_id = normalize_system_id(sys_key) or sys_key
                covered_systems.append(sys_id)
                rec_list = []
                if isinstance(content, list):
                    rec_list = content
                elif isinstance(content, dict):
                    for sub_k, sub_v in content.items():
                        if isinstance(sub_v, list):
                            rec_list.extend(sub_v)

                records_by_system[sys_id] = records_by_system.get(sys_id, 0) + len(rec_list)
                for r_idx, rec in enumerate(rec_list):
                    obs = self._parse_json_record(
                        rec, sys_id, r_idx + 1, eid, rel_path, exported_at,
                        locator=f"service_exports.{sys_key}[{r_idx}]",
                        raw_store_name=sys_key
                    )
                    if obs:
                        observations.append(obs)
                    else:
                        unparsed_records.append({'locator': f"service_exports.{sys_key}[{r_idx}]", 'record': rec})

            covering_snapshot = CoveringSnapshot(
                evidence_id=eid,
                file_path=rel_path,
                captured_at=exported_at,
                covered_systems=list(set(covered_systems)),
                is_full_store_snapshot=True,
                replica_coverage=['primary'],
                total_records=len(observations),
                records_by_system=records_by_system
            )

        # Shape 3: Single collection export ('records' top-level)
        elif 'records' in data:
            exported_at = data.get('exported_at') or data.get('captured_at') or manifest_captured_at
            origin = data.get('origin') or data.get('source') or data.get('collection', '')
            sys_id = normalize_system_id(origin)
            if not sys_id:
                if 'connector' in rel_path:
                    sys_id = 'integration_service'
                elif 'analytics' in rel_path:
                    sys_id = 'analytics_platform'
                elif 'export' in rel_path:
                    sys_id = 'export_service'
                elif 'security' in rel_path or 'audit' in rel_path:
                    sys_id = 'security_service'
                else:
                    sys_id = 'account_service'

            recs = data.get('records', [])
            records_by_system[sys_id] = len(recs)
            for r_idx, rec in enumerate(recs):
                obs = self._parse_json_record(
                    rec, sys_id, r_idx + 1, eid, rel_path, exported_at,
                    locator=f"records[{r_idx}]",
                    raw_store_name=origin
                )
                if obs:
                    observations.append(obs)
                else:
                    unparsed_records.append({'locator': f"records[{r_idx}]", 'record': rec})

            covering_snapshot = CoveringSnapshot(
                evidence_id=eid,
                file_path=rel_path,
                captured_at=exported_at,
                covered_systems=[sys_id],
                is_full_store_snapshot=False,  # Single-system export
                replica_coverage=['primary'],
                total_records=len(observations),
                records_by_system=records_by_system
            )

        report = ParseReport(
            evidence_id=eid,
            file_path=rel_path,
            file_type="json",
            status="SUCCESS" if not unparsed_records else "PARTIAL",
            observation_count=len(observations),
            unparsed_records=unparsed_records
        )
        return observations, covering_snapshot, report

    def _parse_json_record(
        self,
        rec: Dict[str, Any],
        sys_id: str,
        rec_idx: int,
        eid: str,
        rel_path: str,
        default_timestamp: str,
        locator: str,
        raw_store_name: Optional[str] = None
    ) -> Optional[Observation]:
        if not isinstance(rec, dict):
            return None

        rec_str = json.dumps(rec, ensure_ascii=False)
        res_id = rec.get('resource_id') or rec.get('subject_reference')
        if not res_id:
            m = RESOURCE_ID_REGEX.search(rec_str)
            if m:
                res_id = m.group(1)

        user_id = rec.get('user_id') or rec.get('customer_reference') or rec.get('subject_reference')
        if not user_id:
            m_u = USER_ID_REGEX.search(rec_str)
            if m_u:
                user_id = m_u.group(1)

        identifiers = []
        for k in ['email', 'phone', 'email_hash']:
            val = rec.get(k)
            if val:
                identifiers.append(str(val).strip().lower())

        # Support recorded_at and observed_at in addition to captured_at/updated_at/created_at
        obs_time = (
            rec.get('recorded_at')
            or rec.get('observed_at')
            or rec.get('captured_at')
            or rec.get('updated_at')
            or rec.get('created_at')
            or default_timestamp
        )

        # Preserve raw system identity before canonical mapping
        raw_sys = raw_store_name or rec.get('system') or sys_id

        # Determine target system: inner record system takes precedence over container
        target_sys = None
        if rec.get('system'):
            target_sys = normalize_system_id(rec.get('system'))

        if not target_sys and res_id:
            pfx = res_id.split('_')[0]
            target_sys = PREFIX_TO_SYSTEM.get(pfx)

        if not target_sys:
            target_sys = normalize_system_id(sys_id) or sys_id or 'account_service'

        state = self._determine_state(target_sys, rec)

        return Observation(
            evidence_id=eid,
            file_path=rel_path,
            locator=f"{locator}, res:{res_id}",
            observed_at=obs_time,
            system_id=target_sys,
            resource_id=res_id,
            user_id=user_id,
            identifiers=identifiers,
            state=state,
            raw_excerpt=rec_str[:200],
            raw_record=rec,
            parser_confidence=1.0,
            validation_status="VALID",
            raw_system=raw_sys
        )

    def _determine_state(self, sys_id: str, rec: Dict[str, Any]) -> str:
        # Check explicit result field first (e.g. operational_record_history lookups)
        result_val = str(rec.get('result') or '').upper()
        if result_val in ['NOT_FOUND', 'ABSENT']:
            return 'ABSENT'
        elif result_val == 'FOUND':
            return 'PRESENT'
        elif result_val == 'REDACTED':
            return 'REDACTED'
        elif result_val in ['ANONYMIZED', 'ANONYMOUS']:
            return 'ANONYMIZED'
        elif result_val == 'BLOCKED':
            return 'BLOCKED'
        elif result_val == 'DISABLED':
            return 'DISABLED'

        st = str(rec.get('account_status') or rec.get('status') or rec.get('state') or '').upper()

        if sys_id in ['account_service', 'search_service', 'export_service']:
            if st in ['DELETED', 'NOT_FOUND', 'ABSENT']:
                return 'ABSENT'
            if rec.get('hit_count') == 0:
                return 'ABSENT'
            return 'PRESENT'
        elif sys_id == 'identity_service':
            if st == 'REDACTED':
                return 'REDACTED'
            if not rec.get('name') and not rec.get('email') and not rec.get('phone') and not rec.get('email_hash'):
                return 'REDACTED'
            return 'PRESENT'
        elif sys_id == 'support_platform':
            if st in ['REDACTED', 'DELETED', 'ABSENT']:
                is_attach = rec.get('is_attachment', False) or ('attachment' in json.dumps(rec).lower())
                return 'ABSENT' if is_attach else 'REDACTED'
            return 'PRESENT'
        elif sys_id == 'analytics_platform':
            if st in ['ANONYMIZED', 'ANONYMOUS']:
                return 'ANONYMIZED'
            return 'PRESENT'
        elif sys_id == 'security_service':
            return 'PRESENT'
        elif sys_id == 'backup_service':
            if st in ['BLOCKED', 'DISABLED']:
                return 'BLOCKED'
            return 'ACTIVE'
        elif sys_id == 'integration_service':
            if st in ['DISABLED', 'INACTIVE', 'STOPPED']:
                return 'DISABLED'
            return 'ACTIVE'

        return 'PRESENT'
