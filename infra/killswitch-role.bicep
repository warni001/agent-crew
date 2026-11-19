// Kill-switch role: the narrowest role that can delete a resource group.
//
// Deleting a resource group is a subscription-level operation, so this
// module targets the subscription scope — the main template deploys at
// resource-group scope and cannot declare these resources directly.
// There is no way to scope this permission to one resource group. The
// trade-off is explicit: the role can delete ANY resource group in the
// subscription, so the blast radius is bounded by the workflow, which
// hardcodes the crew resource group name in the DELETE URI. The
// alternative (Contributor on the subscription) would let the identity
// create and modify anything; this role lets it do exactly one thing.
// If the workflow is ever edited to point at another resource group,
// that group is at risk — treat the workflow definition as the control.

targetScope = 'subscription'

@description('Principal ID of the kill-switch Logic App system-assigned managed identity.')
param logicAppPrincipalId string

@description('Display name of the custom role.')
param roleName string = 'Agent Crew Kill Switch'

resource killSwitchRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(subscription().id, 'agent-crew-killswitch')
  properties: {
    roleName: roleName
    description: 'Delete a resource group. Single-action custom role for the kill-switch Logic App.'
    type: 'CustomRole'
    permissions: [
      {
        actions: [
          'Microsoft.Resources/subscriptions/resourceGroups/delete'
        ]
        notActions: []
        dataActions: []
        notDataActions: []
      }
    ]
    assignableScopes: [
      subscription().id
    ]
  }
}

resource killSwitchAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, logicAppPrincipalId, killSwitchRole.id)
  properties: {
    roleDefinitionId: killSwitchRole.id
    principalId: logicAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}
