// Parameter file for infra/main.bicep. Values come from the environment
// where they vary per deployer; the rest are the defaults for this
// project. deploy.sh sources a repo-root .env first (never committed),
// so CLAUDE_ORGANIZATION_NAME and friends can live there.

using './main.bicep'

param baseName = 'agent-crew'
param location = 'westus3'
param claudeLocation = 'eastus2'
param deployModels = true
param gptAstraCapacity = 10
param gptSolCapacity = 10
param claudeCapacity = 10
param claudeModelVersion = '1'

// Empty means the Claude deployment is skipped (warn-and-skip). The
// attestation has to name the real organization — set
// CLAUDE_ORGANIZATION_NAME before deploying if Claude is wanted.
param claudeOrganizationName = readEnvironmentVariable('CLAUDE_ORGANIZATION_NAME', '')
param claudeCountryCode = readEnvironmentVariable('CLAUDE_COUNTRY_CODE', 'US')
param claudeIndustry = readEnvironmentVariable('CLAUDE_INDUSTRY', 'technology')

param budgetNotifyEmail = readEnvironmentVariable('BUDGET_NOTIFY_EMAIL', 'john.warnicke@gmail.com')
param budgetAmount = 200
param killSwitchTime = '2026-11-21T22:00:00Z'
