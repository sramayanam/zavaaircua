// ============================================================================
// CUA FastAPI Container App
// Targets the EXISTING managed environment in rg-complaints (eastus2).
//
// Deploy manually (cross-subscription from main azd env):
//   az deployment group create \
//     --subscription <your-subscription-id> \
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

@description('Resource ID of the existing managed environment')
param managedEnvironmentId string

@description('Resource ID of the user-assigned managed identity')
param uamiResourceId string

@description('Client ID of the user-assigned managed identity')
param uamiClientId string

// ── Azure Storage ────────────────────────────────────────────────────────────
param storageAccountName string
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
