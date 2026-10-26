import os
import unittest

from code.evidence_store import EvidenceStore

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(REPO_ROOT, 'dataset')

class TestEvidenceStore(unittest.TestCase):
    """
    Unit test suite verifying EvidenceStore invariants:
    1. Absence by omission
    2. Incomplete snapshot scope enforcement
    3. Replica drift detection
    4. Future-dated evidence cutoff enforcement
    """

    @classmethod
    def setUpClass(cls):
        cls.store = EvidenceStore(DATASET_DIR)
        cls.report = cls.store.load_all_evidence()

    def test_absence_by_omission(self):
        """
        Confirms that when a record is absent from a covering snapshot valid at audit_at,
        the covering snapshot is correctly identified as proof of absence.
        """
        # Case 01 cutoff: 2026-09-03T11:30:00Z, store: account_service
        cutoff = "2026-09-03T11:30:00Z"
        covering_snap = self.store.get_covering_snapshot_for_absence('account_service', cutoff)

        self.assertIsNotNone(covering_snap)
        # Should be evidence_54 (2026-09-02T23:00:00Z)
        self.assertEqual(covering_snap.evidence_id, 'evidence_54')
        self.assertIn('account_service', covering_snap.covered_systems)

        # Check that account_001 is omitted from evidence_54 observations
        obs_account_001 = [
            o for o in self.store.by_resource.get('account_001', [])
            if o.evidence_id == 'evidence_54'
        ]
        self.assertEqual(obs_account_001, [], "account_001 must be omitted from evidence_54")

    def test_incomplete_snapshots(self):
        """
        Verifies that partial-scope snapshots (such as evidence_90 covering only account_service)
        cannot be used to prove absence for un-covered systems.
        """
        snap_90 = next((s for s in self.store.covering_snapshots if s.evidence_id == 'evidence_90'), None)
        self.assertIsNotNone(snap_90)
        self.assertFalse(snap_90.is_full_store_snapshot)
        self.assertEqual(snap_90.covered_systems, ['account_service'])

        # Attempt to retrieve a covering snapshot for search_service at cutoff 2026-09-06T00:00:00Z
        # evidence_90 must NOT qualify for search_service
        cutoff = "2026-09-06T00:00:00Z"
        search_snap = self.store.get_covering_snapshot_for_absence('search_service', cutoff, require_full_store=True)
        if search_snap:
            self.assertNotEqual(search_snap.evidence_id, 'evidence_90', "evidence_90 cannot cover search_service")

    def test_replica_drift_detection(self):
        """
        Verifies that records deleted in primary but still lingering in failover/read replicas
        are flagged as active replica observations (replica drift).
        """
        obs_5 = self.store.by_resource.get('account_005', [])
        self.assertTrue(len(obs_5) > 0, "account_005 must be indexed")

        failover_obs = next((o for o in obs_5 if o.replica_type == 'failover_replica'), None)
        self.assertIsNotNone(failover_obs, "account_005 must be observed in failover replica")
        self.assertEqual(failover_obs.state, 'PRESENT')
        self.assertTrue(failover_obs.is_replica)
        self.assertEqual(failover_obs.evidence_id, 'evidence_54')

    def test_future_dated_evidence_cutoff(self):
        """
        Verifies that observations or snapshots captured after audit_at are strictly excluded.
        """
        cutoff_august = "2026-08-15T00:00:00Z"
        user_obs = self.store.get_observations_for_user(
            user_id='user_05',
            emails=['leila.haddad@yahoo.com'],
            audit_at=cutoff_august
        )

        for o in user_obs:
            self.assertLessEqual(o.observed_at, cutoff_august, f"Observation {o.locator} exceeds audit_at cutoff")

        # Snapshot evidence_54 was captured on 2026-09-02; it must not be valid for an August cutoff
        snap_august = self.store.get_covering_snapshot_for_absence('account_service', cutoff_august)
        if snap_august:
            self.assertLessEqual(snap_august.captured_at, cutoff_august)
            self.assertNotEqual(snap_august.evidence_id, 'evidence_54')

if __name__ == '__main__':
    unittest.main()
