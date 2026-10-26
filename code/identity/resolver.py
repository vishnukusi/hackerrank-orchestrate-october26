import csv
import json
import os
import re
from typing import List, Dict, Any, Optional, Set

from code.evidence_store import EvidenceStore
from code.evidence_store.parsers.base import PREFIX_TO_SYSTEM, normalize_system_id, RESOURCE_ID_REGEX
from code.identity.models import LinkedResource, ResolvedIdentity

class IdentityResolver:
    """
    Dedicated identity-resolution layer.
    Reconstructs user states at audit_at, resolves historical email aliases,
    and links proven resources with explicit provenance.
    """

    def __init__(self, dataset_dir: str):
        self.dataset_dir = dataset_dir
        self.identities_by_user_id: Dict[str, Dict[str, Any]] = {}
        self.user_id_by_email: Dict[str, str] = {}
        self.audit_logs_by_user_id: Dict[str, List[Dict[str, Any]]] = {}
        self.activities: List[Dict[str, Any]] = []

        self._load_identity_data()

    def _load_identity_data(self):
        # 1. Load user_identities.csv
        identities_path = os.path.join(self.dataset_dir, 'user_identities.csv')
        if os.path.exists(identities_path):
            with open(identities_path, 'r', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    uid = row['user_id'].strip().lower()
                    self.identities_by_user_id[uid] = row
                    em = row.get('email', '').strip().lower()
                    if em:
                        self.user_id_by_email[em] = uid

        # 2. Load user_audit_log.csv
        audit_path = os.path.join(self.dataset_dir, 'user_audit_log.csv')
        if os.path.exists(audit_path):
            with open(audit_path, 'r', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    uid = row['user_id'].strip().lower()
                    self.audit_logs_by_user_id.setdefault(uid, []).append(row)
                    if row.get('field_name') == 'email':
                        old_em = row.get('old_value', '').strip().lower()
                        new_em = row.get('new_value', '').strip().lower()
                        if old_em:
                            self.user_id_by_email[old_em] = uid
                        if new_em:
                            self.user_id_by_email[new_em] = uid

        # Sort audit logs chronologically
        for uid in self.audit_logs_by_user_id:
            self.audit_logs_by_user_id[uid].sort(key=lambda x: x.get('event_time', ''))

        # 3. Load system_activity.csv
        act_path = os.path.join(self.dataset_dir, 'system_activity.csv')
        if os.path.exists(act_path):
            with open(act_path, 'r', encoding='utf-8') as f:
                self.activities = list(csv.DictReader(f))

    def find_user_id_for_email(self, email: str) -> Optional[str]:
        if not email:
            return None
        em_clean = email.strip().lower()
        return self.user_id_by_email.get(em_clean)

    def resolve_identity(
        self,
        identifier: str,
        audit_at: str,
        evidence_store: Optional[EvidenceStore] = None,
        requested_at: Optional[str] = None,
        reported_at: Optional[str] = None
    ) -> Optional[ResolvedIdentity]:
        """
        Reconstructs the identity profile as of audit_at.
        identifier: either a user_id (e.g. 'user_01') or an email.
        """
        ident_clean = identifier.strip().lower()

        # Step 1: Identify user_id
        if ident_clean.startswith('user_'):
            user_id = ident_clean
        else:
            user_id = self.find_user_id_for_email(ident_clean)

        if not user_id or user_id not in self.identities_by_user_id:
            return None

        base_rec = self.identities_by_user_id[user_id]
        current_email = base_rec.get('email', '').strip().lower()

        # Step 2: Time-travel email alias reconstruction
        user_audits = self.audit_logs_by_user_id.get(user_id, [])
        email_audits = [a for a in user_audits if a.get('field_name') == 'email']

        # Determine active email at cutoff
        # Reverse search from latest change
        email_at_cutoff = current_email
        for a in reversed(email_audits):
            if a['event_time'] > audit_at:
                if a.get('old_value'):
                    email_at_cutoff = a['old_value'].strip().lower()

        # Collect all valid emails active at or prior to audit_at
        active_emails_set: Set[str] = set()
        if email_at_cutoff:
            active_emails_set.add(email_at_cutoff)
        if not ident_clean.startswith('user_'):
            active_emails_set.add(ident_clean)

        for a in email_audits:
            if a['event_time'] <= audit_at:
                if a.get('old_value'):
                    active_emails_set.add(a['old_value'].strip().lower())
                if a.get('new_value'):
                    active_emails_set.add(a['new_value'].strip().lower())

        active_emails = sorted(list(active_emails_set))

        # Reconstruct active email hashes
        hash_audits = [a for a in user_audits if a.get('field_name') == 'email_hash']
        active_hashes_set: Set[str] = set()
        base_hash = base_rec.get('email_hash', '').strip().lower()
        if base_hash:
            active_hashes_set.add(base_hash)
        for a in hash_audits:
            if a['event_time'] <= audit_at:
                if a.get('old_value'):
                    active_hashes_set.add(a['old_value'].strip().lower())
                if a.get('new_value'):
                    active_hashes_set.add(a['new_value'].strip().lower())
        active_hashes = sorted(list(active_hashes_set))

        resolved = ResolvedIdentity(
            user_id=user_id,
            name=base_rec.get('name', ''),
            current_email=current_email,
            email_at_cutoff=email_at_cutoff,
            active_emails=active_emails,
            phone=base_rec.get('phone', ''),
            email_hash=base_rec.get('email_hash', ''),
            audit_at=audit_at,
            active_hashes=active_hashes,
            email_changes=[a for a in email_audits if a['event_time'] <= audit_at]
        )

        # Step 3: Discover linked resources
        # A. Link resources from system_activity.csv
        self._link_resources_from_activity(resolved, audit_at, requested_at, reported_at)

        # B. Link resources from EvidenceStore observations
        if evidence_store:
            self._link_resources_from_evidence(resolved, evidence_store, audit_at)

        return resolved

    def _link_resources_from_activity(
        self,
        resolved: ResolvedIdentity,
        audit_at: str,
        requested_at: Optional[str],
        reported_at: Optional[str]
    ):
        user_id = resolved.user_id
        active_emails = set(resolved.active_emails)

        for act in self.activities:
            event_time = act.get('event_time', '')
            if event_time > audit_at:
                continue

            details = act.get('details', '')
            res_id = act.get('resource_id')
            event_type = act.get('event_type', '')

            # Check for explicit LINK_RESOURCE_IDENTITY event
            if event_type == 'LINK_RESOURCE_IDENTITY':
                if user_id in details or any(em in details.lower() for em in active_emails):
                    if res_id:
                        sys_id = normalize_system_id(act.get('target_system') or act.get('source_system'))
                        if not sys_id:
                            pfx = res_id.split('_')[0]
                            sys_id = PREFIX_TO_SYSTEM.get(pfx, 'account_service')
                        resolved.linked_resources[res_id] = LinkedResource(
                            resource_id=res_id,
                            system_id=sys_id,
                            link_type='SYSTEM_ACTIVITY_LINK',
                            provenance=act['activity_id'],
                            discovered_at=event_time,
                            metadata={'details': details, 'event_type': event_type}
                        )
                continue

            # Check if details explicitly mentions user_id with word boundary
            user_pattern = rf'\b{re.escape(user_id)}\b'
            has_user_mention = bool(re.search(user_pattern, details, re.IGNORECASE))

            # Check if details explicitly mentions any active email
            has_email_mention = any(em in details.lower() for em in active_emails)

            if has_user_mention or has_email_mention:
                if res_id:
                    sys_id = normalize_system_id(act.get('target_system') or act.get('source_system'))
                    if not sys_id:
                        pfx = res_id.split('_')[0]
                        sys_id = PREFIX_TO_SYSTEM.get(pfx, 'account_service')
                    resolved.linked_resources[res_id] = LinkedResource(
                        resource_id=res_id,
                        system_id=sys_id,
                        link_type='EXACT_USER_ID' if has_user_mention else 'EXACT_EMAIL',
                        provenance=act['activity_id'],
                        discovered_at=event_time,
                        metadata={'details': details, 'event_type': event_type}
                    )

            # Check deletion worker window around reported_at
            # Deletion worker events targeting account_service, search_service, etc.
            if reported_at and requested_at:
                # If activity is a deletion worker submission in the request window
                if act.get('source_system') == 'deletion_worker':
                    # Check if requested_at <= event_time <= reported_at + 15 mins
                    if requested_at <= event_time <= reported_at:
                        # If details match or if no resource is found for this system yet and email matched
                        if has_user_mention or has_email_mention:
                            if res_id and res_id not in resolved.linked_resources:
                                sys_id = normalize_system_id(act.get('target_system'))
                                if not sys_id:
                                    pfx = res_id.split('_')[0]
                                    sys_id = PREFIX_TO_SYSTEM.get(pfx, 'account_service')
                                resolved.linked_resources[res_id] = LinkedResource(
                                    resource_id=res_id,
                                    system_id=sys_id,
                                    link_type='DELETION_WORKER_WINDOW',
                                    provenance=act['activity_id'],
                                    discovered_at=event_time,
                                    metadata={'details': details}
                                )

    def _link_resources_from_evidence(
        self,
        resolved: ResolvedIdentity,
        evidence_store: EvidenceStore,
        audit_at: str
    ):
        obs_list = evidence_store.get_observations_for_user(
            user_id=resolved.user_id,
            emails=resolved.active_emails,
            audit_at=audit_at
        )

        for obs in obs_list:
            if obs.resource_id:
                res_id = obs.resource_id
                # Only add if not already linked with higher specificity, or update with evidence provenance
                if res_id not in resolved.linked_resources:
                    resolved.linked_resources[res_id] = LinkedResource(
                        resource_id=res_id,
                        system_id=obs.system_id,
                        link_type='EVIDENCE_RECORD',
                        provenance=obs.evidence_id,
                        discovered_at=obs.observed_at,
                        metadata={'locator': obs.locator, 'state': obs.state}
                    )
                else:
                    # Update provenance if it gives direct evidence
                    lr = resolved.linked_resources[res_id]
                    if lr.link_type != 'EVIDENCE_RECORD':
                        lr.metadata['evidence_id'] = obs.evidence_id
                        lr.metadata['observed_state'] = obs.state
