# Operator access is pinned to explicit object IDs rather than
# data.azurerm_client_config.current, so the principal that happens to run
# Terraform never becomes the operator. Without this, the first CI apply would
# revoke the human operator's Foundry access and hand it to the service
# principal running the workflow.
resource "azurerm_role_assignment" "operator_foundry_user" {
  for_each = toset(var.operator_object_ids)

  scope              = azurerm_cognitive_account_project.main.id
  role_definition_id = local.role_definition_ids.foundry_user
  principal_id       = each.value
}

# Hosted agents that call model inference directly (rather than through the
# Agents API) authenticate against the account endpoint, and a project-scope
# assignment does not inherit upward to the account. Operators need the same
# grant to run those agents locally: subscription Owner is a control-plane role
# and conveys no Cognitive Services data actions.
resource "azurerm_role_assignment" "operator_foundry_account_user" {
  for_each = toset(var.operator_object_ids)

  scope              = azurerm_cognitive_account.main.id
  role_definition_id = local.role_definition_ids.foundry_user
  principal_id       = each.value
}

resource "azurerm_role_assignment" "project_foundry_user" {
  scope                            = azurerm_cognitive_account.main.id
  role_definition_id               = local.role_definition_ids.foundry_user
  principal_id                     = azurerm_cognitive_account_project.main.identity[0].principal_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "operator_foundry_project_manager" {
  for_each = toset(var.operator_object_ids)

  scope                = azurerm_cognitive_account_project.main.id
  role_definition_name = "Foundry Project Manager"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "operator_acr_writer" {
  for_each = toset(var.operator_object_ids)

  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = each.value
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

# A hosted agent container does not run as the project's managed identity: it
# runs as a per-agent identity Foundry mints for it (the `instance_identity` on
# the agent resource). Anything the container calls itself therefore needs its
# own grant. Agents that reach the model through the project endpoint, and
# agents that call inference on the account endpoint directly, are both covered
# here because an account-scope assignment inherits down to the project.
resource "azurerm_role_assignment" "hosted_agent_foundry_user" {
  for_each = var.hosted_agent_object_ids

  scope                            = azurerm_cognitive_account.main.id
  role_definition_id               = local.role_definition_ids.foundry_user
  principal_id                     = each.value
  skip_service_principal_aad_check = true
}

# Same identity, same reason: the container exports its own traces and live
# metrics, so publishing rights on the project's managed identity do not help
# it. Without this the container logs a Forbidden from the exporter on every
# collection interval, which buries the failures worth reading.
resource "azurerm_role_assignment" "hosted_agent_monitoring_metrics_publisher" {
  for_each = var.hosted_agent_object_ids

  scope                            = azurerm_application_insights.main.id
  role_definition_id               = local.role_definition_ids.monitoring_metrics_publisher
  principal_id                     = each.value
  skip_service_principal_aad_check = true
}
