resource "azurerm_user_assigned_identity" "github_agent_delivery" {
  name                = local.names.github_agent_delivery
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "github_agent_delivery" {
  name                      = "github-${var.github_environment_name}"
  user_assigned_identity_id = azurerm_user_assigned_identity.github_agent_delivery.id
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = local.github_oidc_subject
  audience                  = ["api://AzureADTokenExchange"]
}

# The delivery workflow builds, pushes, and deploys in one job under a single
# federated subject, so image publishing and agent delivery share one identity
# rather than implying a privilege boundary that does not exist.
resource "azurerm_role_assignment" "github_agent_delivery_acr_push" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPush"
  principal_id                     = azurerm_user_assigned_identity.github_agent_delivery.principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "github_agent_delivery_foundry_user" {
  scope                            = azurerm_cognitive_account_project.main.id
  role_definition_id               = local.role_definition_ids.foundry_user
  principal_id                     = azurerm_user_assigned_identity.github_agent_delivery.principal_id
  skip_service_principal_aad_check = true
}
