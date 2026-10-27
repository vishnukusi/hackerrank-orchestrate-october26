import unittest
import csv
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from code.main import (
    get_identity_info,
    is_non_deletion_request,
    query_case_resources,
    build_case_steps,
    DELETION_RULES
)

class TestDeletionAuditRegression(unittest.TestCase):

    def setUp(self):
        self.dataset_dir = os.path.join(REPO_ROOT, 'dataset')
        with open(os.path.join(self.dataset_dir, 'user_identities.csv'), 'r', encoding='utf-8') as f:
            self.identities = list(csv.DictReader(f))
        with open(os.path.join(self.dataset_dir, 'user_audit_log.csv'), 'r', encoding='utf-8') as f:
            self.audit_logs = list(csv.DictReader(f))
        with open(os.path.join(self.dataset_dir, 'system_activity.csv'), 'r', encoding='utf-8') as f:
            self.system_activities = list(csv.DictReader(f))
        with open(os.path.join(self.dataset_dir, 'evidence_files.csv'), 'r', encoding='utf-8') as f:
            self.evidence_manifest = list(csv.DictReader(f))

    def test_audit_at_cutoff_enforcement(self):
        # Historical email changed AFTER audit_at should NOT be resolved for cutoff
        req_email = "nadia.ionescu@yahoo.com"
        cutoff_early = "2024-01-01T00:00:00Z"
        info = get_identity_info(req_email, cutoff_early, self.identities, self.audit_logs)
        if info:
            # Check historical emails captured only up to cutoff_early
            self.assertNotIn("n.petrescu@yahoo.com", info['all_emails'])

    def test_historical_email_alias_resolution(self):
        req_email = "nadia.ionescu@yahoo.com"
        cutoff_late = "2026-09-18T12:25:00Z"
        info = get_identity_info(req_email, cutoff_late, self.identities, self.audit_logs)
        self.assertIsNotNone(info)
        self.assertEqual(info['user_id'], 'user_10')
        self.assertIn("nadia.petrescu@yahoo.com", info['all_emails'])

    def test_non_deletion_intent_detection(self):
        self.assertTrue(is_non_deletion_request("The mobile number on my profile is out of date. Please replace it with the new one and leave the account active."))
        self.assertTrue(is_non_deletion_request("I'd like to download my account information in a portable format. Please keep the account as it is."))
        self.assertFalse(is_non_deletion_request("Please delete my personal data completely from all systems."))

    def test_retained_records_policy_rules(self):
        self.assertEqual(DELETION_RULES.get('SECURITY_AUDIT_LOG'), 'RETAIN')
        self.assertEqual(DELETION_RULES.get('ACCOUNT_TOMBSTONE'), 'REDACT')
        self.assertEqual(DELETION_RULES.get('ANALYTICS_PROFILE'), 'ANONYMIZE')
        self.assertEqual(DELETION_RULES.get('USER_PROFILE'), 'DELETE')

    def test_remediation_dependency_ordering(self):
        mock_resources = [
            {'system_id': 'analytics_platform', 'resource_id': 'analytics_001', 'is_compliant': False, 'evidence_id': 'evidence_01'},
            {'system_id': 'integration_service', 'resource_id': 'data_flow_001', 'is_compliant': False, 'evidence_id': 'evidence_01'},
            {'system_id': 'search_service', 'resource_id': 'search_001', 'is_compliant': False, 'evidence_id': 'evidence_01'},
            {'system_id': 'backup_service', 'resource_id': 'backup_001', 'is_compliant': False, 'evidence_id': 'evidence_01'}
        ]
        status, steps = build_case_steps(mock_resources, 'evidence_01')
        self.assertEqual(status, 'INCOMPLETE')

        rem_steps = [s for s in steps if s['phase'] == 'REMEDIATION']
        op_sequence = [s['operation'] for s in rem_steps]

        # DISABLE_DATA_FLOW must come before DELETE_RECORD and ANONYMIZE_RECORD
        df_idx = op_sequence.index('DISABLE_DATA_FLOW')
        bk_idx = op_sequence.index('BLOCK_RESTORE')
        del_idx = op_sequence.index('DELETE_RECORD')
        anon_idx = op_sequence.index('ANONYMIZE_RECORD')

        self.assertLess(df_idx, del_idx)
        self.assertLess(bk_idx, del_idx)
        self.assertLess(del_idx, anon_idx)

    def test_trace_id_log_grouping(self):
        import tempfile
        from code.evidence_store.parsers.log_parser import LogEvidenceParser
        raw_log = (
            "2026-09-17T08:00:00Z INFO [trace_id=tr-test-01] lookup_started: lookup_key=test@example.com store=search_index\n"
            "2026-09-17T08:00:01Z INFO [trace_id=tr-test-01] lookup_completed: status=404 rows=0\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(raw_log)
            tmp_path = f.name
        try:
            parser = LogEvidenceParser()
            manifest_entry = {"file_id": "evidence_test", "file_path": "test.log", "captured_at": "2026-09-17T08:00:01Z"}
            obs, _, _ = parser.parse(tmp_path, manifest_entry)
            self.assertTrue(any("test@example.com" in o.identifiers and o.state == "ABSENT" for o in obs))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_historical_email_hash_resolution(self):
        from code.identity.resolver import IdentityResolver
        resolver = IdentityResolver(self.dataset_dir)
        identity_12 = resolver.resolve_identity("minjun.kim@gmail.com", "2026-09-20T00:00:00Z")
        self.assertIsNotNone(identity_12)
        self.assertIn("44a65154bfd8e408852578fbd76813d094d1944f255ac1769ee81ba90492f10e", identity_12.active_hashes)

    def test_event_manifest_not_covering_snapshot(self):
        from code.evidence_store.store import EvidenceStore
        store = EvidenceStore(self.dataset_dir)
        store.load_all_evidence()
        # Verify single-record JSON files are not registered as covering snapshots
        e13_snaps = [s for s in store.covering_snapshots if s.evidence_id == "evidence_13"]
        self.assertEqual(len(e13_snaps), 0)
        e36_snaps = [s for s in store.covering_snapshots if s.evidence_id == "evidence_36"]
        self.assertEqual(len(e36_snaps), 0)


    def test_worker_activity_correlation_window_and_reported_result(self):
        """Worker activities in 2h window must correlate correctly using reported_status match."""
        from code.evidence_store.store import EvidenceStore
        from code.identity.resolver import IdentityResolver
        from code.policy.engine import PolicyEngine
        from code.audit_engine.engine import AuditEngine
        store = EvidenceStore(self.dataset_dir)
        store.load_all_evidence()
        resolver = IdentityResolver(self.dataset_dir)
        policy = PolicyEngine(os.path.join(self.dataset_dir, 'deletion_rules.csv'))
        with open(os.path.join(self.dataset_dir, 'sample_deletion_requests.csv'), encoding='utf-8') as f:
            reqs = list(csv.DictReader(f))
        engine = AuditEngine(store, resolver, policy, self.system_activities, reqs)
        r31 = next(r for r in reqs if r['case_id'] == 'case_31')
        r32 = next(r for r in reqs if r['case_id'] == 'case_32')
        res31 = engine.process_request(r31)
        res32 = engine.process_request(r32)
        self.assertIn('account_4857', res31['steps_json'])
        self.assertIn('account_8858', res32['steps_json'])

    def test_audit_evidence_overrides_prior_worker_failure(self):
        """Audit observation of ABSENT must take precedence over prior worker FAILED submission."""
        from code.evidence_store.store import EvidenceStore
        from code.identity.resolver import IdentityResolver
        from code.policy.engine import PolicyEngine
        from code.audit_engine.engine import AuditEngine
        store = EvidenceStore(self.dataset_dir)
        store.load_all_evidence()
        resolver = IdentityResolver(self.dataset_dir)
        policy = PolicyEngine(os.path.join(self.dataset_dir, 'deletion_rules.csv'))
        with open(os.path.join(self.dataset_dir, 'sample_deletion_requests.csv'), encoding='utf-8') as f:
            reqs = list(csv.DictReader(f))
        engine = AuditEngine(store, resolver, policy, self.system_activities, reqs)
        r13 = next(r for r in reqs if r['case_id'] == 'case_13')
        res13 = engine.process_request(r13)
        steps13 = json.loads(res13['steps_json'])
        finding_accounts = [s for s in steps13 if s['phase'] == 'FINDING' and s['system_id'] == 'account_service']
        self.assertEqual(len(finding_accounts), 0, "Account verified ABSENT in audit evidence must not be a FINDING")


if __name__ == '__main__':
    unittest.main()

