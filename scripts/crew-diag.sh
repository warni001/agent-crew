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
az monitor metrics list --resource /subscriptions/2b5dd0c2-bac8-45dc-bc60-6d21c1362cf8/resourceGroups/rg-agent-crew/providers/Microsoft.CognitiveServices/accounts/foundry-gpt-vaxvu3c2 --metric ProcessedPromptTokens GeneratedTokens --interval PT1H --aggregation Total -o table
