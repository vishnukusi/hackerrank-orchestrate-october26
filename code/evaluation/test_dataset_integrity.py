import unittest
import os
import hashlib
import json
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(REPO_ROOT, 'dataset')
CHECKSUM_FILE = os.path.join(REPO_ROOT, 'code', 'evaluation', 'dataset_checksums.json')

class TestDatasetIntegrity(unittest.TestCase):
    """
    Ensures that participant-facing files under dataset/ remain unmodified.
    Protects challenge inputs and canonical samples from unintended tampering.
    """

    def setUp(self):
        self.assertTrue(os.path.exists(DATASET_DIR), f"Dataset directory missing: {DATASET_DIR}")
        self.assertTrue(os.path.exists(CHECKSUM_FILE), f"Checksum manifest missing: {CHECKSUM_FILE}")
        with open(CHECKSUM_FILE, 'r', encoding='utf-8') as f:
            self.expected_checksums = json.load(f)

    def test_all_dataset_files_present_and_unmodified(self):
        actual_files = set()
        mismatched_hashes = []
        missing_files = []

        for rel_path, expected_hash in self.expected_checksums.items():
            abs_path = os.path.join(DATASET_DIR, rel_path.replace('/', os.sep))
            if not os.path.exists(abs_path):
                missing_files.append(rel_path)
                continue

            actual_files.add(rel_path)
            h = hashlib.sha256()
            with open(abs_path, 'rb') as fp:
                while chunk := fp.read(65536):
                    h.update(chunk)
            actual_hash = h.hexdigest()
            if actual_hash != expected_hash:
                mismatched_hashes.append((rel_path, expected_hash, actual_hash))

        self.assertEqual(missing_files, [], f"Missing dataset files: {missing_files}")
        self.assertEqual(mismatched_hashes, [], f"Mismatched sha256 checksums: {mismatched_hashes}")

        # Check for unexpected extra files
        current_files = set()
        for root, dirs, files in os.walk(DATASET_DIR):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), DATASET_DIR).replace('\\', '/')
                current_files.add(rel)

        extra_files = current_files - set(self.expected_checksums.keys())
        self.assertEqual(extra_files, set(), f"Unexpected extra files in dataset: {extra_files}")
        self.assertEqual(len(current_files), 103, f"Expected 103 dataset files, found {len(current_files)}")

    def test_git_dataset_clean(self):
        """Verifies that git working tree reports no modifications in dataset/."""
        if not os.path.exists(os.path.join(REPO_ROOT, '.git')):
            self.skipTest("Not running inside repository root with .git; skipping git status test")
        try:
            res = subprocess.run(
                ['git', 'status', '--porcelain', 'dataset/'],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True
            )
            self.assertEqual(res.stdout.strip(), "", f"Git detected uncommitted changes in dataset/: {res.stdout}")
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.skipTest("git command not found or not a git repository; skipping git status test")

if __name__ == '__main__':
    unittest.main()
