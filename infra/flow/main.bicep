targetScope = 'resourceGroup'

@minLength(1)
param environmentName string
param location string = 'swedencentral'
@description('Existing Foundry project; no model, connection or Fabric item is created.')
param projectResourceId string
@description('Approved existing Entra API registration protecting the invocation adapter.')
param adapterClientId string
param adapterAudience string
@description('Approved external Fabric dispatch identity. Empty keeps the coordinator disabled and denies callback access; does not grant Fabric workspace access.')
param fabricDispatchPrincipalId string = ''
@description('Enable only after verifying the explicit external bindings and identity grants. Callback authentication alone does not prove native tools or a completed response.')
param enableCoordinatorDispatch bool = false
@description('Keep false for the playground publication path; true retains the separate Blob-triggered inference path.')
param enableBlobValidation bool = true
@description('Optional explicitly selected operator allowed to upload synthetic batches and read receipts.')
param uploadOperatorPrincipalId string = ''
@allowed(['User', 'ServicePrincipal'])
param uploadOperatorPrincipalType string = 'User'
@description('Optional explicitly authorized Foundry project managed identity for the publish-only assessment tool; cannot invoke validation/coordinator routes.')
param publisherPrincipalId string = ''
param eventstreamNamespace string
param eventstreamEntity string
@description('Pinned new prompt versions and existing native IQ bindings. Contains no credentials.')
param foundryConfig object
@description('Explicit networking decision for this new optional target.')
param allowPublicNetworkAccess bool
param tags object = {}

var suffix = uniqueString(resourceGroup().id, environmentName, 'headless-v02')
var names = {
  data: 'stflow${suffix}'
  host: 'sthost${suffix}'
  vault: 'kv-flow-${suffix}'
  workflow: 'logic-${environmentName}-${suffix}'
  adapter: 'func-${environmentName}-${suffix}'
}
var networkAccess = allowPublicNetworkAccess ? 'Enabled' : 'Disabled'
var blobReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var blobContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
var vaultReader = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource data 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: names.data
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: networkAccess
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: data
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: { enabled: true, days: 7 }
  }
}
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for name in ['inputs', 'ledger', 'results']: {
  parent: blobs
  name: name
  properties: { publicAccess: 'None' }
}]

resource host 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: names.host
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    publicNetworkAccess: networkAccess
  }
}
resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: host
  name: 'default'
}
resource workflowContent 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: files
  name: 'workflow-content'
  properties: { shareQuota: 5 }
}
resource adapterContent 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: files
  name: 'adapter-content'
  properties: { shareQuota: 5 }
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: names.vault
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: networkAccess
  }
}
resource hostSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'workflow-host-storage'
  properties: {
    value: 'DefaultEndpointsProtocol=https;AccountName=${host.name};AccountKey=${host.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
  }
}
resource hostSecretIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-flow-host-${suffix}'
  location: location
  tags: tags
}
resource hostSecretRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, hostSecretIdentity.id, vaultReader)
  scope: vault
  properties: {
    principalId: hostSecretIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: vaultReader
  }
}
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-flow-${suffix}'
  location: location
  tags: tags
  properties: { retentionInDays: 30, sku: { name: 'PerGB2018' } }
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-flow-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}
resource workflowPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'asp-workflow-${suffix}'
  location: location
  tags: tags
  kind: 'elastic'
  sku: { name: 'WS1', tier: 'WorkflowStandard', capacity: 1 }
  properties: { reserved: false }
}
resource adapterPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'asp-adapter-${suffix}'
  location: location
  tags: tags
  kind: 'elastic'
  sku: { name: 'EP1', tier: 'ElasticPremium', capacity: 1 }
  properties: { reserved: true }
}
var hostSettings = [
  { name: 'AzureWebJobsStorage', value: '@Microsoft.KeyVault(SecretUri=${hostSecret.properties.secretUriWithVersion})' }
  { name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING', value: '@Microsoft.KeyVault(SecretUri=${hostSecret.properties.secretUriWithVersion})' }
  { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
  { name: 'FLOW_STORAGE_URL', value: data.properties.primaryEndpoints.blob }
]
resource workflow 'Microsoft.Web/sites@2023-12-01' = {
  name: names.workflow
  location: location
  tags: tags
  kind: 'functionapp,workflowapp'
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: { '${hostSecretIdentity.id}': {} }
  }
  properties: {
    keyVaultReferenceIdentity: hostSecretIdentity.id
    serverFarmId: workflowPlan.id
    httpsOnly: true
    publicNetworkAccess: networkAccess
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: concat(hostSettings, [
        { name: 'APP_KIND', value: 'workflowApp' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'dotnet' }
        { name: 'WEBSITE_NODE_DEFAULT_VERSION', value: '~22' }
        { name: 'WEBSITE_CONTENTSHARE', value: workflowContent.name }
        { name: 'FLOW_ADAPTER_URL', value: 'https://${names.adapter}.azurewebsites.net' }
        { name: 'FLOW_ADAPTER_AUDIENCE', value: adapterAudience }
        { name: 'FLOW_FOUNDRY_PROJECT_ENDPOINT', value: foundryConfig.endpoint }
        { name: 'FLOW_COORDINATOR_AGENT_NAME', value: foundryConfig.closeout.name }
        { name: 'FLOW_COORDINATOR_AGENT_VERSION', value: string(foundryConfig.closeout.version) }
        { name: 'Workflows.validation.FlowState', value: enableBlobValidation ? 'Enabled' : 'Disabled' }
        { name: 'Workflows.coordinator.FlowState', value: enableCoordinatorDispatch && !empty(fabricDispatchPrincipalId) ? 'Enabled' : 'Disabled' }
      ])
    }
  }
  dependsOn: [hostSecretRole]
}
resource adapter 'Microsoft.Web/sites@2023-12-01' = {
  name: names.adapter
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: { '${hostSecretIdentity.id}': {} }
  }
  properties: {
    keyVaultReferenceIdentity: hostSecretIdentity.id
    serverFarmId: adapterPlan.id
    httpsOnly: true
    publicNetworkAccess: networkAccess
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: concat(hostSettings, [
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'WEBSITE_CONTENTSHARE', value: adapterContent.name }
        { name: 'FLOW_ALLOWED_CALLER_OIDS', value: workflow.identity.principalId }
        { name: 'FLOW_PUBLISHER_CALLER_OIDS', value: publisherPrincipalId }
        { name: 'FLOW_EVENTSTREAM_NAMESPACE', value: eventstreamNamespace }
        { name: 'FLOW_EVENTSTREAM_ENTITY', value: eventstreamEntity }
        { name: 'FLOW_FOUNDRY_CONFIG_JSON', value: string(foundryConfig) }
        { name: 'FLOW_CONTRACTS_ROOT', value: 'contracts' }
      ])
    }
  }
  dependsOn: [hostSecretRole]
}
resource adapterAuth 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: adapter
  name: 'authsettingsV2'
  properties: {
    platform: { enabled: true }
    globalValidation: { requireAuthentication: true, unauthenticatedClientAction: 'Return401' }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: adapterClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [adapterAudience]
          defaultAuthorizationPolicy: {
            allowedPrincipals: {
              identities: concat([workflow.identity.principalId], empty(publisherPrincipalId) ? [] : [publisherPrincipalId])
            }
          }
        }
      }
    }
    httpSettings: { requireHttps: true }
  }
}
resource workflowAuth 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: workflow
  name: 'authsettingsV2'
  properties: {
    platform: { enabled: true }
    globalValidation: { requireAuthentication: true, unauthenticatedClientAction: 'Return401' }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: adapterClientId
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: [adapterAudience]
          defaultAuthorizationPolicy: {
            allowedPrincipals: {
              // A missing dispatch binding must fail closed, not allow every tenant principal.
              identities: [empty(fabricDispatchPrincipalId) ? '00000000-0000-0000-0000-000000000000' : fabricDispatchPrincipalId]
            }
          }
        }
      }
    }
    httpSettings: { requireHttps: true }
  }
}
resource workflowInputRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableBlobValidation) {
  name: guid(containers[0].id, workflow.id, blobContributor)
  scope: containers[0]
  properties: { principalId: workflow.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobContributor }
}
resource workflowResultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containers[2].id, workflow.id, blobContributor)
  scope: containers[2]
  properties: { principalId: workflow.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobContributor }
}
resource adapterDataRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, index) in ['inputs', 'ledger', 'results']: {
  name: guid(containers[index].id, adapter.id, name)
  scope: containers[index]
  properties: {
    principalId: adapter.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: index == 0 ? blobReader : blobContributor
  }
}]
resource operatorDataRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, index) in ['inputs', 'ledger', 'results']: if (!empty(uploadOperatorPrincipalId)) {
  name: guid(containers[index].id, uploadOperatorPrincipalId, name)
  scope: containers[index]
  properties: {
    principalId: uploadOperatorPrincipalId
    principalType: uploadOperatorPrincipalType
    roleDefinitionId: index == 2 ? blobReader : blobContributor
  }
}]
var projectParts = split(projectResourceId, '/')
module projectAccess 'project-access.bicep' = {
  name: 'flow-project-access'
  scope: resourceGroup(projectParts[2], projectParts[4])
  params: {
    accountName: projectParts[8]
    projectName: projectParts[10]
    adapterPrincipalId: adapter.identity.principalId
    workflowPrincipalId: workflow.identity.principalId
  }
}

output storageUrl string = data.properties.primaryEndpoints.blob
output workflowName string = workflow.name
output adapterName string = adapter.name
output workflowPrincipalId string = workflow.identity.principalId
output adapterPrincipalId string = adapter.identity.principalId
output publisherConfig object = {
  serverUrl: 'https://${adapter.name}.azurewebsites.net'
  audience: adapterAudience
}
output deploymentState string = 'Foundation only; deploy workflow/runtime packages and verify native tools, Eventstream and Fabric separately.'
