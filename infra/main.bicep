// ============================================================================
// Zava Air Complaints – Main Bicep Template
// Deploys: Container App, Managed Identity,
//          Log Analytics, Application Insights
// Connects to existing Azure PostgreSQL Flexible Server via env vars
// ============================================================================

targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the AZD environment')
param environmentName string

@description('Primary location for all resources')
param location string

@description('Resource group name')
param resourceGroupName string = 'rg-${environmentName}'

// ── PostgreSQL connection parameters ────────────────────────────────────────
@description('PostgreSQL server hostname')
param dbHost string = 'aaaorgpgflexserver.postgres.database.azure.com'

@description('PostgreSQL database name')
param dbName string = 'airlines'

@description('PostgreSQL user')
param dbUser string = 'adminsrram'

@secure()
@description('PostgreSQL password')
param dbPassword string

@description('PostgreSQL schema')
param dbSchema string = 'custcomplaints'

@description('CUA Container App base URL')
param cuaApiUrl string = 'https://cua-lunarair.braverock-d5a3ef65.eastus2.azurecontainerapps.io'

// ── Resource token for unique naming ────────────────────────────────────────
var resourceToken = uniqueString(subscription().id, location, environmentName)

// ── Resource Group ──────────────────────────────────────────────────────────
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: {
    'azd-env-name': environmentName
  }
}

// ── Modules ─────────────────────────────────────────────────────────────────
module monitoring 'monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    resourceToken: resourceToken
  }
}

module identity 'identity.bicep' = {
  name: 'identity'
  scope: rg
  params: {
    location: location
    resourceToken: resourceToken
  }
}

module acr 'acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    location: location
    resourceToken: resourceToken
    principalId: identity.outputs.managedIdentityPrincipalId
  }
}

module containerapp 'containerapp.bicep' = {
  name: 'containerapp'
  scope: rg
  params: {
    location: location
    resourceToken: resourceToken
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    managedIdentityId: identity.outputs.managedIdentityId
    acrLoginServer: acr.outputs.acrLoginServer
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    dbHost: dbHost
    dbName: dbName
    dbUser: dbUser
    dbPassword: dbPassword
    dbSchema: dbSchema
  }
}

module logicapp 'logicapp.bicep' = {
  name: 'logicapp'
  scope: rg
  params: {
    location: location
    resourceToken: resourceToken
    cuaApiUrl: cuaApiUrl
  }
}

module logicappRbac 'logicapp-rbac.bicep' = {
  name: 'logicapp-rbac'
  // Deploy to rg-databases because aaaorgcuastore lives there;
  // role assignments must be scoped to the same RG as the target resource.
  scope: resourceGroup('rg-databases')
  params: {
    logicAppPrincipalId: logicapp.outputs.logicAppPrincipalId
  }
}

module eventgrid 'eventgrid.bicep' = {
  name: 'eventgrid'
  // Deploy to rg-databases because aaaorgcuastore (the event source) lives there
  scope: resourceGroup('rg-databases')
  params: {
    location: location
    resourceToken: resourceToken
    logicAppCallbackUrl: logicapp.outputs.triggerCallbackUrl
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────
output RESOURCE_GROUP_ID string = rg.id
output WEBAPP_URL string = containerapp.outputs.webAppUrl
output WEBAPP_NAME string = containerapp.outputs.webAppName
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.acrLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.outputs.acrName
output LOGIC_APP_NAME string = logicapp.outputs.logicAppName
