targetScope = 'resourceGroup'

@description('Globally unique Foundry resource name: lowercase letters, digits and hyphens.')
@minLength(3)
@maxLength(64)
param foundryName string

param projectName string = 'close-out-demo'
@allowed(['swedencentral'])
param location string = 'swedencentral'
@allowed(['francecentral'])
param searchLocation string = 'francecentral'
param modelDeploymentName string = 'close-out-gpt-4o'
param modelName string = 'gpt-4o'
param modelVersion string = '2024-11-20'
@minValue(1)
param modelCapacity int = 30
@description('User for portal deployments; override for service-principal automation.')
@allowed(['User', 'ServicePrincipal'])
param operatorPrincipalType string = 'User'

var operatorPrincipalId = deployer().objectId
var suffix = uniqueString(resourceGroup().id, foundryName)
var tags = { 'azd-env-name': foundryName }
var foundryUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
var logReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '73c42c96-874c-492b-b04d-ab87d138a893')

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
  resource project 'projects' = {
    name: projectName
    location: location
    identity: { type: 'SystemAssigned' }
    properties: {
      displayName: projectName
      description: 'Read-only synthetic close-out demonstration.'
    }
  }
}

resource operatorAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry::project.id, operatorPrincipalId, foundryUser)
  scope: foundry::project
  properties: {
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
    roleDefinitionId: foundryUser
  }
}

module model '../models.bicep' = {
  name: 'close-out-model'
  params: {
    accountName: foundry.name
    deploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    capacity: modelCapacity
  }
}

module knowledge '../knowledge/resources.bicep' = {
  name: 'close-out-knowledge-resources'
  params: {
    location: location
    searchLocation: searchLocation
    operatorPrincipalId: operatorPrincipalId
    operatorPrincipalType: operatorPrincipalType
    projectPrincipalId: foundry::project.identity.principalId
    tags: tags
  }
}

module knowledgeConnections '../knowledge/connection.bicep' = {
  name: 'close-out-knowledge-connection'
  params: {
    accountName: foundry.name
    projectName: foundry::project.name
    connectionName: 'close-out-knowledge'
    knowledgeBaseName: 'close-out-demo'
    searchEndpoint: knowledge.outputs.searchEndpoint
    searchResourceId: knowledge.outputs.searchResourceId
    searchPrincipalId: knowledge.outputs.searchPrincipalId
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

resource insightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: foundry::project
  name: 'close-out-insights'
  properties: {
    category: 'AppInsights'
    authType: 'ApiKey'
    target: insights.id
    isSharedToAll: true
    credentials: { key: insights.properties.ConnectionString }
    metadata: {
      ApiType: 'Azure'
      ResourceId: insights.id
    }
  }
}

resource insightsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(insights.id, foundry::project.id, logReader)
  scope: insights
  properties: {
    principalId: foundry::project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: logReader
  }
}

output resourceGroupName string = resourceGroup().name
output subscriptionId string = subscription().subscriptionId
output tenantId string = tenant().tenantId
output foundryName string = foundry.name
output projectName string = foundry::project.name
output projectResourceId string = foundry::project.id
output projectEndpoint string = foundry::project.properties.endpoints['AI Foundry API']
output modelDeploymentName string = model.outputs.deploymentName
output modelName string = modelName
output modelVersion string = modelVersion
output searchEndpoint string = knowledge.outputs.searchEndpoint
output storageEndpoint string = knowledge.outputs.storageEndpoint
output storageResourceId string = knowledge.outputs.storageResourceId
output containerName string = knowledge.outputs.containerName
output connectionName string = 'close-out-knowledge'
output knowledgeBaseName string = 'close-out-demo'
output knowledgeModel object = {
  kind: 'azureOpenAI'
  azureOpenAIParameters: {
    resourceUri: foundry.properties.endpoints['OpenAI Language Model Instance API']
    deploymentId: model.outputs.deploymentName
    modelName: modelName
  }
}
output applicationInsightsResourceId string = insights.id
