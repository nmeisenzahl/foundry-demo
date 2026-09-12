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
    github_agent_delivery           = "${var.project_name}-dev-gh-delivery"
    architecture_advisor_repository = "architecture-advisor"
  }

  # Repositories with immutable subject claims present
  # repo:<owner>@<owner_id>/<name>@<repo_id> instead of repo:<owner>/<name>, so the
  # trusted subject has to mirror whatever prefix the repository actually emits.
  github_oidc_subject_prefix = coalesce(var.github_oidc_subject_prefix, "repo:${var.github_repository}")
  github_oidc_subject        = "${local.github_oidc_subject_prefix}:environment:${var.github_environment_name}"

  github_actions_environment_variables = {
    AZURE_SUBSCRIPTION_ID          = data.azurerm_client_config.current.subscription_id
    AZURE_TENANT_ID                = data.azurerm_client_config.current.tenant_id
    AZURE_AGENT_DELIVERY_CLIENT_ID = azurerm_user_assigned_identity.github_agent_delivery.client_id
    FOUNDRY_PROJECT_ENDPOINT       = azurerm_cognitive_account_project.main.endpoints["AI Foundry API"]
    FOUNDRY_MODEL_DEPLOYMENT_NAME  = azurerm_cognitive_deployment.chat.name
    ACR_NAME                       = azurerm_container_registry.main.name
    ACR_LOGIN_SERVER               = azurerm_container_registry.main.login_server
  }

  github_actions_repository_variables = {
    APPLICATION_INSIGHTS_PORTAL_URL = "https://portal.azure.com/#@/resource${azurerm_application_insights.main.id}/overview"
  }

  role_definition_ids = {
    foundry_user                 = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d"
    monitoring_metrics_publisher = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/3913510d-42f4-4e42-8a64-420c390055eb"
  }
}
