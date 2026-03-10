// ============================================================================
// Logic App RBAC – grants the Logic App's system-assigned MSI access to
// the storage account so it can:
//   • Read blobs from the `payloads` container
//   • Write rows to the `logicAppAudit` table
//
// Role IDs (built-in):
//   StorageBlobDataReader        2a2b9908-6ea1-4ae2-8e65-a410df84e7d1
//   StorageTableDataContributor  0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3
// ============================================================================

@description('Principal ID of the Logic App system-assigned managed identity')
param logicAppPrincipalId string

@description('Storage account name – must already exist in this resource group')
param storageAccountName string

// This module is deployed to rg-databases (where the storage account lives),
// No cross-RG scope is needed on the existing reference.
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

// Ensure the Table service is present (idempotent on existing accounts)
resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

// Create the logicAppAudit table (idempotent – safe to re-deploy)
resource auditTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: 'logicAppAudit'
}

// Blob Data Reader – read payload blobs from the `payloads` container
resource blobReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, logicAppPrincipalId, 'blob-data-reader')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1' // Storage Blob Data Reader
    )
    principalId: logicAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Table Data Contributor – write audit records to `logicAppAudit`
resource tableContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, logicAppPrincipalId, 'table-data-contributor')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3' // Storage Table Data Contributor
    )
    principalId: logicAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}
