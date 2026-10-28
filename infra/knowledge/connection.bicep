targetScope = 'resourceGroup'

param accountName string
param projectName string
param connectionName string
param searchEndpoint string
param searchResourceId string
param searchPrincipalId string
param knowledgeBaseName string

var modelUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')

resource account 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' existing = {
  name: accountName
  resource project 'projects' existing = {
    name: projectName
  }
}

resource searchModelUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, searchResourceId, modelUser)
  scope: account
  properties: {
    roleDefinitionId: modelUser
    principalId: searchPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource knowledgeConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  parent: account::project
  name: connectionName
  properties: {
    category: 'RemoteTool'
    authType: 'ProjectManagedIdentity'
    target: '${searchEndpoint}/knowledgebases/${knowledgeBaseName}/mcp?api-version=2026-08-01-preview'
    audience: 'https://search.azure.com/'
    isSharedToAll: true
    metadata: { ApiType: 'Azure' }
  }
}

resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-10-01-preview' = {
  parent: account::project
  name: '${connectionName}-search'
  properties: {
    category: 'CognitiveSearch'
    authType: 'AAD'
    target: searchEndpoint
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: searchResourceId
    }
  }
}
