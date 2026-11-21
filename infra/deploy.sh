#!/usr/bin/env bash
# Deploy the Agent Crew Azure resources with Bicep.
#
# Prereqs:
#   - Azure CLI installed and signed in:  az login
#   - Bicep: the script installs it via `az bicep install` if missing.
#   - GitHub CLI (`gh`), authenticated, to set the AZURE_DEPLOY_CREDENTIALS
#     secret automatically. Optional — without it, the script prints the
#     credential JSON for manual copy.
#   - Run on your Mac (where `az` lives), not on the assistant VM.
#
# What it does:
#   1. Creates the resource group (before the deploy SP step — the
#      Contributor role assignment needs the resource group to exist).
#   2. Ensures the deploy service principal `github-agent-crew-deploy`
#      exists with Contributor on the resource group (idempotent). The
#      credential JSON is set as the AZURE_DEPLOY_CREDENTIALS secret in
#      the agent-crew repo via `gh secret set`. If gh is missing, the JSON
#      is printed for manual copy. It is shown only once. Do not store it
#      anywhere else (not in the Key Vault — the crew's managed identity
#      could read it and gain a deployment credential).
#   3. Warn-only quota pre-check for the three models (quota is the
#      likeliest deploy blocker; a zero quota fails the model deployments,
#      not the rest of the template).
#   4. Deploys infra/main.bicep with infra/main.bicepparam (two Foundry
#      accounts, Container Apps, Key Vault, Log Analytics).
#   5. Stores the crew's GitHub fine-grained token in the Key Vault as
#      GitHubToken — from GITHUB_CREW_TOKEN in the environment (or a
#      repo-root .env, never committed). Warns and skips when the token
#      is not supplied; the crew cannot reach GitHub until it is stored.
#
# Re-running is safe: the deployment is incremental. It WILL reset the
# deploy SP credential each run and update the repo secret to match.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-agent-crew}"
LOCATION="${LOCATION:-westus3}"
CLAUDE_LOCATION="${CLAUDE_LOCATION:-eastus2}"
BASE_NAME="${BASE_NAME:-agent-crew}"
DEPLOY_SP_NAME="${DEPLOY_SP_NAME:-github-agent-crew-deploy}"
GITHUB_REPO="${GITHUB_REPO:-warni001/agent-crew}"

cd "$(dirname "$0")"

command -v az >/dev/null || { echo "Azure CLI not found. Install: brew install azure-cli"; exit 1; }
az account show >/dev/null 2>&1 || { echo "Not signed in. Run: az login"; exit 1; }
az bicep version >/dev/null 2>&1 || az bicep install

echo "Subscription: $(az account show --query '{name:name, id:id}' -o tsv)"

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
echo "Tenant: $TENANT_ID"

# Local .env (repo root, never committed): CLAUDE_ORGANIZATION_NAME,
# CLAUDE_COUNTRY_CODE, CLAUDE_INDUSTRY, BUDGET_NOTIFY_EMAIL,
# GITHUB_CREW_TOKEN. The bicepparam file reads the Claude values from
# the environment.
if [ -f ../.env ]; then
    set -a
    # shellcheck disable=SC1091
    source ../.env
    set +a
fi

echo "Creating resource group $RESOURCE_GROUP in $LOCATION ..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none

echo "Ensuring deploy service principal '$DEPLOY_SP_NAME' ..."
DEPLOY_SP_APP_ID="$(az ad sp list --display-name "$DEPLOY_SP_NAME" --query "[0].appId" -o tsv)"
RG_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
if [ -n "$DEPLOY_SP_APP_ID" ]; then
  echo "Deploy SP '$DEPLOY_SP_NAME' already exists (appId $DEPLOY_SP_APP_ID); resetting its credential..."
  DEPLOY_SP_PASSWORD="$(az ad sp credential reset --id "$DEPLOY_SP_APP_ID" --append --query password -o tsv)"
  [ -n "$DEPLOY_SP_PASSWORD" ] || { echo "Failed to reset deploy SP credential."; exit 1; }
  CREDENTIAL_JSON="$(python3 -c 'import json,sys; print(json.dumps({"clientId": sys.argv[1], "clientSecret": sys.argv[2], "subscriptionId": sys.argv[3], "tenantId": sys.argv[4], "activeDirectoryEndpointUrl": "https://login.microsoftonline.com/", "resourceManagerEndpointUrl": "https://management.azure.com/", "activeDirectoryGraphResourceId": "https://graph.windows.net/", "sqlManagementEndpointUrl": "https://management.core.windows.net:8443/", "galleryEndpointUrl": "https://gallery.azure.com/", "managementEndpointUrl": "https://management.core.windows.net/"}, indent=2))' "$DEPLOY_SP_APP_ID" "$DEPLOY_SP_PASSWORD" "$SUBSCRIPTION_ID" "$TENANT_ID")"
  unset DEPLOY_SP_PASSWORD
else
  echo "Creating deploy SP '$DEPLOY_SP_NAME' with Contributor on $RESOURCE_GROUP ..."
  CREDENTIAL_JSON="$(az ad sp create-for-rbac --name "$DEPLOY_SP_NAME" --role Contributor --scopes "$RG_SCOPE" --sdk-auth)"
fi

# Set the AZURE_DEPLOY_CREDENTIALS secret in the repo via gh. Falls back
# to printing the JSON for manual copy if gh is missing or not authed.
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  echo "$CREDENTIAL_JSON" | gh secret set AZURE_DEPLOY_CREDENTIALS --repo "$GITHUB_REPO"
  echo "Set AZURE_DEPLOY_CREDENTIALS in $GITHUB_REPO."
else
  echo "gh CLI not found or not authenticated. Save the JSON below as the AZURE_DEPLOY_CREDENTIALS secret in $GITHUB_REPO:"
  echo "$CREDENTIAL_JSON"
  echo "It is shown only once. Do not store it anywhere else."
fi
unset CREDENTIAL_JSON

# Ensure the Contributor role assignment exists (idempotent — the
# create-for-rbac above sets it on a fresh creation; this covers the
# already-exists path and any drift).
if [ -n "$DEPLOY_SP_APP_ID" ]; then
  if [ -z "$(az role assignment list --assignee "$DEPLOY_SP_APP_ID" --role Contributor --scope "$RG_SCOPE" --query "[0].id" -o tsv)" ]; then
    az role assignment create --assignee "$DEPLOY_SP_APP_ID" --role Contributor --scope "$RG_SCOPE" -o none
    echo "Granted Contributor on $RESOURCE_GROUP to '$DEPLOY_SP_NAME'."
  else
    echo "Contributor on $RESOURCE_GROUP already assigned to '$DEPLOY_SP_NAME'."
  fi
fi

# Quota pre-check (warn-only): a zero TPM quota fails the model
# deployments. The check prints what the subscription has for the
# three models in the two regions; if a model is missing, request
# quota in the portal (Foundry -> Quotas) or re-run with
# deployModels=false and add the models later.
# NOTE: -o tsv drops the model names (they render as None), so the
# query pulls name.value out explicitly.
echo "Quota pre-check (warn-only)..."
for LOC in "$LOCATION" "$CLAUDE_LOCATION"; do
  echo "--- $LOC ---"
  OUT="$(az cognitiveservices usage list --location "$LOC" \
    --query "[?contains(name.value, 'gpt-6.2') || contains(name.value, 'claude-sonnet-5-5')].{model:name.value, used:currentValue, limit:limit}" \
    -o table 2>/dev/null || true)"
  if [ -n "$OUT" ]; then
    echo "$OUT"
  else
    echo "  (no quota listed for gpt-6.2 / claude-sonnet-5-5 in $LOC — check the portal)"
  fi
done

# Claude attestation: the deployment is skipped when the organization
# name is empty. Say so plainly instead of failing later.
if [ -z "${CLAUDE_ORGANIZATION_NAME:-}" ]; then
  echo "warning: CLAUDE_ORGANIZATION_NAME not set; the Claude deployment will be skipped (set it in .env and re-run to add Claude)" >&2
fi

echo "Deploying infra/main.bicep ..."
RESULT="$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file main.bicep \
  --parameters main.bicepparam \
  --query properties.outputs -o json)"

KEY_VAULT_NAME="$(echo "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["keyVaultName"]["value"])')"

# GitHub token: the crew's fine-grained PAT, stored as GitHubToken.
# Skips when the vault already has it; warns and skips when no token
# was supplied. The token value is never printed.
# The vault is RBAC-only and the template gives the crew read access
# only, so the deployer needs Key Vault Secrets Officer to store the
# token (Nov 19: the first deploy failed ForbiddenByRbac here until
# the role was granted manually). Grant it idempotently first.
if az keyvault secret show --vault-name "$KEY_VAULT_NAME" --name GitHubToken >/dev/null 2>&1; then
  echo "GitHubToken already in Key Vault $KEY_VAULT_NAME; skipping."
elif [ -n "${GITHUB_CREW_TOKEN:-}" ]; then
  DEPLOYER_OID="$(az ad signed-in-user show --query id -o tsv)"
  VAULT_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.KeyVault/vaults/$KEY_VAULT_NAME"
  if [ -z "$(az role assignment list --assignee "$DEPLOYER_OID" --role "Key Vault Secrets Officer" --scope "$VAULT_SCOPE" --query "[0].id" -o tsv)" ]; then
    az role assignment create --assignee "$DEPLOYER_OID" --role "Key Vault Secrets Officer" --scope "$VAULT_SCOPE" -o none
    echo "Granted Key Vault Secrets Officer on $KEY_VAULT_NAME to the deployer; waiting 45s for propagation..."
    sleep 45
  fi
  az keyvault secret set --vault-name "$KEY_VAULT_NAME" --name GitHubToken --value "$GITHUB_CREW_TOKEN" -o none
  unset GITHUB_CREW_TOKEN
  echo "Stored GitHubToken in Key Vault $KEY_VAULT_NAME."
else
  echo "warning: GITHUB_CREW_TOKEN not set and GitHubToken is not in the vault; the crew cannot reach GitHub until it is stored." >&2
  echo "  Create the fine-grained token in the GitHub web UI (Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens), scoped to agent-crew only, then run:" >&2
  echo "    az keyvault secret set --vault-name $KEY_VAULT_NAME --name GitHubToken --value \"\$TOKEN\" -o none" >&2
fi

echo
echo "Done. Key outputs:"
echo "$RESULT" | python3 -c 'import json,sys; [print("  %s: %s" % (k, v["value"])) for k, v in json.load(sys.stdin).items()]'
echo
echo "Next steps:"
echo "  1. If the Claude deployment was skipped, set CLAUDE_ORGANIZATION_NAME"
echo "     in .env and re-run this script. Claude also needs an Enterprise"
echo "     or MCA-E subscription per the Anthropic terms; on any other"
echo "     subscription type the Claude deployment is the most likely to"
echo "     fail — that is an eligibility question, not a template bug."
echo "  2. If a model deployment failed on quota, request TPM quota in the"
echo "     portal (Foundry -> Quotas) for that model in its region, then"
echo "     re-run this script."
echo "  3. The container app runs a placeholder image until the crew ships"
echo "     its own image to GHCR and updates the app. Deploy the sha tag,"
echo "     never :latest (an unchanged :latest string mints no new"
echo "     revision, and a :latest pull can resolve stale):"
echo "       az containerapp update -g $RESOURCE_GROUP -n <app> --image ghcr.io/warni001/agent-crew:sha-<commit-sha> --revision-suffix <suffix>"
echo "  4. Manual kill (there is no automatic kill — the kill switch was"
echo "     removed Nov 21, 2026; you delete the group by hand when done):"
echo "       az group delete --name $RESOURCE_GROUP --yes --no-wait"
echo "  5. AZURE_DEPLOY_CREDENTIALS is set in $GITHUB_REPO by this script"
echo "     (or printed above for manual copy if gh was unavailable)."
