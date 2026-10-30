param accountName string
param projectName string
param adapterPrincipalId string
param workflowPrincipalId string

resource account 'Microsoft.CognitiveServices/accounts@2025-10-01-preview' existing = {
  name: accountName
  resource project 'projects' existing = {
    name: projectName
  }
}
var userRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
resource access 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for principal in [adapterPrincipalId, workflowPrincipalId]: {
  name: guid(account::project.id, principal, userRole)
  scope: account::project
  properties: {
    principalId: principal
    principalType: 'ServicePrincipal'
    roleDefinitionId: userRole
  }
}]
