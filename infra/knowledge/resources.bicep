targetScope = 'resourceGroup'

param location string
param searchLocation string
param operatorPrincipalId string
@allowed(['User', 'ServicePrincipal'])
param operatorPrincipalType string = 'User'
param projectPrincipalId string
param tags object = {}

var suffix = uniqueString(resourceGroup().id)
var blobReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var blobContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var searchReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
var searchContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
var searchServiceContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${suffix}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    isHnsEnabled: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    allowCrossTenantReplication: false
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource documents 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'documents'
  properties: { publicAccess: 'None' }
}

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: 'srch-${suffix}'
  location: searchLocation
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
    semanticSearch: 'free'
  }
}

resource indexerBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(documents.id, search.id, blobReader)
  scope: documents
  properties: {
    roleDefinitionId: blobReader
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource operatorBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(documents.id, operatorPrincipalId, blobContributor)
  scope: documents
  properties: {
    roleDefinitionId: blobContributor
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
  }
}

resource projectSearchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, projectPrincipalId, searchReader)
  scope: search
  properties: {
    roleDefinitionId: searchReader
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource operatorSearchRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in [searchContributor, searchServiceContributor]: {
  name: guid(search.id, operatorPrincipalId, role)
  scope: search
  properties: {
    roleDefinitionId: role
    principalId: operatorPrincipalId
    principalType: operatorPrincipalType
  }
}]

output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchResourceId string = search.id
output searchPrincipalId string = search.identity.principalId
output storageEndpoint string = storage.properties.primaryEndpoints.blob
output storageResourceId string = storage.id
output containerName string = documents.name
