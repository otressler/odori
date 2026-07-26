@description('Azure region for the Foundry resource.')
param location string = resourceGroup().location

@description('Globally unique Azure AI Services account name.')
param accountName string

@description('Name exposed to Odori as AZURE_OPENAI_EMBEDDING_DEPLOYMENT.')
param embeddingDeploymentName string = 'text-embedding-3-small'

@description('Embedding model version available in the selected region.')
param embeddingModelVersion string

@description('Embedding deployment capacity in thousands of tokens per minute.')
param embeddingCapacity int = 1

@description('Azure OpenAI deployment SKU available for the selected region.')
param embeddingSku string = 'GlobalStandard'

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: accountName
  location: location
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    customSubDomainName: accountName
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: account
  name: embeddingDeploymentName
  sku: {
    name: embeddingSku
    capacity: embeddingCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: embeddingModelVersion
    }
  }
}

output endpoint string = account.properties.endpoint
output embeddingDeployment string = embeddingDeployment.name
