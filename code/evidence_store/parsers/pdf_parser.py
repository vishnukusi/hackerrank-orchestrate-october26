import os
import re
from typing import List, Tuple, Dict, Any, Optional

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

from code.evidence_store.models import Observation, CoveringSnapshot, ParseReport
from code.evidence_store.parsers.base import (
    BaseEvidenceParser,
    normalize_system_id,
    PREFIX_TO_SYSTEM,
    RESOURCE_ID_REGEX,
    USER_ID_REGEX,
    EMAIL_REGEX
)

class PdfDocumentParser(BaseEvidenceParser):
    """
    Parses PDF document attachments and compliance registers using text-layer extraction.
    Extracts customer statements, support attachments, and compliance retention records.
    """

    def can_parse(self, file_path: str) -> bool:
        return file_path.lower().endswith('.pdf')

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
                file_type="pdf",
                status="FAILED",
                observation_count=0,
                error_message=f"File not found: {abs_path}"
            )
            return observations, None, report

        if not PYPDF_AVAILABLE:
            report = ParseReport(
                evidence_id=eid,
                file_path=rel_path,
                file_type="pdf",
                status="FAILED",
                observation_count=0,
                error_message="pypdf package is not installed."
            )
            return observations, None, report

        try:
            reader = pypdf.PdfReader(abs_path)
            for page_idx, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ''
                if not page_text.strip():
                    continue

                res_matches = RESOURCE_ID_REGEX.findall(page_text)
                user_matches = USER_ID_REGEX.findall(page_text)
                email_matches = EMAIL_REGEX.findall(page_text)

                # Check if this document mentions any relevant resource or user
                if res_matches or user_matches or email_matches:
                    for res_id in (res_matches or [None]):
                        user_id = user_matches[0] if user_matches else None
                        emails = [e.lower() for e in email_matches]

                        # Determine target system
                        sys_id = None
                        if res_id:
                            pfx = res_id.split('_')[0]
                            sys_id = PREFIX_TO_SYSTEM.get(pfx)

                        if not sys_id:
                            if 'delivery_correction' in rel_path or 'support' in page_text.lower():
                                sys_id = 'support_platform'
                            elif 'security' in rel_path or 'compliance' in page_text.lower():
                                sys_id = 'security_service'
                            elif 'account' in rel_path:
                                sys_id = 'account_service'
                            else:
                                sys_id = 'support_platform'

                        # Determine state
                        # Retention records (security_service) are retained -> PRESENT
                        # Support attachments (e.g. delivery_correction_attachment) are PRESENT until deleted
                        state = 'PRESENT'

                        obs = Observation(
                            evidence_id=eid,
                            file_path=rel_path,
                            locator=f"page:{page_idx}, res:{res_id}",
                            observed_at=manifest_time,
                            system_id=sys_id,
                            resource_id=res_id,
                            user_id=user_id,
                            identifiers=emails,
                            state=state,
                            raw_excerpt=page_text[:200].replace('\n', ' '),
                            parser_confidence=1.0,
                            validation_status="VALID",
                            metadata={'page_number': page_idx, 'total_pages': len(reader.pages)},
                            raw_system=sys_id
                        )
                        observations.append(obs)

        except Exception as e:
            report = ParseReport(
                evidence_id=eid,
                file_path=rel_path,
                file_type="pdf",
                status="FAILED",
                observation_count=0,
                error_message=f"PDF extraction error: {e}"
            )
            return observations, None, report

        report = ParseReport(
            evidence_id=eid,
            file_path=rel_path,
            file_type="pdf",
            status="SUCCESS",
            observation_count=len(observations)
        )
        return observations, None, report
