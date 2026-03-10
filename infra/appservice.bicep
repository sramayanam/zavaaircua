// ============================================================================
// App Service Plan + App Service (Linux, Node.js 20)
// ============================================================================

@description('Azure region')
param location string

@description('Unique resource token')
param resourceToken string

@description('Application Insights connection string')
param appInsightsConnectionString string

@description('User-assigned managed identity resource ID')
param managedIdentityId string

// ── PostgreSQL connection ───────────────────────────────────────────────────
param dbHost string
param dbName string
param dbUser string
@secure()
param dbPassword string
param dbSchema string

// ── App Service Plan (Linux, B1) ────────────────────────────────────────────
resource appServicePlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'azasp${resourceToken}'
  location: location
  kind: 'linux'
  sku: {
    name: 'S1'
    tier: 'Standard'
  }
  properties: {
    reserved: true // Required for Linux
  }
}

// ── App Service (Node.js 20 LTS) ───────────────────────────────────────────
resource webApp 'Microsoft.Web/sites@2024-04-01' = {
  name: 'azweb${resourceToken}'
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
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|20-lts'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appCommandLine: 'node server.js'
      appSettings: [
        {
          name: 'DB_HOST'
          value: dbHost
        }
        {
          name: 'DB_PORT'
          value: '5432'
        }
        {
          name: 'DB_NAME'
          value: dbName
        }
        {
          name: 'DB_USER'
          value: dbUser
        }
        {
          name: 'DB_PASSWORD'
          value: dbPassword
        }
        {
          name: 'DB_SCHEMA'
          value: dbSchema
        }
        {
          name: 'DB_SSL'
          value: 'true'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'WEBSITE_NODE_DEFAULT_VERSION'
          value: '~20'
        }
      ]
    }
  }
}

// ── Site Extension (required by AZD IaC rules) ──────────────────────────────
resource siteExtension 'Microsoft.Web/sites/siteextensions@2024-04-01' = {
  parent: webApp
  name: 'Microsoft.ApplicationInsights.AzureWebSites'
}

// ── Outputs ─────────────────────────────────────────────────────────────────
output webAppUrl string = 'https://${webApp.properties.defaultHostName}'
output webAppName string = webApp.name
