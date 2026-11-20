#!/bin/bash
# crew-spend.sh — crew spend from the blob ledger (live, per-call pricing).
# Needs Storage Blob Data Reader on the ledger storage account (granted Nov 20).
# If login auth fails, the role may still be propagating — wait a few minutes and retry.
set -euo pipefail
RG=rg-agent-crew
ACCT=$(az storage account list -g "$RG" --query "[0].name" -o tsv)
az storage blob download --account-name "$ACCT" --container-name ledger --name crew-ledger.jsonl --file /tmp/crew-ledger.jsonl --auth-mode login --overwrite -o none
python3 - <<'PY'
import json
spend = None
for line in open('/tmp/crew-ledger.jsonl'):
    line = line.strip()
    if not line:
        continue
    e = json.loads(line)
    if e.get('event') == 'spend':
        spend = e
if spend:
    usd = spend['usd']
    print(f"spent: ${usd:.2f} (as of {spend['ts']})")
    print(f"guard: $190.00, remaining: ${190 - usd:.2f}")
else:
    print('no spend entries in ledger')
PY
