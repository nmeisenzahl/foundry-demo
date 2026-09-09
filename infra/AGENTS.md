# Infrastructure Guide

- Run Terraform from the repository root with `terraform -chdir=infra`.
- Format with `terraform -chdir=infra fmt -recursive` and validate after changes.
- Keep provider versions constrained in `versions.tf` and shared names and tags in `locals.tf`.
- Never commit `terraform.tfstate`, plans, `.terraform/`, or real environment tfvars.
- Preserve managed-identity authentication and least-privilege RBAC patterns.
