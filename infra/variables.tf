variable "subscription_id" {
  description = "Azure subscription ID used for the local deployment."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be a valid UUID."
  }
}

variable "location" {
  description = "Azure region that offers the selected model, version, SKU, and capacity."
  type        = string

  validation {
    condition     = trimspace(var.location) != "" && !startswith(var.location, "<")
    error_message = "location must be an explicit Azure region."
  }
}

variable "project_name" {
  description = "Lowercase project prefix used in every Azure resource name."
  type        = string

  validation {
    condition = (
      length(var.project_name) >= 2 &&
      length(var.project_name) <= 20 &&
      can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", var.project_name))
    )
    error_message = "project_name must be 2-20 lowercase letters, digits, or hyphens and cannot start or end with a hyphen."
  }
}

variable "github_repository" {
  description = "GitHub repository in owner/name form trusted for workload identity federation."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must use owner/name form."
  }
}

variable "github_environment_name" {
  description = "GitHub Environment trusted to deploy this Terraform environment."
  type        = string
  default     = "dev"

  validation {
    condition     = trimspace(var.github_environment_name) != "" && var.github_environment_name == trimspace(var.github_environment_name)
    error_message = "github_environment_name cannot be blank or include leading/trailing whitespace."
  }
}

variable "model_name" {
  description = "Exact model name offered in the selected Azure region."
  type        = string

  validation {
    condition     = trimspace(var.model_name) != "" && !startswith(var.model_name, "<")
    error_message = "model_name must be explicitly set."
  }
}

variable "model_version" {
  description = "Exact model version offered in the selected Azure region."
  type        = string

  validation {
    condition     = trimspace(var.model_version) != "" && !startswith(var.model_version, "<")
    error_message = "model_version must be explicitly set."
  }
}

variable "model_sku_name" {
  description = "Deployment SKU offered for the selected model and region."
  type        = string

  validation {
    condition     = trimspace(var.model_sku_name) != "" && !startswith(var.model_sku_name, "<")
    error_message = "model_sku_name must be explicitly set."
  }
}

variable "model_capacity" {
  description = "Positive integer capacity for the model deployment."
  type        = number

  validation {
    condition     = var.model_capacity > 0 && floor(var.model_capacity) == var.model_capacity
    error_message = "model_capacity must be a positive integer."
  }
}

variable "github_oidc_subject_prefix" {
  description = "Subject claim prefix GitHub presents for this repository. Leave null while the repository emits mutable owner/name subjects. Set it to the exact `sub_claim_prefix` reported by `gh api repos/<owner>/<name>/actions/oidc/customization/sub` once immutable subject claims are enabled."
  type        = string
  default     = null

  validation {
    condition     = var.github_oidc_subject_prefix == null || can(regex("^repo:[^:]+$", var.github_oidc_subject_prefix))
    error_message = "github_oidc_subject_prefix must look like \"repo:<owner>@<owner_id>/<name>@<repo_id>\"."
  }
}

variable "operator_object_ids" {
  description = "Entra ID object IDs granted operator access to the Foundry project and container registry. Supplied by environment so local and CI runs plan identically."
  type        = list(string)

  validation {
    condition = alltrue([
      for id in var.operator_object_ids :
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))
    ])
    error_message = "operator_object_ids entries must be valid UUIDs."
  }
}

variable "hosted_agent_object_ids" {
  description = <<-DESC
    Entra ID object IDs of the per-agent identities Foundry creates for hosted
    agents, keyed by agent name. Foundry mints these lazily when an agent is
    first created, so Terraform cannot derive them: reading them back would
    need Microsoft Graph directory permissions that the deploying workload
    identity is not granted. They are supplied explicitly, like
    operator_object_ids. Read one with:
      az ad sp list --filter "displayName eq '<account>-<project>-<agent>-AgentIdentity'" --query "[0].id" -o tsv
  DESC
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for id in values(var.hosted_agent_object_ids) :
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", id))
    ])
    error_message = "hosted_agent_object_ids values must be valid UUIDs."
  }
}
