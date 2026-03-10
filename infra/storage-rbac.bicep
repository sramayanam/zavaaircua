// ============================================================================
// RBAC role assignments for UAMI on the existing storage account.
//
// These roles are required for the CUA Container App to access:
//   – Azure Blob Storage  (screenshot uploads)
//   – Azure Queue Storage (job dispatch)
//   – Azure Table Storage (job status)
//
// Deploy once (run in the subscription/RG that owns the storage account):
//   az deployment group create \
//     --resource-group <rg-containing-storage-account> \
//     --template-file infra/storage-rbac.bicep
// ============================================================================

@description('Name of the existing storage account to grant access on.')
param storageAccountName string

@description('Principal ID of the User-Assigned Managed Identity.')
param uamiPrincipalId string

// ── Reference the existing storage account ───────────────────────────────────
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: storageAccountName
}

// ── Role definition IDs (built-in, subscription-invariant) ───────────────────
var blobDataContributorRoleId    = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var queueDataContributorRoleId   = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var tableDataContributorRoleId   = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'

// ── Storage Blob Data Contributor ────────────────────────────────────────────
resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, uamiPrincipalId, blobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataContributorRoleId)
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Storage Queue Data Contributor ───────────────────────────────────────────
resource queueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, uamiPrincipalId, queueDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueDataContributorRoleId)
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Storage Table Data Contributor ───────────────────────────────────────────
resource tableRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, uamiPrincipalId, tableDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', tableDataContributorRoleId)
    principalId: uamiPrincipalId
    principalType: 'ServicePrincipal'
  }
}
