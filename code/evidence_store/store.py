import csv
import os
from typing import List, Dict, Any, Optional, Set, Tuple

from code.evidence_store.models import Observation, CoveringSnapshot, ParseReport
from code.evidence_store.parsers import (
    BaseEvidenceParser,
    SqlSnapshotParser,
    JsonBundleParser,
    LogEvidenceParser,
    TextHandoverParser,
    PdfDocumentParser,
    ImageOcrParser
)

class EvidenceStore:
    """
    Central repository for parsed observations, covering snapshots, and provenance tracking.
    Enforces audit_at cutoffs and handles absence-by-omission and replica drift detection.
    """

    def __init__(self, dataset_dir: str):
        self.dataset_dir = dataset_dir
        self.manifest_path = os.path.join(dataset_dir, 'evidence_files.csv')

        self.parsers: List[BaseEvidenceParser] = [
            SqlSnapshotParser(),
            JsonBundleParser(),
            LogEvidenceParser(),
            TextHandoverParser(),
            PdfDocumentParser(),
            ImageOcrParser()
        ]

        self.observations: List[Observation] = []
        self.covering_snapshots: List[CoveringSnapshot] = []
        self.parse_reports: List[ParseReport] = []

        # Indexing structures for efficient case querying
        self.by_resource: Dict[str, List[Observation]] = {}
        self.by_user_id: Dict[str, List[Observation]] = {}
        self.by_identifier: Dict[str, List[Observation]] = {}
        self.by_system: Dict[str, List[Observation]] = {}
        self.by_evidence_id: Dict[str, List[Observation]] = {}

    def load_all_evidence(self) -> Dict[str, Any]:
        """
        Parses all evidence files specified in the manifest.
        Returns a summary report of the loading process.
        """
        if not os.path.exists(self.manifest_path):
            raise FileNotFoundError(f"Manifest not found: {self.manifest_path}")

        with open(self.manifest_path, 'r', encoding='utf-8') as f:
            manifest_rows = list(csv.DictReader(f))

        self.observations.clear()
        self.covering_snapshots.clear()
        self.parse_reports.clear()

        for entry in manifest_rows:
            fpath = entry['file_path']
            abs_path = os.path.join(self.dataset_dir, fpath)

            parser = next((p for p in self.parsers if p.can_parse(fpath)), None)
            if not parser:
                report = ParseReport(
                    evidence_id=entry['file_id'],
                    file_path=fpath,
                    file_type="unknown",
                    status="FAILED",
                    observation_count=0,
                    error_message=f"No parser available for {fpath}"
                )
                self.parse_reports.append(report)
                continue

            obs_list, snapshot, report = parser.parse(abs_path, entry)
            self.parse_reports.append(report)
            if snapshot:
                self.covering_snapshots.append(snapshot)

            for obs in obs_list:
                self._add_observation(obs)

        return self.generate_coverage_report()

    def _add_observation(self, obs: Observation):
        self.observations.append(obs)

        # Index by resource ID
        if obs.resource_id:
            self.by_resource.setdefault(obs.resource_id, []).append(obs)

        # Index by user ID
        if obs.user_id:
            self.by_user_id.setdefault(obs.user_id.lower(), []).append(obs)

        # Index by identifiers (emails, phone numbers, hashes)
        for ident in obs.identifiers:
            self.by_identifier.setdefault(ident.lower(), []).append(obs)

        # Index by system
        self.by_system.setdefault(obs.system_id, []).append(obs)

        # Index by evidence ID
        self.by_evidence_id.setdefault(obs.evidence_id, []).append(obs)

    def get_observations_for_user(
        self,
        user_id: Optional[str],
        emails: List[str],
        audit_at: str
    ) -> List[Observation]:
        """
        Retrieves all observations relevant to a user that were observed at or before audit_at.
        """
        results: List[Observation] = []
        seen_keys: Set[Tuple[str, str, str]] = set()

        candidate_obs: List[Observation] = []

        if user_id and user_id.lower() in self.by_user_id:
            candidate_obs.extend(self.by_user_id[user_id.lower()])

        for em in emails:
            em_clean = em.lower().strip()
            if em_clean in self.by_identifier:
                candidate_obs.extend(self.by_identifier[em_clean])

        for obs in candidate_obs:
            if obs.observed_at <= audit_at:
                key = (obs.evidence_id, obs.system_id, obs.resource_id or obs.locator)
                if key not in seen_keys:
                    seen_keys.add(key)
                    results.append(obs)

        results.sort(key=lambda x: x.observed_at, reverse=True)
        return results

    def get_covering_snapshot_for_absence(
        self,
        system_id: str,
        audit_at: str,
        require_full_store: bool = True
    ) -> Optional[CoveringSnapshot]:
        """
        Finds the latest covering snapshot valid at audit_at that covers system_id.
        Rejects partial exports if require_full_store is True.
        """
        valid_snaps = [
            s for s in self.covering_snapshots
            if s.captured_at <= audit_at
            and system_id in s.covered_systems
            and (not require_full_store or s.is_full_store_snapshot)
        ]
        if not valid_snaps:
            return None

        # Return latest valid covering snapshot
        valid_snaps.sort(key=lambda s: s.captured_at, reverse=True)
        return valid_snaps[0]

    def generate_coverage_report(self) -> Dict[str, Any]:
        """
        Summarizes parsing coverage, observation counts, and snapshot counts.
        """
        total_files = len(self.parse_reports)
        successful_files = sum(1 for r in self.parse_reports if r.status == 'SUCCESS')
        partial_files = sum(1 for r in self.parse_reports if r.status == 'PARTIAL')
        failed_files = sum(1 for r in self.parse_reports if r.status == 'FAILED')

        obs_by_type: Dict[str, int] = {}
        for r in self.parse_reports:
            obs_by_type[r.file_type] = obs_by_type.get(r.file_type, 0) + r.observation_count

        obs_by_system: Dict[str, int] = {}
        for obs in self.observations:
            obs_by_system[obs.system_id] = obs_by_system.get(obs.system_id, 0) + 1

        replicas_count = sum(1 for o in self.observations if o.is_replica)

        return {
            'total_files_in_manifest': total_files,
            'successful_files': successful_files,
            'partial_files': partial_files,
            'failed_files': failed_files,
            'total_observations': len(self.observations),
            'observations_by_file_type': obs_by_type,
            'observations_by_system': obs_by_system,
            'replica_observations': replicas_count,
            'covering_snapshots_registered': len(self.covering_snapshots),
            'failed_file_details': [
                {'evidence_id': r.evidence_id, 'file_path': r.file_path, 'error': r.error_message}
                for r in self.parse_reports if r.status == 'FAILED'
            ]
        }
