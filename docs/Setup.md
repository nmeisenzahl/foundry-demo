# Setup and Infrastructure

This guide covers local prerequisites, Azure provisioning, and runtime
configuration. See [Agents](Agents.md) for agent-specific development and
deployment.

## Prerequisites

- Python `>= 3.11, < 3.12`
- `uv >= 0.4`
- Terraform `1.16.1`
- Azure CLI authenticated to the target subscription
- Docker for hosted-agent image builds
- Permission to create Azure resources and role assignments, including
  `Microsoft.Authorization/roleAssignments/write`
- Registered providers: `Microsoft.CognitiveServices`,
  `Microsoft.OperationalInsights`, `Microsoft.Insights`, and
  `Microsoft.Authorization`, and `Microsoft.ContainerRegistry`

The root project uses Python 3.11. The standalone hosted runtime under
`src/agents/architecture_advisor/` has its own Python 3.13 environment.

## Azure Login and Capacity

Sign in and confirm the active subscription:

```bash
az login
az account show \
  --query "{subscription:name, subscriptionId:id, tenantId:tenantId}" \
  -o table
```

Check model availability and quota for the location selected in your Terraform
variables:

```bash
az cognitiveservices model list \
  --location swedencentral \
  --query "[?model.format=='OpenAI'].{Name:model.name,Version:model.version,Sku:model.skus}" \
  -o table
az cognitiveservices usage list --location swedencentral -o table
```

## Terraform Configuration

Terraform under `infra/` provisions:

- A resource group, Foundry account, and Foundry project
- A pinned model deployment with `NoAutoUpgrade`
- Azure Container Registry with admin credentials disabled
- Log Analytics and keyless Application Insights monitoring
- Operator and project-identity RBAC

Copy `infra/env/terraform.tfvars.example` to a reviewed environment file such
as `infra/env/dev.tfvars`, then provide the target subscription, location,
project name, and model settings. Terraform variable files are intentionally
eligible for source control. Keep them free of secrets; use secure CI variables,
environment variables, or a secret store for sensitive values.

Provision the control plane:

```bash
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init
terraform -chdir=infra validate
terraform -chdir=infra plan -var-file=env/dev.tfvars
terraform -chdir=infra apply -var-file=env/dev.tfvars
terraform -chdir=infra output
```

Provider auto-registration is disabled. A subscription administrator must
register the required providers before the first apply. Azure role assignments
are eventually consistent; if the Application Insights connection initially
returns `403`, wait for propagation and run the apply again.

Public networking is enabled for this demo. It is not intended as a general
production default.

## GitHub Actions OIDC Bootstrap (dev)

The reviewed tfvars file must include the repository and deployment environment
trusted by workload identity federation:

```hcl
github_repository       = "octo-org/foundry-demo"
github_environment_name = "dev"
```

`github_environment_name` is part of the federated identity subject:
`repo:<owner>/<repo>:environment:<environment>`. If the GitHub Environment name
does not match Terraform, Azure login from Actions will be rejected.

After setting those values, run the existing local apply:

```bash
terraform -chdir=infra apply -var-file=env/dev.tfvars
```

Then export the reviewed GitHub configuration variables by scope:

```bash
terraform -chdir=infra output -json github_actions_environment_variables
terraform -chdir=infra output -json github_actions_repository_variables
```

Create a GitHub Environment named `dev` in the repository, then add every value
returned in `github_actions_environment_variables` as an **Environment
variable** (repository **Settings > Environments > dev > Variables**). Do not
store these values as secrets.

The seven Environment variable names returned by Terraform are:

- `AZURE_SUBSCRIPTION_ID`
- `AZURE_TENANT_ID`
- `AZURE_AGENT_DELIVERY_CLIENT_ID`
- `FOUNDRY_PROJECT_ENDPOINT`
- `FOUNDRY_MODEL_DEPLOYMENT_NAME`
- `ACR_NAME`
- `ACR_LOGIN_SERVER`

In the same `dev` environment configuration, set deployment branch restrictions
to `main` and add any required reviewers used for your release policy.

Add `APPLICATION_INSIGHTS_PORTAL_URL` from
`github_actions_repository_variables` as a **repository-level Actions
variable** (**Settings > Secrets and variables > Actions > Variables**), not
only as a `dev` Environment variable. Release aggregation has no GitHub
Environment and cannot read Environment-scoped variables. This monitoring
link is non-sensitive and optional: if absent, aggregation omits
`--monitoring-url`, records `monitoring_url: null`, and reports monitoring as
not configured without failing manifest generation.

## Runtime Environment

The project API endpoint is exported by Terraform under the `AI Foundry API`
key:

```bash
export FOUNDRY_PROJECT_ENDPOINT="$(
  terraform -chdir=infra output -json foundry_project_endpoints \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["AI Foundry API"])'
)"
export FOUNDRY_MODEL_DEPLOYMENT_NAME="$(
  terraform -chdir=infra output -raw model_deployment_name
)"
```

Optional settings:

```bash
# Omit by default for model stability.
export FOUNDRY_AGENT_TEMPERATURE="0.2"

# Defaults to artifacts/deployments.
export FOUNDRY_DEPLOYMENT_RECORD_DIR="$PWD/artifacts/deployments"
```

Hosted-agent publishing also generates environment variables containing a
unique fully qualified tagged image reference and a separate immutable digest.
Delivery converts these to `repository@digest` for the candidate and evidence.
Follow [Agents](Agents.md#deploy-the-hosted-agent)
to produce and load that file.

## Local Development

Install the root dependencies and run the existing checks:

```bash
uv sync --dev
uv run ruff check .
uv run pytest -q
```

Show the deployment CLI help or list registered agents without contacting
Azure:

```bash
uv run deploy-agent --help
uv run deploy-agent --list
```

## State and Secret Safety

Terraform currently uses local state. State and connection metadata can contain
sensitive values.

Never commit or publish:

- `terraform.tfstate` or state backups
- Terraform plans
- `.terraform/`
- `.env` files
- Generated deployment records or image environment files

Terraform variable files may be committed when they contain only reviewed,
non-secret environment configuration. Never place credentials, tokens, keys,
or other secrets in a tfvars file.

Use a secured remote backend before shared or automated use.

## Cleanup

Remove the provisioned resources with:

```bash
terraform -chdir=infra destroy -var-file=env/dev.tfvars
```
