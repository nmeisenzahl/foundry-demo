provider "azurerm" {
  features {}

  subscription_id                 = var.subscription_id
  resource_provider_registrations = "none"
}

provider "azapi" {
  subscription_id = data.azurerm_client_config.current.subscription_id
}

data "azurerm_client_config" "current" {}
