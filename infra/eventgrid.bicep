// ============================================================================
// Event Grid – Storage Account → Logic App HTTP Trigger
//
// Creates:
//   • System Topic on the storage account (Microsoft.Storage.StorageAccounts)
//   • Event Subscription filtering on Microsoft.Storage.BlobCreated
//     for the payloads container → webhooks to the Logic App HTTP trigger
//
// Deploy scope: rg-databases (where the storage account lives)
// ============================================================================

@description('Azure region')
param location string

@description('Unique resource token for naming')
param resourceToken string

@description('Storage account name')
param storageAccountName string

@description('Logic App HTTP trigger callback URL (from logicapp module output)')
param logicAppCallbackUrl string

// ── Event Grid System Topic (pre-existing) ────────────────────────────────────
// One system topic per storage account is allowed; reference the existing one.
@description('Name of the pre-existing Event Grid system topic for the storage account')
param systemTopicName string

resource systemTopic 'Microsoft.EventGrid/systemTopics@2022-06-15' existing = {
  name: systemTopicName
}

// ── Event Subscription → Logic App HTTP trigger ──────────────────────────────
resource eventSubscription 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2022-06-15' = {
  parent: systemTopic
  name: 'sub-la-payloads-${resourceToken}'
  properties: {
    filter: {
      includedEventTypes: [
        'Microsoft.Storage.BlobCreated'
      ]
      // Only fire for the payloads container
      subjectBeginsWith: '/blobServices/default/containers/payloads/blobs/'
    }
    destination: {
      endpointType: 'WebHook'
      properties: {
        endpointUrl: logicAppCallbackUrl
        maxEventsPerBatch: 1
        preferredBatchSizeInKilobytes: 64
      }
    }
    eventDeliverySchema: 'EventGridSchema'
    retryPolicy: {
      maxDeliveryAttempts: 3
      eventTimeToLiveInMinutes: 60
    }
  }
}

output systemTopicName string = systemTopic.name
