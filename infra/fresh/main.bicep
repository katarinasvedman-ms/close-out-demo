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

@description('Existing single-tenant Entra API registration client ID, with identifier URI api://<client-id>. Create manually before deployment; no secret is required for this registration.')
@minLength(36)
@maxLength(36)
param flowApiClientId string
@description('Exact object ID of the approved Fabric callback caller. This is not a connection ID or application client ID.')
@minLength(36)
@maxLength(36)
param fabricDispatchPrincipalId string
@description('Existing Fabric Eventstream custom endpoint namespace FQDN; no Fabric resource is provisioned.')
@minLength(1)
param eventstreamNamespace string
@description('Existing Fabric Eventstream custom endpoint entity name.')
@minLength(1)
param eventstreamEntity string

var operatorPrincipalId = deployer().objectId
var tags = { 'azd-env-name': foundryName }

module foundation './foundation.bicep' = {
  name: 'close-out-foundation'
  params: {
    foundryName: foundryName
    projectName: projectName
    location: location
    searchLocation: searchLocation
    modelDeploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    modelCapacity: modelCapacity
    operatorPrincipalType: operatorPrincipalType
  }
}

module flow '../flow/main.bicep' = {
  name: 'close-out-playground-flow'
  params: {
    environmentName: take(foundryName, 24)
    location: location
    projectResourceId: foundation.outputs.projectResourceId
    adapterClientId: flowApiClientId
    adapterAudience: 'api://${flowApiClientId}'
    fabricDispatchPrincipalId: fabricDispatchPrincipalId
    enableCoordinatorDispatch: false
    enableBlobValidation: false
    uploadOperatorPrincipalId: operatorPrincipalId
    uploadOperatorPrincipalType: operatorPrincipalType
    publisherPrincipalId: foundation.outputs.projectPrincipalId
    eventstreamNamespace: eventstreamNamespace
    eventstreamEntity: eventstreamEntity
    allowPublicNetworkAccess: true
    tags: tags
    foundryConfig: {
      endpoint: foundation.outputs.projectEndpoint
      deployment: foundation.outputs.modelDeploymentName
      model: modelName
      modelVersion: modelVersion
      validation: { name: 'close-out-validation', version: 'pending-publication', definitionHash: 'pending-publication' }
      closeout: { name: 'close-out-coordinator', version: 'pending-publication', definitionHash: 'pending-publication' }
    }
  }
}

output resourceGroupName string = foundation.outputs.resourceGroupName
output subscriptionId string = foundation.outputs.subscriptionId
output tenantId string = foundation.outputs.tenantId
output foundryName string = foundation.outputs.foundryName
output projectName string = foundation.outputs.projectName
output projectResourceId string = foundation.outputs.projectResourceId
output projectPrincipalId string = foundation.outputs.projectPrincipalId
output projectEndpoint string = foundation.outputs.projectEndpoint
output modelDeploymentName string = foundation.outputs.modelDeploymentName
output modelName string = foundation.outputs.modelName
output modelVersion string = foundation.outputs.modelVersion
output searchEndpoint string = foundation.outputs.searchEndpoint
output storageEndpoint string = foundation.outputs.storageEndpoint
output storageResourceId string = foundation.outputs.storageResourceId
output containerName string = foundation.outputs.containerName
output connectionName string = foundation.outputs.connectionName
output knowledgeBaseName string = foundation.outputs.knowledgeBaseName
output knowledgeModel object = foundation.outputs.knowledgeModel
output applicationInsightsResourceId string = foundation.outputs.applicationInsightsResourceId
output flow object = {
  workflowName: flow.outputs.workflowName
  adapterName: flow.outputs.adapterName
  storageUrl: flow.outputs.storageUrl
  workflowPrincipalId: flow.outputs.workflowPrincipalId
  adapterPrincipalId: flow.outputs.adapterPrincipalId
  publisherConfig: flow.outputs.publisherConfig
}
@description('Run after ARM succeeds. Publishes both agent definitions, native IQ and the runtime/workflow ZIPs. No inference or Fabric item deployment. Complete manual grants, documents, callback and enablement before use.')
output publishDefinitionsCommand string = 'uv run --frozen --extra agents --extra flow python -m agents.bootstrap --resource-group "${resourceGroup().name}" --deployment "${deployment().name}" --create-fabric-connection --publish-flow'
