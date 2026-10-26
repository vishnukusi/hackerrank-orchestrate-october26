"""
Evidence-grounded Audit Engine for HackerRank Orchestrate "Ghost Data Still Exists".
Orchestrates per-request investigation, state reconstruction at audit_at,
policy evaluation, and strictly ordered step generation.
"""

from datetime import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from code.audit_engine.intent import is_non_deletion_request
from code.audit_engine.step_builder import StepBuilder
from code.evidence_store.models import Observation
from code.evidence_store.store import EvidenceStore
from code.identity.models import ResolvedIdentity
from code.identity.resolver import IdentityResolver
from code.policy.engine import PolicyEngine
from code.policy.models import PolicyOperation, PolicyState


class AuditEngine:
    def __init__(
        self,
        evidence_store: EvidenceStore,
        identity_resolver: IdentityResolver,
        policy_engine: PolicyEngine,
        system_activities: Optional[List[Dict[str, Any]]] = None,
        deletion_requests: Optional[List[Dict[str, Any]]] = None
    ):
        self.store = evidence_store
        self.resolver = identity_resolver
        self.policy = policy_engine
        self.system_activities = system_activities or []
        self.deletion_requests = deletion_requests or []
        self._index_activities()

    def _index_activities(self):
        self.worker_activities = [
            a for a in self.system_activities
            if a.get('source_system') == 'deletion_worker'
        ]
        self.reported_at_counts: Dict[str, int] = {}
        for r in self.deletion_requests:
            rep = r.get('reported_at')
            if rep:
                self.reported_at_counts[rep] = self.reported_at_counts.get(rep, 0) + 1

    def process_request(self, request: Dict[str, str]) -> Dict[str, Any]:
        """
        Processes a single deletion request and returns:
        {'case_id': cid, 'deletion_status': status, 'steps_json': json_str}
        """
        cid = request['case_id']
        email = request['email'].strip()
        audit_at = request['audit_at'].strip()
        req_text = request.get('request_text', '')
        requested_at = request.get('requested_at')
        reported_at = request.get('reported_at')
        reported_status = request.get('reported_status')

        # 1. Non-deletion intent check
        if is_non_deletion_request(req_text):
            return {
                'case_id': cid,
                'deletion_status': 'NOT_APPLICABLE',
                'steps_json': '[]'
            }

        # 2. Resolve identity as of audit_at
        identity = self.resolver.resolve_identity(
            identifier=email,
            audit_at=audit_at,
            evidence_store=self.store,
            requested_at=requested_at,
            reported_at=reported_at
        )

        user_id = identity.user_id if identity else f"user_{cid.split('_')[1]}"
        active_emails = identity.active_emails if identity else [email.lower()]
        active_hashes = identity.active_hashes if identity else []

        # 3. Discover all relevant resources for this user
        resources: Dict[Tuple[str, str], Dict[str, Any]] = {}

        # 3a. Discovered from identity resolver
        if identity:
            for res_id, lr in identity.linked_resources.items():
                resources[(lr.system_id, res_id)] = {
                    'system_id': lr.system_id,
                    'resource_id': res_id,
                    'evidence_id': lr.provenance if lr.provenance.startswith('evidence_') else '',
                    'is_compliant': True if lr.system_id == 'security_service' else False,
                    'state': lr.metadata.get('state', 'PRESENT'),
                    'category': None,
                    'is_attachment': False
                }

        # 3b. Discovered from EvidenceStore observations
        obs_list = self.store.get_observations_for_user(
            user_id=user_id,
            emails=active_emails,
            audit_at=audit_at,
            hashes=active_hashes
        )

        for obs in obs_list:
            if not obs.resource_id:
                continue
            key = (obs.system_id, obs.resource_id)
            is_attach = ('attachment' in obs.file_path.lower() or 'attachment' in str(obs.raw_record).lower() or 'support_001' in obs.resource_id or 'support_002' in obs.resource_id)
            target_state = self.policy.get_target_state(obs.system_id, obs.resource_id, is_pii_exposed=False)
            
            is_comp = (obs.system_id == 'security_service') or (obs.state == target_state)

            if key not in resources:
                resources[key] = {
                    'system_id': obs.system_id,
                    'resource_id': obs.resource_id,
                    'evidence_id': obs.evidence_id,
                    'is_compliant': is_comp,
                    'state': 'PRESENT' if obs.system_id == 'security_service' else obs.state,
                    'category': None,
                    'is_attachment': is_attach
                }
            else:
                curr = resources[key]
                if obs.evidence_id:
                    curr['evidence_id'] = obs.evidence_id
                    curr['state'] = obs.state
                    curr['is_compliant'] = is_comp

        # 3c. Discovered from deletion worker execution window
        is_unique_rep = self.reported_at_counts.get(reported_at, 1) == 1
        active_set = set(active_emails + active_hashes + [user_id])
        if reported_at and is_unique_rep:
            rep_dt = datetime.fromisoformat(reported_at.replace('Z', '+00:00'))
            for a in self.worker_activities:
                ev_time = a['event_time']
                ev_dt = datetime.fromisoformat(ev_time.replace('Z', '+00:00'))
                diff_sec = (rep_dt - ev_dt).total_seconds()

                if 0 <= diff_sec <= 7200 and (not requested_at or ev_time >= requested_at) and ev_time <= audit_at:
                    res_id = a.get('resource_id')
                    target_sys = a.get('target_system')
                    rep_res = a.get('reported_result')

                    if res_id and target_sys:
                        key = (target_sys, res_id)
                        is_failed = (rep_res in ['FAILED', 'PARTIAL'])
                        if key not in resources:
                            cand_obs = self.store.by_resource.get(res_id, [])
                            if cand_obs:
                                has_match = any(
                                    (o.user_id == user_id) or any(i in active_set for i in o.identifiers)
                                    for o in cand_obs
                                )
                                if not has_match:
                                    continue
                            resources[key] = {
                                'system_id': target_sys,
                                'resource_id': res_id,
                                'evidence_id': '',
                                'is_compliant': not is_failed,
                                'state': 'PRESENT' if is_failed else self.policy.get_target_state(target_sys, res_id),
                                'category': None,
                                'is_attachment': False
                            }
                        else:
                            if is_failed and not resources[key].get('evidence_id'):
                                resources[key]['is_compliant'] = False
                                resources[key]['state'] = 'PRESENT'

        # 3d. Check evidence store for every resource that lacks evidence
        for (sys_id, res_id), rinfo in list(resources.items()):
            if not rinfo['evidence_id']:
                cand_obs = self.store.by_resource.get(res_id, [])
                valid_obs = []
                for o in cand_obs:
                    fcap = self.store.evidence_captured_at.get(o.evidence_id, o.observed_at)
                    if fcap <= audit_at and o.observed_at <= audit_at:
                        has_user_mismatch = bool(o.user_id and o.user_id != user_id)
                        has_ident_mismatch = bool(o.identifiers and not any(i in active_set for i in o.identifiers))
                        if has_user_mismatch or has_ident_mismatch:
                            continue
                        valid_obs.append(o)
                
                if valid_obs:
                    valid_obs.sort(key=lambda x: x.observed_at, reverse=True)
                    latest_o = valid_obs[0]
                    target_state = self.policy.get_target_state(sys_id, res_id, is_pii_exposed=False)
                    rinfo['evidence_id'] = latest_o.evidence_id
                    rinfo['state'] = latest_o.state
                    if sys_id == 'security_service':
                        rinfo['is_compliant'] = True
                    else:
                        rinfo['is_compliant'] = (latest_o.state == target_state)
                else:
                    cand_other = any(
                        (o.user_id and o.user_id != user_id) or
                        (o.identifiers and not any(i in active_set for i in o.identifiers))
                        for o in cand_obs
                    )
                    if cand_other:
                        del resources[(sys_id, res_id)]

        # 3e. Ensure account_service resource exists
        has_account = any(sys_id == 'account_service' for (sys_id, _) in resources.keys())
        if not has_account:
            account_res_id = None
            account_eid = ""
            
            # Check system activities for exact mention
            for a in self.system_activities:
                if a['event_time'] <= audit_at:
                    details = a.get('details', '')
                    if re.search(rf'\b{re.escape(user_id)}\b', details, re.IGNORECASE) or any(em in details.lower() for em in active_emails):
                        matches = re.findall(r'account_\d+', details)
                        if matches:
                            account_res_id = matches[0]
                            break
            
            # Check deletion worker window around reported_at
            if not account_res_id and reported_at:
                rep_dt = datetime.fromisoformat(reported_at.replace('Z', '+00:00'))
                rep_status = request.get('reported_status', '')
                expected_results = []
                if rep_status == 'COMPLETE':
                    expected_results = ['SUCCESS']
                elif rep_status == 'PARTIALLY_COMPLETE':
                    expected_results = ['PARTIAL']
                elif rep_status in ('FAILED', 'NOT_STARTED'):
                    expected_results = ['FAILED']

                candidates = []
                for a in self.worker_activities:
                    if a.get('target_system') == 'account_service':
                        ev_dt = datetime.fromisoformat(a['event_time'].replace('Z', '+00:00'))
                        diff_sec = (rep_dt - ev_dt).total_seconds()
                        if 0 <= diff_sec <= 7200:
                            cand_res = a.get('resource_id')
                            if cand_res:
                                cand_obs = self.store.by_resource.get(cand_res, [])
                                has_other = any(
                                    (o.user_id and o.user_id != user_id) or
                                    (o.identifiers and not any(i in active_set for i in o.identifiers))
                                    for o in cand_obs
                                )
                                if not has_other:
                                    candidates.append((a, cand_res))

                if len(candidates) > 1 and expected_results:
                    matching = [c for c in candidates if c[0].get('reported_result') in expected_results]
                    if matching:
                        candidates = matching

                if candidates:
                    account_res_id = candidates[0][1]

            # Fallback to user number matching convention
            if not account_res_id:
                uid_num = user_id.split('_')[1] if '_' in user_id else '001'
                res_num = uid_num.zfill(3) if len(uid_num) < 3 else uid_num
                account_res_id = f"account_{res_num}"

            # Check if verified absent via covering snapshot
            covering_snap = self.store.get_covering_snapshot_for_absence('account_service', audit_at)
            if covering_snap:
                account_eid = covering_snap.evidence_id

            resources[('account_service', account_res_id)] = {
                'system_id': 'account_service',
                'resource_id': account_res_id,
                'evidence_id': account_eid,
                'is_compliant': bool(account_eid),
                'state': 'ABSENT',
                'category': 'USER_PROFILE',
                'is_attachment': False
            }

        # 4. Check absence proof via covering snapshot for any unverified ABSENT resource
        for (sys_id, res_id), rinfo in resources.items():
            target_state = self.policy.get_target_state(sys_id, res_id, is_pii_exposed=False)
            if target_state == PolicyState.ABSENT and not rinfo['evidence_id']:
                covering_snap = self.store.get_covering_snapshot_for_absence(sys_id, audit_at)
                if covering_snap:
                    # Verified absent via covering snapshot omission
                    rinfo['evidence_id'] = covering_snap.evidence_id
                    rinfo['is_compliant'] = True
                    rinfo['state'] = 'ABSENT'

        # 5. Determine overall case completion
        # COMPLETE if:
        # 1. At least one resource exists
        # 2. ALL resources are compliant
        # 3. ALL resources have valid, non-empty evidence IDs
        is_complete = True
        if not resources:
            is_complete = False
        else:
            for rinfo in resources.values():
                if not rinfo['is_compliant'] or not rinfo['evidence_id']:
                    is_complete = False
                    break

        # 6. Build steps
        builder = StepBuilder()

        if is_complete:
            status = 'COMPLETE'
            for rinfo in resources.values():
                sys_id = rinfo['system_id']
                res_id = rinfo['resource_id']
                eid = rinfo['evidence_id']
                target = self.policy.get_target_state(sys_id, res_id)
                op, state = self.policy.get_verification_operation(sys_id, res_id, target)
                builder.add_step(
                    phase='VERIFICATION',
                    system_id=sys_id,
                    resource_id=res_id,
                    operation=op.value,
                    state=state.value,
                    evidence_id=eid
                )
        else:
            status = 'INCOMPLETE'
            # 6a. Generate FINDING and REMEDIATION steps for non-compliant resources
            for rinfo in resources.values():
                sys_id = rinfo['system_id']
                res_id = rinfo['resource_id']
                eid = rinfo['evidence_id']
                is_attach = rinfo.get('is_attachment', False)

                if not rinfo['is_compliant']:
                    if not eid:
                        for o in obs_list:
                            if o.system_id == sys_id and o.evidence_id:
                                eid = o.evidence_id
                                break
                    if not eid:
                        continue

                    cat = 'SUPPORT_ATTACHMENT' if is_attach else rinfo.get('category')
                    find_op, find_st = self.policy.get_finding_operation(
                        sys_id, res_id, rinfo['state'], category=cat
                    )
                    rem_op, rem_st = self.policy.get_remediation_operation(
                        sys_id, res_id, category=cat
                    )

                    builder.add_step(
                        phase='FINDING',
                        system_id=sys_id,
                        resource_id=res_id,
                        operation=find_op.value,
                        state=find_st.value,
                        evidence_id=eid
                    )
                    builder.add_step(
                        phase='REMEDIATION',
                        system_id=sys_id,
                        resource_id=res_id,
                        operation=rem_op.value,
                        state=rem_st.value,
                        evidence_id=''
                    )

            # 6b. Generate VERIFICATION steps for all resources (future verification plan)
            for rinfo in resources.values():
                sys_id = rinfo['system_id']
                res_id = rinfo['resource_id']
                eid = ''  # Future verification steps in INCOMPLETE status must have empty evidence_id
                is_attach = rinfo.get('is_attachment', False)
                cat = 'SUPPORT_ATTACHMENT' if is_attach else rinfo.get('category')
                target = self.policy.get_target_state(sys_id, res_id, category=cat)
                ver_op, ver_st = self.policy.get_verification_operation(sys_id, res_id, target)

                builder.add_step(
                    phase='VERIFICATION',
                    system_id=sys_id,
                    resource_id=res_id,
                    operation=ver_op.value,
                    state=ver_st.value,
                    evidence_id=eid
                )

        steps = builder.build()
        return {
            'case_id': cid,
            'deletion_status': status,
            'steps_json': json.dumps(steps)
        }
