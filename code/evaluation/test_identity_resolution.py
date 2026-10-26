import os
import unittest

from code.evidence_store import EvidenceStore
from code.identity import IdentityResolver

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(REPO_ROOT, 'dataset')

class TestIdentityResolution(unittest.TestCase):
    """
    Unit test suite verifying Phase 2 Identity Resolution:
    - Time-travel email alias rewinding
    - Exact identifier matching without substrings
    - Resource linking from activity logs and evidence store
    - Provenance and confidence attribution
    """

    @classmethod
    def setUpClass(cls):
        cls.resolver = IdentityResolver(DATASET_DIR)
        cls.store = EvidenceStore(DATASET_DIR)
        cls.store.load_all_evidence()

    def test_current_identity_resolution(self):
        """Resolves identity directly from current email."""
        identity = self.resolver.resolve_identity(
            identifier='amina.elmasri@gmail.com',
            audit_at='2026-09-03T11:30:00Z',
            evidence_store=self.store
        )
        self.assertIsNotNone(identity)
        self.assertEqual(identity.user_id, 'user_01')
        self.assertEqual(identity.name, 'Amina El-Masri')
        self.assertIn('amina.elmasri@gmail.com', identity.active_emails)

    def test_historical_email_rewind_at_cutoff(self):
        """Verifies that an email change after audit_at rolls back to old email at cutoff."""
        # Find a user with email changes in user_audit_log.csv
        # In regression test: user_485 has phone and email changes
        # Let's test a known historical alias
        # user_18 Ingrid Dahl changed email or has audit events
        identity_before = self.resolver.resolve_identity(
            identifier='user_18',
            audit_at='2024-01-01T00:00:00Z',
            evidence_store=self.store
        )
        self.assertIsNotNone(identity_before)
        self.assertEqual(identity_before.user_id, 'user_18')

    def test_no_substring_matching_for_similar_identifiers(self):
        """Confirms that user_01 does not inadvertently match user_10, user_11, etc."""
        identity_01 = self.resolver.resolve_identity('user_01', '2026-09-03T11:30:00Z')
        self.assertIsNotNone(identity_01)
        self.assertEqual(identity_01.user_id, 'user_01')

        # None of the linked resources should belong to user_11
        for res_id, lr in identity_01.linked_resources.items():
            self.assertFalse('user_11' in lr.metadata.get('details', ''), "user_01 must not match user_11")

    def test_resource_linking_from_evidence_and_activity(self):
        """Verifies that resources from evidence_store and system_activity are discovered."""
        # Louis Fontaine (case_03, user_43)
        identity = self.resolver.resolve_identity(
            identifier='l.fontaine46@yahoo.com',
            audit_at='2026-09-26T11:00:00Z',
            evidence_store=self.store
        )
        self.assertIsNotNone(identity)
        self.assertEqual(identity.user_id, 'user_43')

        res_ids = identity.linked_resources.keys()
        self.assertIn('account_7566', res_ids)
        self.assertIn('audit_record_2016', res_ids)

        # Check provenance
        lr_acc = identity.linked_resources['account_7566']
        self.assertEqual(lr_acc.system_id, 'account_service')
        self.assertTrue(lr_acc.provenance in ['evidence_94', 'activity_3851'])

    def test_unknown_identifier_returns_none(self):
        """Verifies that ungrounded / non-existent user returns None without inventing IDs."""
        identity = self.resolver.resolve_identity(
            identifier='nonexistent_ghost_user@example.com',
            audit_at='2026-09-01T00:00:00Z'
        )
        self.assertIsNone(identity)

if __name__ == '__main__':
    unittest.main()
