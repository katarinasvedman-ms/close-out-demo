targetScope = 'resourceGroup'

param accountName string
param deploymentName string
param modelName string
param modelVersion string
@minValue(1)
param capacity int = 30

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: accountName
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: deploymentName
  sku: {
    name: 'Standard'
    capacity: capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    raiPolicyName: 'Microsoft.DefaultV2'
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

output deploymentId string = model.id
output deploymentName string = model.name
