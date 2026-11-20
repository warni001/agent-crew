// Agent Crew — real-time token alerts (the fast spend cutoff).
//
// Four metric alert rules on the Foundry account, one per deployment
// (gpt-6-2-astra / gpt-6-2-sol) and direction (prompt / completion
// tokens). Each fires the EXISTING kill-switch Action Group, which
// calls the EXISTING Logic App that deletes the crew resource group.
//
// Why this exists: the $200 Cost Management budget evaluates roughly
// daily on cost data 8-24h late, so it cannot stop a runaway crew in
// time. Platform token metrics land within minutes, and a metric
// alert evaluates every minute — this is the real-time layer.
//
// Threshold math: each rule fires at $40 of tokens in a rolling 24h
// window (param alertDollarsPerRule). At first fire the worst case
// total is just under 4 x $40 = $160 — the firing rule at $40, the
// other three just below — plus a few dollars of overshoot during
// the minutes between evaluation and the delete. That stays under
// the $190 stop line. Prices are $ per 1M tokens: Astra $8 in /
// $40 out, Sol $2 in / $10 out (cached input is cheaper, so pricing
// all input at the full rate fires early — the safe direction).
//
// Honest limits, read before relying on this:
//   - The window is 24h (the longest a metric alert allows). Spend
//     spread evenly over several days never trips a single window —
//     the slow drip is caught by the Cost Management budget, the
//     Saturday deadline, and the crew's own in-code spend counter
//     (crew/spend.py), which is cumulative across the whole run.
//   - Metric latency is minutes, not seconds: the alert fires ~1-5
//     minutes after the tokens are spent, and the Logic App delete
//     takes seconds after that.
//
// Deploy this file ON ITS OWN — do NOT re-run infra/deploy.sh or
// deploy infra/main.bicep to add these rules. main.bicep declares
// the container app with the placeholder image; an incremental
// re-deploy would reset the live app's image back to the placeholder
// and mint a new revision, killing the running crew until the next
// push. This file declares ONLY the alert rules, so deploying it
// touches nothing else:
//
//   az deployment group create -g rg-agent-crew \
//     --template-file infra/alerts.bicep
//
// Names (foundry-gpt-vaxvu3c2, ag-agent-crew-killswitch) are the live
// resource names from the Nov 19 deploy; override with parameters if
// they ever change. Metric names (ProcessedPromptTokens,
// GeneratedTokens) and the ModelDeploymentName dimension are verified
// against Microsoft Learn, "Monitoring data reference for Azure
// OpenAI" (Microsoft.CognitiveServices/accounts).

targetScope = 'resourceGroup'

@description('Azure region of the Foundry account (for targetResourceRegion).')
param location string = 'westus3'

@description('Name of the existing Foundry (Cognitive Services) account holding the GPT deployments.')
param gptAccountName string = 'foundry-gpt-vaxvu3c2'

@description('Name of the existing kill-switch Action Group.')
param actionGroupName string = 'ag-agent-crew-killswitch'

@description('Dollars of tokens per rule before the kill fires. Worst case total at first fire is just under 4x this amount.')
param alertDollarsPerRule int = 40

resource gptAccount 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' existing = {
  name: gptAccountName
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' existing = {
  name: actionGroupName
}

// One rule per (deployment, direction). Thresholds are computed from
// the dollar amount so the price math stays in one place.
var rules = [
  {
    key: 'astra-input'
    metric: 'ProcessedPromptTokens'
    deployment: 'gpt-6-2-astra'
    pricePerMillion: 8
  }
  {
    key: 'astra-output'
    metric: 'GeneratedTokens'
    deployment: 'gpt-6-2-astra'
    pricePerMillion: 40
  }
  {
    key: 'sol-input'
    metric: 'ProcessedPromptTokens'
    deployment: 'gpt-6-2-sol'
    pricePerMillion: 2
  }
  {
    key: 'sol-output'
    metric: 'GeneratedTokens'
    deployment: 'gpt-6-2-sol'
    pricePerMillion: 10
  }
]

resource tokenAlerts 'Microsoft.Insights/metricAlerts@2018-03-01' = [
  for rule in rules: {
    name: 'ma-agent-crew-${rule.key}'
    location: 'global'
    properties: {
      description: 'Kill switch: ${rule.deployment} ${rule.metric} past $${alertDollarsPerRule} in 24h (fires the Logic App that deletes rg-agent-crew).'
      severity: 0
      enabled: true
      scopes: [
        gptAccount.id
      ]
      evaluationFrequency: 'PT1M'
      windowSize: 'P1D'
      criteria: {
        'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
        allOf: [
          {
            criterionType: 'StaticThresholdCriterion'
            name: 'Tokens'
            metricName: rule.metric
            metricNamespace: 'Microsoft.CognitiveServices/accounts'
            operator: 'GreaterThan'
            threshold: alertDollarsPerRule * 1000000 / rule.pricePerMillion
            timeAggregation: 'Total'
            dimensions: [
              {
                name: 'ModelDeploymentName'
                operator: 'Include'
                values: [
                  rule.deployment
                ]
              }
            ]
          }
        ]
      }
      // Stay fired until resolved by hand: a dip below the threshold
      // must not un-fire the kill while the delete is in flight.
      autoMitigate: false
      actions: [
        {
          actionGroupId: actionGroup.id
        }
      ]
      targetResourceType: 'Microsoft.CognitiveServices/accounts'
      targetResourceRegion: location
    }
  }
]

output alertNames array = [for rule in rules: 'ma-agent-crew-${rule.key}']
