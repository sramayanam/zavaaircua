// ============================================================================
// Container App Environment + Container App (Node.js 20)
// ============================================================================

@description('Azure region')
param location string

@description('Unique resource token')
param resourceToken string

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('User-assigned managed identity resource ID')
param managedIdentityId string

@description('ACR login server')
param acrLoginServer string

@description('Log Analytics workspace ID for Container App Environment')
param logAnalyticsWorkspaceId string

// ── PostgreSQL connection ───────────────────────────────────────────────────
param dbHost string
param dbName string
param dbUser string
@secure()
param dbPassword string
param dbSchema string

// ── Container App Environment ───────────────────────────────────────────────
resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'azcae${resourceToken}'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceId, '2023-09-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceId, '2023-09-01').primarySharedKey
      }
    }
  }
}

// ── Container App ───────────────────────────────────────────────────────────
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'azca${resourceToken}'
  location: location
  tags: {
    'azd-service-name': 'web'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 3000
        transport: 'http'
        allowInsecure: false
      }
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentityId
        }
      ]
      secrets: [
        {
          name: 'db-password'
          value: dbPassword
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'DB_HOST', value: dbHost }
            { name: 'DB_PORT', value: '5432' }
            { name: 'DB_NAME', value: dbName }
            { name: 'DB_USER', value: dbUser }
            { name: 'DB_PASSWORD', secretRef: 'db-password' }
            { name: 'DB_SCHEMA', value: dbSchema }
            { name: 'DB_SSL', value: 'true' }
            { name: 'PORT', value: '3000' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────
output webAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output webAppName string = containerApp.name
