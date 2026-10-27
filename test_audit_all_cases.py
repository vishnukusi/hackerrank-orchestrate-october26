import csv
import json
import os
import glob
import re
import sqlite3

# Load dataset
with open('dataset/deletion_requests.csv', encoding='utf-8') as f:
    requests = list(csv.DictReader(f))

with open('dataset/user_identities.csv', encoding='utf-8') as f:
    identities = list(csv.DictReader(f))

with open('dataset/user_audit_log.csv', encoding='utf-8') as f:
    audit_logs = list(csv.DictReader(f))

with open('dataset/evidence_files.csv', encoding='utf-8') as f:
    evidence_manifest = {r['file_id']: r for r in csv.DictReader(f)}

if os.path.exists('output.csv'):
    with open('output.csv', encoding='utf-8') as f:
        output_rows = {r['case_id']: r for r in csv.DictReader(f)}
else:
    output_rows = {}

print(f"Loaded {len(output_rows)} output rows for audit.")
