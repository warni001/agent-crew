// Agent Crew — Azure infrastructure.
//
// Deploys into an existing resource group (see deploy.sh, which creates
// the group first — the Contributor role assignment for the deploy
// service principal needs the group to exist):
//   Key Vault (RBAC, purge protection) — holds the crew's GitHub token,
//     added by deploy.sh after the vault exists. No secrets are committed
//     here or in the template.
//   Two Azure AI Foundry accounts (AIServices, S0), one per model family,
//     because the families do not share a region:
//     - GPT account in the resource-group region: gpt-5.4-mini, Data
//       Zone Standard, version 2026-03-17 (200K TPM Data Zone, verified
//       live Nov 27), plus gpt-6.2-sol, Global Standard, version
//       2026-11-17 (1M TPM pool, verified live Nov 27 — the trial
//       deployment provisioned and answered the same day; the 0 TPM
//       reading from Nov 26-27 is stale). All four crew roles run on
//       Sol; mini stays deployed as the fallback and for mini-chat.
//     - Claude account in eastus2: claude-sonnet-5-5, Global Standard.
//       Verified against the Claude on Foundry starter kit: eastus2 hosts
//       all three Claude families.
//   Container Apps environment + one container app (minReplicas 1) that
//     runs the crew. It starts on a public placeholder image; shipping
//     the crew's own image is the crew's first task (see README).
//   Log Analytics workspace (shared by Container Apps + the Logic App).
//   Storage account (Hot, LRS) holding the run ledger as an append
//     blob, so a deploy restart no longer wipes it. The container
//     app's identity gets Storage Blob Data Contributor on it; the
//     blob URL reaches the app as CREW_LEDGER_BLOB_URL. Cost is
//     fractions of a cent at this scale (kilobytes, a handful of
//     writes per task).
//   Managed identities: the container app's SystemAssigned identity is
//     the crew's runtime identity (Key Vault Secrets User on the vault,
//     Cognitive Services User on both Foundry accounts so models are
//     called with Entra ID, Contributor on the crew resource group).
//     The Logic App's SystemAssigned identity gets a narrow custom role
//     with exactly one permission: delete a resource group.
//
// Every model name and version below was checked against a real source
// (see README for the list). Nothing here is guessed. Quota is the
// likeliest blocker at deploy time: a model deployment fails if the
// subscription has no TPM quota for that model in that region. Pass
// deployModels=false to deploy the infrastructure without the model
// deployments, get quota, then re-run.

targetScope = 'resourceGroup'

@description('Base name for resources; a unique suffix is appended where global uniqueness is required.')
param baseName string = 'agent-crew'

@description('Azure region for the resource group and most resources.')
param location string = resourceGroup().location

@description('Azure region for the Claude Foundry account. eastus2 hosts all three Claude families, so the families live in different regions; the GPT account stays in westus3, where its quota was verified.')
param claudeLocation string = 'eastus2'

@description('Set false to deploy the infrastructure without model deployments (use when TPM quota is not granted yet), then re-run with true.')
param deployModels bool = true

@description('Data Zone Standard capacity (thousands of TPM) for the gpt-5.4-mini deployment.')
param gptMiniCapacity int = 10

@description('Global Standard capacity (thousands of TPM) for the gpt-6.2-sol deployment. 100 fits the 1M TPM pool verified live Nov 27.')
param gptSolCapacity int = 10

@description('Global Standard capacity (thousands of TPM) for the Sonnet deployment.')
param claudeCapacity int = 10

@description('Claude model version. Default 1 matches the Claude on Foundry starter kit. The portal model card lists the hosting option per version: if version 1 lands the Anthropic-hosted variant instead of the Azure-hosted one, redeploy with 2.')
param claudeModelVersion string = '1'

@description('Organization name sent to Anthropic as the marketplace attestation for Claude. Empty means the Claude deployment is skipped (warn-and-skip): the attestation has to name the real organization, and there is no default to invent.')
param claudeOrganizationName string = ''

@description('Country code sent to Anthropic as the marketplace attestation for Claude.')
param claudeCountryCode string = 'US'

@description('Industry sent to Anthropic as the marketplace attestation for Claude.')
param claudeIndustry string = 'technology'

@description('Object ID of the deploy service principal (github-agent-crew-deploy). When set, it gets Storage Blob Data Reader on the ledger storage account, so the deploy workflow can read the run ledger for the post-deploy health watch (scripts/rollback_watch.py). Empty means no assignment is created — grant the role once with az role assignment create instead.')
param deployServicePrincipalId string = ''

var suffix = uniqueString(resourceGroup().id)
var vaultName = 'kv-${replace(baseName, '-', '')}-${take(suffix, 8)}'
var gptAccountName = 'foundry-gpt-${take(suffix, 8)}'
var gptProjectName = 'crew'
// The project endpoint is set on the container app; crew/config.py
// derives the classic Azure OpenAI chat endpoint from it (or from the
// Cognitive Services account endpoint) when AZURE_OPENAI_ENDPOINT is
// unset. Chat calls go to the classic endpoint, not this one — the
// project endpoint returns hollow 200s (proven live Nov 19).
var gptProjectEndpoint = 'https://${gptAccountName}.services.ai.azure.com/api/projects/${gptProjectName}'
var claudeAccountName = 'foundry-claude-${take(suffix, 8)}'
var logAnalyticsName = 'log-${baseName}'
var containerEnvName = 'cae-${baseName}'
var containerAppName = 'ca-${baseName}'
var storageAccountName = 'st${replace(baseName, '-', '')}${take(suffix, 8)}'
var ledgerBlobUrl = 'https://${storageAccountName}.blob.${environment().suffixes.storage}/ledger/crew-ledger.jsonl'

// Built-in role definition IDs.
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

// Key Vault: RBAC authorization, purge protection on. No secrets are
// created by this template; deploy.sh stores the GitHub token after
// the vault exists.
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enabledForDeployment: false
    enabledForTemplateDeployment: false
    enabledForDiskEncryption: false
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// Foundry account for the GPT models. Kind AIServices, SKU S0, shape
// copied from the Claude on Foundry starter kit (same resource family).
resource gptAccount 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' = {
  name: gptAccountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: gptAccountName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

resource gptProject 'Microsoft.CognitiveServices/accounts/projects@2025-10-01-preview' = {
  parent: gptAccount
  name: gptProjectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

// Model name and version verified against the live model list
// (az cognitiveservices model list, westus3, Nov 27): gpt-5.4-mini,
// version 2026-03-17, DataZoneStandard in the SKU list. Mini stays
// deployed as the fallback (and for mini-chat); the crew runs on Sol.
resource miniDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = if (deployModels) {
  parent: gptAccount
  name: 'gpt-5-4-mini'
  sku: {
    name: 'DataZoneStandard'
    capacity: gptMiniCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.4-mini'
      version: '2026-03-17'
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
  dependsOn: [
    gptProject
  ]
}

// gpt-6.2-sol, Global Standard, version 2026-11-17. Verified live
// Nov 27: the Foundry portal shows a 1M TPM shared pool on this
// subscription, and a trial deployment (gpt-6-2-sol-test, capacity 10)
// provisioned and answered the same day. All four crew roles run on
// this deployment (see crew/config.py defaults).
resource solDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = if (deployModels) {
  parent: gptAccount
  name: 'gpt-6-2-sol'
  sku: {
    name: 'GlobalStandard'
    capacity: gptSolCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-6.2-sol'
      version: '2026-11-17'
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
  dependsOn: [
    gptProject
  ]
}

// Foundry account for Claude. Separate account in eastus2 because the
// Claude families are hosted there (starter kit region matrix); the
// GPT account stays in westus3, where its quota was verified.
resource claudeAccount 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' = {
  name: claudeAccountName
  location: claudeLocation
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: claudeAccountName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

resource claudeProject 'Microsoft.CognitiveServices/accounts/projects@2025-10-01-preview' = {
  parent: claudeAccount
  name: 'crew'
  location: claudeLocation
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

// Model ID verified against the Claude on Foundry starter kit and the
// Anthropic Foundry guide: claude-sonnet-5-5, Global Standard only.
// The modelProviderData block is the marketplace attestation; the
// Cognitive Services RP uses it to auto-sign the Anthropic offer, so
// there is no manual click-through. Skipped when the organization name
// is empty — the attestation has to name the real organization.
// Note: Claude requires an Enterprise or MCA-E subscription per the
// Anthropic Commercial Terms linked from the starter kit. On any other
// subscription type this deployment is the most likely to fail; that
// is a subscription-eligibility question, not a template bug.
resource sonnetDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = if (deployModels && !empty(claudeOrganizationName)) {
  parent: claudeAccount
  name: 'claude-sonnet-5-5'
  sku: {
    name: 'GlobalStandard'
    capacity: claudeCapacity
  }
  properties: {
    model: {
      format: 'Anthropic'
      name: 'claude-sonnet-5-5'
      version: claudeModelVersion
    }
    modelProviderData: {
      organizationName: claudeOrganizationName
      countryCode: claudeCountryCode
      industry: claudeIndustry
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [
    claudeProject
  ]
}

// Storage account for the run ledger (append blob). Hot tier, LRS —
// the ledger is kilobytes; there is no reason to pay for anything
// else. Public blob access is off; the crew reaches it with Entra ID.
resource ledgerStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource ledgerContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${ledgerStorage.name}/default/ledger'
  properties: {
    publicAccess: 'None'
  }
}

// Container Apps: the crew's runtime. One always-on replica, no
// ingress (nothing calls into the crew). It starts on a public
// placeholder image — shipping the crew's own image is the crew's
// first task. GHCR public images pull anonymously, so no registry
// credential is needed for the replacement image.
resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource crewApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      // The crew's GitHub token, read from the vault at runtime by the
      // app's own identity (Key Vault Secrets User, granted below).
      // No token value is ever in this template or in the image.
      secrets: [
        {
          name: 'githubtoken'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/GitHubToken'
          identity: 'system'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'crew'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'KEY_VAULT_URL'
              value: vault.properties.vaultUri
            }
            {
              name: 'FOUNDRY_GPT_ENDPOINT'
              value: gptAccount.properties.endpoint
            }
            {
              // The endpoint the crew actually uses: config prefers
              // FOUNDRY_PROJECT_ENDPOINT over the account endpoint.
              name: 'FOUNDRY_PROJECT_ENDPOINT'
              value: gptProjectEndpoint
            }
            {
              // The run ledger lives in this append blob so it
              // survives container restarts and redeploys. The URL
              // is not a secret; unset means the local file.
              name: 'CREW_LEDGER_BLOB_URL'
              value: ledgerBlobUrl
            }
            {
              name: 'FOUNDRY_CLAUDE_ENDPOINT'
              value: claudeAccount.properties.endpoint
            }
            {
              // The crew config reads GITHUB_TOKEN; the gh CLI reads
              // GH_TOKEN. Both come from the same vault secret.
              name: 'GITHUB_TOKEN'
              secretRef: 'githubtoken'
            }
            {
              name: 'GH_TOKEN'
              secretRef: 'githubtoken'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// The container app's SystemAssigned identity is the crew's runtime
// identity. Least privilege: read secrets from the vault, call models
// with Entra ID on both Foundry accounts, manage resources inside the
// crew resource group only.
resource crewVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, crewApp.id, keyVaultSecretsUserRoleId)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: crewApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource crewGptAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(gptAccount.id, crewApp.id, cognitiveServicesUserRoleId)
  scope: gptAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: crewApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource crewClaudeAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(claudeAccount.id, crewApp.id, cognitiveServicesUserRoleId)
  scope: claudeAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
    principalId: crewApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource crewRgAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, crewApp.id, contributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
    principalId: crewApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// The crew writes its run ledger to the append blob with Entra ID.
resource crewLedgerAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ledgerStorage.id, crewApp.id, storageBlobDataContributorRoleId)
  scope: ledgerStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: crewApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// The deploy workflow's post-deploy health watch reads the same ledger
// (scripts/rollback_watch.py). The deploy service principal is created
// by deploy.sh, outside this template, so its object ID arrives as a
// parameter; empty means no assignment is created here.
resource deployLedgerReadAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployServicePrincipalId)) {
  name: guid(ledgerStorage.id, deployServicePrincipalId, storageBlobDataReaderRoleId)
  scope: ledgerStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalId: deployServicePrincipalId
    principalType: 'ServicePrincipal'
  }
}

output keyVaultUrl string = vault.properties.vaultUri
output keyVaultName string = vault.name
output gptAccountName string = gptAccount.name
output gptEndpoint string = gptAccount.properties.endpoint
output gptProjectEndpoint string = gptProjectEndpoint
output claudeAccountName string = claudeAccount.name
output claudeEndpoint string = claudeAccount.properties.endpoint
output containerAppName string = crewApp.name
output ledgerStorageAccountName string = ledgerStorage.name
output ledgerBlobUrl string = ledgerBlobUrl
