# State lives in the storage account provisioned by
# https://github.com/whiteducksoftware/terraform-scaffold-for-azure.
# Names are resource identifiers, not credentials: the container allows no
# anonymous access and is read only through Entra ID authentication. The
# subscription ID is supplied by the environment and stays out of the tree.
terraform {
  backend "azurerm" {
    resource_group_name  = "foundrydemo-state-rg"
    storage_account_name = "foundrydemostate"
    container_name       = "tfstate"
    key                  = "foundry-demo/dev.tfstate"
    use_azuread_auth     = true
  }
}
