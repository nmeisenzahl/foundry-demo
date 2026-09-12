output "resource_group_name" {
  description = "Name of the Terraform-managed resource group."
  value       = azurerm_resource_group.main.name
}

output "foundry_account_id" {
  description = "Resource ID of the Microsoft Foundry account."
  value       = azurerm_cognitive_account.main.id
}

output "foundry_account_name" {
  description = "Name of the Microsoft Foundry account."
  value       = azurerm_cognitive_account.main.name
}

output "foundry_account_endpoint" {
  description = "Base endpoint exposed by the Microsoft Foundry account."
  value       = azurerm_cognitive_account.main.endpoint
}

output "foundry_project_id" {
  description = "Resource ID of the Microsoft Foundry project."
  value       = azurerm_cognitive_account_project.main.id
}

output "foundry_project_name" {
  description = "Name of the Microsoft Foundry project."
  value       = azurerm_cognitive_account_project.main.name
}

output "foundry_project_endpoints" {
  description = "Endpoint map returned by Azure for the Microsoft Foundry project."
  value       = azurerm_cognitive_account_project.main.endpoints
}

output "model_deployment_name" {
  description = "Name of the deployed model alias."
  value       = azurerm_cognitive_deployment.chat.name
}

output "application_insights_id" {
  description = "Resource ID of the connected Application Insights instance."
  value       = azurerm_application_insights.main.id
}

output "container_registry_name" {
  description = "Name of the Azure Container Registry used for hosted agents."
  value       = azurerm_container_registry.main.name
}

output "container_registry_login_server" {
  description = "Login server of the Azure Container Registry used for hosted agents."
  value       = azurerm_container_registry.main.login_server
}

output "architecture_advisor_repository" {
  description = "Repository name used for the architecture advisor image."
  value       = local.names.architecture_advisor_repository
}

output "incident_triage_repository" {
  description = "Repository name used for the incident triage image."
  value       = local.names.incident_triage_repository
}

output "github_actions_environment_variables" {
  description = "Seven non-sensitive GitHub Environment variables for deployment authentication and runtime configuration."
  value       = local.github_actions_environment_variables
}

output "github_actions_repository_variables" {
  description = "Non-sensitive repository-level GitHub Actions variables for aggregation jobs without a GitHub Environment."
  value       = local.github_actions_repository_variables
}
