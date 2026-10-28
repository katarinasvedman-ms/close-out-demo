targetScope = 'subscription'

param environmentName string
param projectResourceId string
param operatorPrincipalId string
@minLength(1)
param knowledgeModelDeploymentName string
@minLength(1)
param knowledgeModelName string
param knowledgeBaseName string = 'close-out-demo'
param location string = 'swedencentral'
param searchLocation string = 'francecentral'
param securityControlIgnore bool = false
param connectionName string = 'close-out-knowledge'
param tags object = {}

var projectParts = split(projectResourceId, '/')
var knowledgeGroupName = 'rg-${environmentName}-knowledge'

resource knowledgeGroup 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: knowledgeGroupName
  location: location
  tags: union(tags, { 'azd-env-name': environmentName }, securityControlIgnore ? { SecurityControl: 'Ignore' } : {})
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' existing = {
  scope: resourceGroup(projectParts[2], projectParts[4])
  name: projectParts[8]
  resource project 'projects' existing = {
    name: projectParts[10]
  }
}

module resources 'resources.bicep' = {
  name: 'close-out-knowledge-resources'
  scope: knowledgeGroup
  params: {
    location: location
    searchLocation: searchLocation
    operatorPrincipalId: operatorPrincipalId
    projectPrincipalId: foundryAccount::project.identity.principalId
    tags: tags
  }
}

module connection 'connection.bicep' = {
  name: 'close-out-knowledge-connection'
  scope: resourceGroup(projectParts[2], projectParts[4])
  params: {
    accountName: projectParts[8]
    projectName: projectParts[10]
    connectionName: connectionName
    searchEndpoint: resources.outputs.searchEndpoint
    searchResourceId: resources.outputs.searchResourceId
    searchPrincipalId: resources.outputs.searchPrincipalId
    knowledgeBaseName: knowledgeBaseName
  }
}

output searchEndpoint string = resources.outputs.searchEndpoint
output storageEndpoint string = resources.outputs.storageEndpoint
output storageResourceId string = resources.outputs.storageResourceId
output containerName string = resources.outputs.containerName
output connectionName string = connectionName
output resourceGroupName string = knowledgeGroupName
output knowledgeBaseName string = knowledgeBaseName
output knowledgeModel object = {
  kind: 'azureOpenAI'
  azureOpenAIParameters: {
    resourceUri: foundryAccount.properties.endpoints['OpenAI Language Model Instance API']
    deploymentId: knowledgeModelDeploymentName
    modelName: knowledgeModelName
  }
}
