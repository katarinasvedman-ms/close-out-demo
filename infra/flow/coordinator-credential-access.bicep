targetScope = 'resourceGroup'

@minLength(1)
param vaultName string
@description('Only the new delegated coordinator credential; no vault-wide grant.')
@minLength(1)
param secretName string
@minLength(36)
@maxLength(36)
param workflowPrincipalId string

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: vaultName
}
resource credential 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: secretName
}
resource rotationAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(credential.id, workflowPrincipalId, 'coordinator-credential-rotation')
  scope: credential
  properties: {
    principalId: workflowPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  }
}

output credentialUri string = '${vault.properties.vaultUri}secrets/${secretName}'
