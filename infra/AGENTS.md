# Infrastructure Guide

- Run Terraform from the repository root with `terraform -chdir=infra`.
- Format with `terraform -chdir=infra fmt -recursive` and validate after changes.
- Keep provider versions constrained in `versions.tf` and shared names and tags in `locals.tf`.
- Never commit `terraform.tfstate`, plans, or `.terraform/`.
- `infra/env/dev.tfvars` is the one tracked variable file and must stay
  secret-free. `subscription_id` and `operator_object_ids` come from the
  environment so local and CI runs plan identically.
- State is remote (`backend.tf`); `terraform init` needs an authenticated
  Azure session even for a plan.
- Preserve managed-identity authentication and least-privilege RBAC patterns.
