resource "azurerm_role_assignment" "operator_foundry_user" {
  scope              = azurerm_cognitive_account_project.main.id
  role_definition_id = local.role_definition_ids.foundry_user
  principal_id       = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "project_foundry_user" {
  scope                            = azurerm_cognitive_account.main.id
  role_definition_id               = local.role_definition_ids.foundry_user
  principal_id                     = azurerm_cognitive_account_project.main.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "operator_foundry_project_manager" {
  scope                = azurerm_cognitive_account_project.main.id
  role_definition_name = "Foundry Project Manager"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "operator_acr_writer" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "project_acr_reader" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_cognitive_account_project.main.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "project_monitoring_metrics_publisher" {
  scope                            = azurerm_application_insights.main.id
  role_definition_id               = local.role_definition_ids.monitoring_metrics_publisher
  principal_id                     = azurerm_cognitive_account_project.main.identity[0].principal_id
  skip_service_principal_aad_check = true
}
