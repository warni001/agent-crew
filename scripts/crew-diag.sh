#!/bin/bash
# Crew health diagnostics. Run after the v2 push + deploy.
# Usage: bash scripts/crew-diag.sh
echo "===== container revisions ====="
az containerapp revision list -g rg-agent-crew -n ca-agent-crew --query "[].{name:name, active:properties.active, created:properties.createdTime}" -o table
echo
echo "===== latest logs, raw (last 30 lines, unfiltered) ====="
az containerapp logs show -g rg-agent-crew -n ca-agent-crew --tail 30
echo
echo "===== ledger failures in last 200 lines (want 0) ====="
az containerapp logs show -g rg-agent-crew -n ca-agent-crew --tail 200 | grep -c "Progress ledger attempt failed" || true
echo
echo "===== task progress (last 10) ====="
az containerapp logs show -g rg-agent-crew -n ca-agent-crew --tail 200 | grep -E "task (started|finished|stalled)|no task queued" | tail -10
echo
echo "===== open PRs ====="
gh pr list -R warni001/agent-crew --state open
echo
echo "===== open crew-task issues ====="
gh issue list -R warni001/agent-crew --label crew-task --state open
echo
echo "===== token usage, Foundry account (last 24h) ====="
SUB="$(az account show --query id -o tsv)"
GPT_ACCT="$(az cognitiveservices account list -g rg-agent-crew --query "[?contains(name, 'foundry-gpt')].name | [0]" -o tsv)"
az monitor metrics list --resource "/subscriptions/$SUB/resourceGroups/rg-agent-crew/providers/Microsoft.CognitiveServices/accounts/$GPT_ACCT" --metric ProcessedPromptTokens GeneratedTokens --interval PT1H --aggregation Total -o table
