// ============================================================================
// Azure Container Registry
// ============================================================================

@description('Azure region')
param location string

@description('Unique resource token')
param resourceToken string

@description('Principal ID of the managed identity to grant AcrPull')
param principalId string

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: 'azacr${resourceToken}'
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

// AcrPull role assignment for managed identity
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, principalId, acrPullRoleId)
  scope: acr
  properties: {
    principalId: principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalType: 'ServicePrincipal'
  }
}

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
