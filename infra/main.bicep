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
//     - GPT account in the resource-group region: gpt-6.2-astra and
//       gpt-6.2-sol, Global Standard. Verified against the Foundry region
//       table: both are available in westus3, not in eastus2.
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
//   Kill switch (the spend cutoff): a $200 Cost Management budget on the
//     crew resource group, an Action Group, and a Logic App that deletes
//     the crew resource group. The budget fires the Logic App at 95%
//     (~$190); a Recurrence trigger fires it at the hard deadline,
//     Sat Nov 21 5pm ET, whether or not the budget fired. Shutdown is a
//     delete, not a pause.
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

@description('Azure region for the Claude Foundry account. eastus2 hosts all three Claude families; GPT-6.2 is not available there, so the families live in different regions.')
param claudeLocation string = 'eastus2'

@description('Set false to deploy the infrastructure without model deployments (use when TPM quota is not granted yet), then re-run with true.')
param deployModels bool = true

@description('Global Standard capacity (thousands of TPM) for the Astra deployment.')
param gptAstraCapacity int = 10

@description('Global Standard capacity (thousands of TPM) for the Sol deployment.')
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

@description('Email address for the 50% and 90% budget alerts. Override at deploy time; the 95% alert also fires the Action Group.')
param budgetNotifyEmail string = 'john.warnicke@gmail.com'

@description('Object ID of the deploy service principal (github-agent-crew-deploy). When set, it gets Storage Blob Data Reader on the ledger storage account, so the deploy workflow can read the run ledger for the post-deploy health watch (scripts/rollback_watch.py). Empty means no assignment is created — grant the role once with az role assignment create instead.')
param deployServicePrincipalId string = ''

@description('Monthly budget amount in USD for the crew resource group. The kill switch fires at 95% of this amount.')
param budgetAmount int = 200

@description('Hard shutdown time (UTC) for the Recurrence trigger: Sat Nov 21 5pm ET is 22:00 UTC. The credit expires ~6:30pm ET, so this leaves margin.')
param killSwitchTime string = '2026-11-21T22:00:00Z'

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
var logicAppName = 'logic-${baseName}-killswitch'
var actionGroupName = 'ag-${baseName}-killswitch'
var budgetName = 'budget-${baseName}'

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

// Model names and versions verified against the Foundry models catalog
// (learn.microsoft.com, "Foundry Models sold by Azure"): gpt-6.2-astra
// and gpt-6.2-sol, both version 2026-11-17. Deployments under one
// account are chained to avoid 409s on concurrent create.
resource astraDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview' = if (deployModels) {
  parent: gptAccount
  name: 'gpt-6-2-astra'
  sku: {
    name: 'GlobalStandard'
    capacity: gptAstraCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-6.2-astra'
      version: '2026-11-17'
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
  }
  dependsOn: [
    gptProject
  ]
}

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
    astraDeployment
  ]
}

// Foundry account for Claude. Separate account in eastus2 because the
// Claude families are hosted there (starter kit region matrix) while
// GPT-6.2 is not available in eastus2.
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

// Kill switch, part 1: the Logic App that deletes the crew resource
// group. Two triggers share one action. The HTTP trigger is called by
// the Action Group when the budget hits 95%. The Recurrence trigger
// fires once at the hard deadline (Sat Nov 21 5pm ET) whether or not
// the budget fired — deleting the group also deletes this Logic App,
// so the daily recurrence never fires twice.
// The action authenticates with the Logic App's managed identity
// against the ARM endpoint; the identity's permission comes from the
// narrow custom role below.
resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: logicAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      parameters: {}
      triggers: {
        manual: {
          type: 'Request'
          kind: 'Http'
          inputs: {
            schema: {}
          }
        }
        Recurrence: {
          type: 'Recurrence'
          recurrence: {
            frequency: 'Day'
            interval: 1
            startTime: killSwitchTime
            timeZone: 'UTC'
          }
        }
      }
      actions: {
        Delete_crew_resource_group: {
          type: 'Http'
          inputs: {
            method: 'DELETE'
            uri: '${environment().resourceManager}subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}?api-version=2021-04-01'
            authentication: {
              type: 'ManagedServiceIdentity'
              audience: environment().resourceManager
            }
          }
          runAfter: {}
        }
      }
      outputs: {}
    }
  }
}

// Kill switch, part 2: the narrowest role that can delete a resource
// group — a subscription-scoped module (the main template deploys at
// resource-group scope and cannot declare subscription-scope resources
// directly). See killswitch-role.bicep for the trade-off: the role can
// delete any resource group in the subscription, so the workflow's
// hardcoded resource-group name is the control.
module killSwitchRole './killswitch-role.bicep' = {
  name: 'killSwitchRole'
  scope: subscription()
  params: {
    logicAppPrincipalId: logicApp.identity.principalId
  }
}

// Kill switch, part 3: the Action Group the budget calls at 95%.
// The callback URL is the Logic App's HTTP trigger URL, resolved by
// listCallbackUrl — the same pattern Microsoft's own examples use.
resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: actionGroupName
  location: 'global'
  properties: {
    groupShortName: 'crewkill'
    enabled: true
    logicAppReceivers: [
      {
        name: logicApp.name
        resourceId: logicApp.id
        callbackUrl: listCallbackUrl('${logicApp.id}/triggers/manual', '2019-05-01').value
        useCommonAlertSchema: true
      }
    ]
  }
}

// Kill switch, part 4: the budget. Scoped to the crew resource group
// (this deployment's scope), $200 for November 2026. 50% and 90% are
// notify-only (email + Owner role). 95% (~$190) fires the Action Group
// and therefore the Logic App.
// Honest limit: Cost Management evaluates budgets roughly every 24
// hours on cost data that lands 8–24 hours late, so the 95% trigger
// is a backstop, not a real-time cutoff — spend can overshoot between
// evaluation runs. The 95% threshold (not 100%) leaves headroom for
// that lag, and the Recurrence trigger is the hard deadline that does
// not depend on cost data at all.
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: budgetName
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: '2026-11-01'
      endDate: '2026-12-01'
    }
    notifications: {
      Actual_50: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: [
          budgetNotifyEmail
        ]
        contactRoles: [
          'Owner'
        ]
      }
      Actual_90: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 90
        thresholdType: 'Actual'
        contactEmails: [
          budgetNotifyEmail
        ]
        contactRoles: [
          'Owner'
        ]
      }
      Actual_95: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 95
        thresholdType: 'Actual'
        contactEmails: [
          budgetNotifyEmail
        ]
        contactRoles: [
          'Owner'
        ]
        contactGroups: [
          actionGroup.id
        ]
      }
    }
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
output logicAppName string = logicApp.name
output actionGroupName string = actionGroup.name
output budgetName string = budget.name
output killSwitchTime string = killSwitchTime
