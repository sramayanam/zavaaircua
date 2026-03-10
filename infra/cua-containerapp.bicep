// ============================================================================
// CUA FastAPI Container App
// Targets the EXISTING managed environment in rg-complaints (eastus2).
//
// Deploy manually (cross-subscription from main azd env):
//   az deployment group create \
//     --subscription 32e739cb-7b23-4259-a180-e1e0e69b974d \
//     --resource-group rg-complaints \
//     --template-file infra/cua-containerapp.bicep \
//     --parameters \
//         acrLoginServer=<acr>.azurecr.io \
//         cuaImage=<acr>.azurecr.io/cua:<tag> \
//         azureOpenAiBaseUrl=<url> \
//         azureOpenAiDeployment=<deployment> \
//         zavaAirUrl=<url>
// ============================================================================

@description('Name for the CUA Container App')
param containerAppName string = 'cua-lunarair'

@description('ACR login server (e.g. myacr.azurecr.io)')
param acrLoginServer string

@description('Fully-qualified CUA container image reference')
param cuaImage string

// ── Existing managed environment (eastus2, rg-complaints) ──────────────────
var managedEnvironmentId = '/subscriptions/32e739cb-7b23-4259-a180-e1e0e69b974d/resourceGroups/rg-complaints/providers/Microsoft.App/managedEnvironments/azcaegsuqive4sc3tm'

// ── User-Assigned Managed Identity (cross-subscription) ────────────────────
var uamiResourceId = '/subscriptions/aa8123d8-cdcc-443a-a2a1-a0ed191da95c/resourceGroups/rg-sc/providers/Microsoft.ManagedIdentity/userAssignedIdentities/aaaorguamgdidentity'
var uamiClientId = '7f12934d-08b8-402b-8c1d-8529efd4f8c1'

// ── Azure Storage (aaaorgcuastore) ──────────────────────────────────────────
param storageAccountName string = 'aaaorgcuastore'
param blobContainerName string = 'cua-screenshots'
param storageQueueName string = 'cua-agent-jobs'
param storageTableName string = 'cuaJobStatus'

// ── Azure OpenAI ────────────────────────────────────────────────────────────
@description('Azure OpenAI base URL (e.g. https://myoai.openai.azure.com/openai)')
param azureOpenAiBaseUrl string

@description('Azure OpenAI model deployment name')
param azureOpenAiDeployment string

// ── App URL ─────────────────────────────────────────────────────────────────
@description('URL of the Zava Air complaints web app')
param zavaAirUrl string

// ── AI Foundry (optional) ────────────────────────────────────────────────────
@description('Azure AI Foundry project endpoint (leave empty to use direct Azure OpenAI)')
param foundryProjectEndpoint string = ''

@description('Foundry model deployment name (defaults to azureOpenAiDeployment if empty)')
param foundryModelDeploymentName string = ''

// ── CORS origins (comma-separated) ──────────────────────────────────────────
@description('Allowed CORS origins. Defaults to all origins if empty.')
param allowedOrigins string = ''

// ── Container App ────────────────────────────────────────────────────────────
resource cuaContainerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: 'eastus2'
  tags: {
    'azd-service-name': 'cua'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnvironmentId
    configuration: {
      ingress: {
        external: true
        targetPort: 8501
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: acrLoginServer
          identity: uamiResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'cua'
          image: cuaImage
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            // ── Identity: instructs DefaultAzureCredential to use this UAMI ──
            { name: 'AZURE_CLIENT_ID', value: uamiClientId }

            // ── Azure OpenAI ──────────────────────────────────────────────
            { name: 'AZURE_OPENAI_BASE_URL', value: azureOpenAiBaseUrl }
            { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }

            // ── AI Foundry (optional) ─────────────────────────────────────
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'FOUNDRY_MODEL_DEPLOYMENT_NAME', value: foundryModelDeploymentName }

            // ── Target web app ────────────────────────────────────────────
            { name: 'ZAVA_AIR_URL', value: zavaAirUrl }

            // ── Azure Storage (Queue + Table + Blob) ──────────────────────
            { name: 'AZURE_STORAGE_ACCOUNT_NAME', value: storageAccountName }
            { name: 'AZURE_STORAGE_QUEUE_NAME', value: storageQueueName }
            { name: 'AZURE_STORAGE_TABLE_NAME', value: storageTableName }
            { name: 'AZURE_STORAGE_BLOB_CONTAINER_NAME', value: blobContainerName }

            // ── CORS ──────────────────────────────────────────────────────
            { name: 'ALLOWED_ORIGINS', value: allowedOrigins }
          ]
        }
      ]
      scale: {
        // Keep at least 1 replica so the queue poller is always running
        minReplicas: 1
        maxReplicas: 2
      }
    }
  }
}

// ── Outputs ──────────────────────────────────────────────────────────────────
output cuaUrl string = 'https://${cuaContainerApp.properties.configuration.ingress.fqdn}'
output cuaName string = cuaContainerApp.name
