resource "random_string" "suffix" {
  length  = 5
  upper   = false
  lower   = true
  numeric = true
  special = false
}

resource "azurerm_resource_group" "main" {
  name     = local.names.resource_group
  location = var.location
  tags     = local.tags
}

resource "azurerm_container_registry" "main" {
  name                          = local.names.container_registry
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  sku                           = "Standard"
  admin_enabled                 = false
  public_network_access_enabled = true
  tags                          = local.tags
}

resource "azurerm_cognitive_account" "main" {
  name                          = local.names.foundry_account
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  kind                          = "AIServices"
  sku_name                      = "S0"
  custom_subdomain_name         = local.names.foundry_account
  project_management_enabled    = true
  local_auth_enabled            = false
  public_network_access_enabled = true

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

resource "azurerm_cognitive_deployment" "chat" {
  name                   = local.names.model_deployment
  cognitive_account_id   = azurerm_cognitive_account.main.id
  version_upgrade_option = "NoAutoUpgrade"

  model {
    format  = "OpenAI"
    name    = var.model_name
    version = var.model_version
  }

  sku {
    name     = var.model_sku_name
    capacity = var.model_capacity
  }
}

resource "azurerm_cognitive_account_project" "main" {
  name                 = local.names.foundry_project
  cognitive_account_id = azurerm_cognitive_account.main.id
  location             = azurerm_resource_group.main.location
  display_name         = local.names.foundry_project
  description          = "Development project for ${var.project_name}."

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}
