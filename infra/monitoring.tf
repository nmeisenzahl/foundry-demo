resource "azurerm_log_analytics_workspace" "main" {
  name                           = local.names.log_analytics_workspace
  location                       = azurerm_resource_group.main.location
  resource_group_name            = azurerm_resource_group.main.name
  sku                            = "PerGB2018"
  retention_in_days              = 30
  local_authentication_enabled   = false
  internet_ingestion_access_type = "Enabled"
  internet_query_access_type     = "Enabled"
  tags                           = local.tags
}

resource "azurerm_application_insights" "main" {
  name                         = local.names.application_insights
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  workspace_id                 = azurerm_log_analytics_workspace.main.id
  application_type             = "web"
  local_authentication_enabled = false
  internet_ingestion_enabled   = true
  internet_query_enabled       = true
  tags                         = local.tags
}

# The Foundry service and official Terraform sample support ProjectManagedIdentity,
# but the generated generic ARM schema does not consistently expose it.
resource "azapi_resource" "application_insights_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2026-03-01"
  name                      = local.names.application_insights_connection
  parent_id                 = azurerm_cognitive_account_project.main.id
  schema_validation_enabled = false

  depends_on = [
    azurerm_role_assignment.project_monitoring_metrics_publisher
  ]

  body = {
    properties = {
      category      = "AppInsights"
      target        = azurerm_application_insights.main.id
      authType      = "ProjectManagedIdentity"
      isSharedToAll = false
      metadata = {
        ApiType                             = "Azure"
        ResourceId                          = azurerm_application_insights.main.id
        ApplicationInsightsConnectionString = azurerm_application_insights.main.connection_string
      }
    }
  }
}
