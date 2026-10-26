import os
import unittest

from code.evidence_store.parsers import (
    SqlSnapshotParser,
    JsonBundleParser,
    LogEvidenceParser,
    TextHandoverParser,
    PdfDocumentParser,
    ImageOcrParser
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(REPO_ROOT, 'dataset')

class TestEvidenceParsers(unittest.TestCase):
    """
    Unit test suite verifying all 6 unified evidence format parsers.
    """

    def test_sql_snapshot_parser_with_replicas(self):
        parser = SqlSnapshotParser()
        entry = {
            'file_id': 'evidence_54',
            'file_path': 'evidence/databases/customer_accounts_failover_replica_20260902.sql',
            'captured_at': '2026-09-02T23:00:00Z'
        }
        abs_path = os.path.join(DATASET_DIR, entry['file_path'])
        obs, snap, report = parser.parse(abs_path, entry)

        self.assertEqual(report.status, 'SUCCESS')
        self.assertGreater(len(obs), 0)
        self.assertIsNotNone(snap)
        self.assertIn('account_service', snap.covered_systems)
        self.assertIn('failover_replica', snap.replica_coverage)

        # Confirm replica drift observation is present
        drift_obs = next((o for o in obs if o.resource_id == 'account_005'), None)
        self.assertIsNotNone(drift_obs, "account_005 should be observed in failover replica")
        self.assertTrue(drift_obs.is_replica)
        self.assertEqual(drift_obs.replica_type, 'failover_replica')
        self.assertEqual(drift_obs.state, 'PRESENT')

    def test_json_bundle_parser_embedded_timestamps(self):
        parser = JsonBundleParser()
        entry = {
            'file_id': 'evidence_51',
            'file_path': 'evidence/records/customer_data_snapshot_bundle_20260915.json',
            'captured_at': '2026-09-15T12:00:00Z'
        }
        abs_path = os.path.join(DATASET_DIR, entry['file_path'])
        obs, snap, report = parser.parse(abs_path, entry)

        self.assertEqual(report.status, 'SUCCESS')
        self.assertGreater(len(obs), 0)
        self.assertIsNotNone(snap)
        self.assertIn('identity_service', snap.covered_systems)
        self.assertIn('security_service', snap.covered_systems)
        self.assertIn('account_service', snap.covered_systems)

        # Check tombstone_440 observation
        tomb_obs = next((o for o in obs if o.resource_id == 'tombstone_440'), None)
        self.assertIsNotNone(tomb_obs)
        self.assertEqual(tomb_obs.system_id, 'identity_service')
        self.assertEqual(tomb_obs.state, 'REDACTED')
        self.assertEqual(tomb_obs.observed_at, '2026-09-15T12:00:00Z')

    def test_log_parser_structured_and_unstructured(self):
        parser = LogEvidenceParser()
        entry = {
            'file_id': 'evidence_01',
            'file_path': 'evidence/logs/service_events_ap_south_20260915.log',
            'captured_at': '2026-09-15T09:00:00Z'
        }
        abs_path = os.path.join(DATASET_DIR, entry['file_path'])
        obs, snap, report = parser.parse(abs_path, entry)

        self.assertEqual(report.status, 'SUCCESS')
        self.assertGreater(len(obs), 0)
        self.assertIsNone(snap, "Logs should not be registered as full store snapshots")

        # Every observation must have valid system and locator
        for o in obs[:10]:
            self.assertIsNotNone(o.system_id)
            self.assertTrue(o.locator.startswith("line:"))
            self.assertEqual(o.validation_status, "VALID")

    def test_text_handover_parser(self):
        parser = TextHandoverParser()
        entry = {
            'file_id': 'evidence_03',
            'file_path': 'evidence/records/customer_account_handover_20261001.txt',
            'captured_at': '2026-10-01T12:00:00Z'
        }
        abs_path = os.path.join(DATASET_DIR, entry['file_path'])
        obs, snap, report = parser.parse(abs_path, entry)

        self.assertEqual(report.status, 'SUCCESS')
        self.assertEqual(len(obs), 41)

        # Check specific entry: account_3677
        tanaka_obs = next((o for o in obs if o.resource_id == 'account_3677'), None)
        self.assertIsNotNone(tanaka_obs)
        self.assertEqual(tanaka_obs.system_id, 'account_service')
        self.assertEqual(tanaka_obs.state, 'ABSENT')
        self.assertIn('y.tanaka32@gmail.com', tanaka_obs.identifiers)

    def test_pdf_document_parser(self):
        parser = PdfDocumentParser()
        entry = {
            'file_id': 'evidence_18',
            'file_path': 'evidence/documents/account_history_report_20260718.pdf',
            'captured_at': '2026-07-18T10:00:00Z'
        }
        abs_path = os.path.join(DATASET_DIR, entry['file_path'])
        obs, snap, report = parser.parse(abs_path, entry)

        self.assertEqual(report.status, 'SUCCESS')
        self.assertGreater(len(obs), 0)
        user18_obs = next((o for o in obs if o.user_id == 'user_18'), None)
        self.assertIsNotNone(user18_obs)
        self.assertIn('ingrid.dahl@outlook.com', user18_obs.identifiers)

    def test_image_ocr_parser(self):
        parser = ImageOcrParser()
        entry = {
            'file_id': 'evidence_04',
            'file_path': 'evidence/images/account_admin_console_20260926.png',
            'captured_at': '2026-09-26T12:00:00Z'
        }
        abs_path = os.path.join(DATASET_DIR, entry['file_path'])
        obs, snap, report = parser.parse(abs_path, entry)

        self.assertEqual(report.status, 'SUCCESS')
        self.assertGreater(len(obs), 0)
        account_obs = next((o for o in obs if o.resource_id == 'account_5762'), None)
        self.assertIsNotNone(account_obs)
        self.assertEqual(account_obs.system_id, 'account_service')
        self.assertIn('haruka.s1838@zoho.com', account_obs.identifiers)
        self.assertGreaterEqual(account_obs.parser_confidence, 0.9)

    def test_support_and_backup_storage_service_mapping(self):
        """Verifies raw_system preservation, inner record system mapping, and recorded_at timestamp extraction."""
        parser = JsonBundleParser()
        entry = {
            'file_id': 'evidence_31',
            'file_path': 'evidence/records/customer_data_snapshot_bundle_20260511.json',
            'captured_at': '2026-05-11T11:00:00Z'
        }
        abs_path = os.path.join(DATASET_DIR, entry['file_path'])
        obs, snap, report = parser.parse(abs_path, entry)

        self.assertEqual(report.status, 'SUCCESS')
        storage_obs = [o for o in obs if o.raw_system == 'support_storage_service']
        self.assertGreater(len(storage_obs), 0)

        # Check search_9252 record from operational_record_history
        search_obs = next((o for o in storage_obs if o.resource_id == 'search_9252'), None)
        self.assertIsNotNone(search_obs)
        self.assertEqual(search_obs.raw_system, 'support_storage_service')
        self.assertEqual(search_obs.system_id, 'search_service')
        self.assertEqual(search_obs.observed_at, '2026-05-04T07:00:00Z')  # from recorded_at, not exported_at!
        self.assertEqual(search_obs.state, 'PRESENT')  # from result == 'FOUND'

        # Check NOT_FOUND record
        absent_obs = next((o for o in storage_obs if o.resource_id == 'account_1015'), None)
        self.assertIsNotNone(absent_obs)
        self.assertEqual(absent_obs.raw_system, 'support_storage_service')
        self.assertEqual(absent_obs.system_id, 'account_service')
        self.assertEqual(absent_obs.state, 'ABSENT')  # from result == 'NOT_FOUND'

    def test_image_ocr_normalization_and_user_spacing(self):
        """Verifies OCR normalization for space-separated user IDs, marketing IDs, and email domain typos."""
        parser = ImageOcrParser()

        # 1. Spaced user ID: 'user 17' in deleted_account_reference_20260310.png
        entry_42 = {
            'file_id': 'evidence_42',
            'file_path': 'evidence/images/deleted_account_reference_20260310.png',
            'captured_at': '2026-03-10T08:00:00Z'
        }
        obs_42, _, rep_42 = parser.parse(os.path.join(DATASET_DIR, entry_42['file_path']), entry_42)
        self.assertEqual(rep_42.status, 'SUCCESS')
        user17_obs = next((o for o in obs_42 if o.user_id == 'user_17'), None)
        self.assertIsNotNone(user17_obs, "user_17 must be extracted from 'user 17' in evidence_42")
        self.assertEqual(user17_obs.system_id, 'identity_service')

        # 2. Marketing resource ID in marketing_profile_console.png
        entry_60 = {
            'file_id': 'evidence_60',
            'file_path': 'evidence/images/marketing_profile_console.png',
            'captured_at': '2026-09-15T23:45:00Z'
        }
        obs_60, _, rep_60 = parser.parse(os.path.join(DATASET_DIR, entry_60['file_path']), entry_60)
        self.assertEqual(rep_60.status, 'SUCCESS')
        mktg_obs = next((o for o in obs_60 if o.resource_id == 'marketing_001'), None)
        self.assertIsNotNone(mktg_obs, "marketing_001 must be extracted from evidence_60")
        self.assertEqual(mktg_obs.system_id, 'account_service')

        # 3. Domain typo: 'areed1433@gmaii.com' -> 'areed1433@gmail.com' in backup_restore_console_20260429.png
        entry_32 = {
            'file_id': 'evidence_32',
            'file_path': 'evidence/images/backup_restore_console_20260429.png',
            'captured_at': '2026-04-29T11:00:00Z'
        }
        obs_32, _, rep_32 = parser.parse(os.path.join(DATASET_DIR, entry_32['file_path']), entry_32)
        self.assertEqual(rep_32.status, 'SUCCESS')
        reed_obs = next((o for o in obs_32 if 'areed1433@gmail.com' in o.identifiers), None)
        self.assertIsNotNone(reed_obs, "areed1433@gmail.com must be normalized from areed1433@gmaii.com")

if __name__ == '__main__':
    unittest.main()

