locals {
  tags = {
    "managed-by" = data.azurerm_client_config.current.client_id
  }

  names = {
    resource_group                  = "${var.project_name}-rg"
    foundry_account                 = "${var.project_name}-ai-${random_string.suffix.result}"
    foundry_project                 = "${var.project_name}-dev"
    model_deployment                = "${var.project_name}-chat-model"
    log_analytics_workspace         = "${var.project_name}-law"
    application_insights            = "${var.project_name}-appi"
    application_insights_connection = "${var.project_name}-appi"
    container_registry              = "${replace(var.project_name, "-", "")}acr${random_string.suffix.result}"
    architecture_advisor_repository = "architecture-advisor"
  }

  role_definition_ids = {
    foundry_user                 = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d"
    monitoring_metrics_publisher = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/3913510d-42f4-4e42-8a64-420c390055eb"
  }
}
