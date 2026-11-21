// Parameter file for infra/main.bicep. Values come from the environment
// where they vary per deployer; the rest are the defaults for this
// project. deploy.sh sources a repo-root .env first (never committed),
// so CLAUDE_ORGANIZATION_NAME and friends can live there.

using './main.bicep'

param baseName = 'agent-crew'
param location = 'westus3'
param claudeLocation = 'eastus2'
param deployModels = true
param gptAstraCapacity = 100
param gptSolCapacity = 100
param claudeCapacity = 10
param claudeModelVersion = '1'

// Empty means the Claude deployment is skipped (warn-and-skip). The
// attestation has to name the real organization — set
// CLAUDE_ORGANIZATION_NAME before deploying if Claude is wanted.
param claudeOrganizationName = readEnvironmentVariable('CLAUDE_ORGANIZATION_NAME', '')
param claudeCountryCode = readEnvironmentVariable('CLAUDE_COUNTRY_CODE', 'US')
param claudeIndustry = readEnvironmentVariable('CLAUDE_INDUSTRY', 'technology')

// Object ID of the deploy service principal, for the ledger Storage
// Blob Data Reader grant (rollback watch). deploy.sh exports it as
// DEPLOY_SP_OID. Empty means no assignment is created.
param deployServicePrincipalId = readEnvironmentVariable('DEPLOY_SP_OID', '')
