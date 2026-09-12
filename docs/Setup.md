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

`infra/env/dev.tfvars` is tracked in git. It holds only reviewed, non-secret
configuration: location, project name, model settings, and the GitHub
repository and OIDC subject prefix. Never add credentials, tokens, or keys to
it. `infra/env/terraform.tfvars.example` documents the shape for a new
environment.

Two variables are deliberately absent from that file and supplied by the
environment instead, so that a local run and a GitHub Actions run produce
identical plans:

```bash
export TF_VAR_subscription_id="$(az account show --query id -o tsv)"
export TF_VAR_operator_object_ids="[\"$(az ad signed-in-user show --query id -o tsv)\"]"
```

`operator_object_ids` controls who receives Foundry User, Foundry Project
Manager, and AcrPush on the project and registry. It has no default on
purpose: an empty value would silently plan a destroy of those grants and
revoke human access. Every principal that needs operator access must appear in
the list.

Provision the control plane:

```bash
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init
terraform -chdir=infra validate
terraform -chdir=infra plan -var-file=env/dev.tfvars
terraform -chdir=infra apply -var-file=env/dev.tfvars
terraform -chdir=infra output
```

`terraform init` contacts the remote backend described in
[Remote State](#remote-state), so an authenticated Azure CLI session is
required even for a plan.

Provider auto-registration is disabled. A subscription administrator must
register the required providers before the first apply. Azure role assignments
are eventually consistent; if the Application Insights connection initially
returns `403`, wait for propagation and run the apply again.

Public networking is enabled for this demo. It is not intended as a general
production default.

## Remote State

Terraform state lives in Azure Blob Storage with Entra ID authentication and
blob leasing for locking. `infra/backend.tf` pins the location:

| Item | Name |
| --- | --- |
| Resource group | `foundrydemo-state-rg` |
| Storage account | `foundrydemostate` |
| Blob container | `tfstate` |
| State key | `foundry-demo/dev.tfstate` |

These are resource identifiers, not credentials. The container allows no
anonymous access, shared keys are never used, and the subscription ID stays
out of the repository.

### One-Time Bootstrap

State storage and the service principal that GitHub Actions runs Terraform as
are provisioned by
[whiteducksoftware/terraform-scaffold-for-azure][tf-scaffold], using its OIDC
option (repository root, not `client-secret/`). This is a one-time local step
requiring subscription Owner, or Contributor plus Role Based Access Control
Administrator, plus the Entra ID Application Developer role.

[tf-scaffold]: https://github.com/whiteducksoftware/terraform-scaffold-for-azure

Clone the scaffold and replace its `.env` with explicit names, so the state
resources follow this project's convention rather than the scaffold's
`stac0${name}0${suffix}` templates:

```bash
name="foundrydemo"
suffix="dev"
location="swedencentral"

spName="foundrydemo-dev-tf"
rg="foundrydemo-state-rg"
tag="$suffix"
saName="foundrydemostate"
scName="tfstate"

saSku="Standard_ZRS"
```

Keep `FEDERATED_CREDENTIAL_FILE="federated_credential_github.json"` in `up.sh`,
and set that file's `subject` to the immutable subject for this repository and
the `dev` environment:

```text
repo:nmeisenzahl@28020936/foundry-demo@1362938755:environment:dev
```

The prefix must match what GitHub actually emits. Confirm it before running:

```bash
gh api repos/nmeisenzahl/foundry-demo/actions/oidc/customization/sub \
  --jq '{use_immutable_subject, sub_claim_prefix}'
```

Run `up.sh`. It grants the service principal Contributor, Monitoring Metrics
Publisher, and a conditioned Role Based Access Control Administrator at
subscription scope, plus Storage Blob Data Owner on the state account. The
condition forbids assigning privileged roles; none of the roles this project
assigns (Foundry User, Foundry Project Manager, AcrPush, AcrPull, Monitoring
Metrics Publisher) is affected.

Skip two of the scaffold's steps. The Microsoft Graph permissions
(`Group.ReadWrite.All`, `GroupMember.ReadWrite.All`, `User.Read.All`) and the
admin-consent step exist so Terraform can manage Entra ID objects. This project
declares only the `azapi`, `azurerm`, and `random` providers and never touches
Entra ID.

The scaffold enables 30-day soft delete for blobs and containers on the state
account. That is the recovery path for a corrupted or deleted state file.

### Operator Access to the State Container

The scaffold grants Storage Blob Data Owner to the service principal only.
`use_azuread_auth = true` routes every state read and write through Entra ID
instead of shared keys, and subscription Owner does not confer blob data-plane
access. Each operator who runs Terraform locally therefore needs an explicit
grant, or `terraform init` fails with a 403 from the blob endpoint:

```bash
az role assignment create \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Storage Blob Data Owner" \
  --scope "/subscriptions/$(az account show --query id -o tsv)/resourceGroups/foundrydemo-state-rg/providers/Microsoft.Storage/storageAccounts/foundrydemostate"
```

### Migrating Existing Local State

Run once, from a workstation that already holds the local state:

```bash
terraform -chdir=infra init -migrate-state
terraform -chdir=infra plan -var-file=env/dev.tfvars
```

The plan must report no unexpected changes. Then delete
`infra/terraform.tfstate`, `infra/terraform.tfstate.backup`, and any local
plan files.

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

Two further `dev` Environment variables are set by hand, because Terraform
cannot produce them. `AZURE_TERRAFORM_CLIENT_ID` identifies the principal
Terraform itself runs as, and `AZURE_OPERATOR_OBJECT_IDS` is an input to
Terraform rather than an output. Both are non-sensitive and stored as
variables, not secrets.

```bash
az ad sp list --display-name foundrydemo-dev-tf --query "[0].appId" -o tsv
az ad signed-in-user show --query id -o tsv
```

| Variable | Value |
| --- | --- |
| `AZURE_TERRAFORM_CLIENT_ID` | `appId` of `foundrydemo-dev-tf` |
| `AZURE_OPERATOR_OBJECT_IDS` | JSON array, for example `["<object-id>"]` |

Do not add a deployment branch restriction to `dev`. The Terraform plan job
runs from pull request branches and declares `environment: dev` to obtain its
federated credential, so a restriction to `main` would block every plan. If a
restriction becomes necessary, the plan job must first be moved onto a second
federated credential with a `pull_request` subject.

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

Terraform state is held in the remote backend described in
[Remote State](#remote-state). State and connection metadata can contain
sensitive values, so the container is RBAC-only and never read through shared
keys.

Never commit or publish:

- `terraform.tfstate` or state backups left over from before the migration
- Terraform plans
- `.terraform/`
- `.env` files
- Generated deployment records or image environment files

`infra/env/dev.tfvars` is the one tracked variable file. It may contain only
reviewed, non-secret environment configuration. Never place credentials,
tokens, keys, or other secrets in a tfvars file; `subscription_id` and
`operator_object_ids` are supplied through the environment instead.

This repository is public, and so are its Actions job summaries. The Terraform
plan published to a job summary exposes resource IDs, the Foundry endpoint,
and the registry login server, all of which are already published as `dev`
Environment variables. Terraform renders sensitive attributes as
`(sensitive value)`, and `local_auth_enabled = false` keeps account keys out
of state entirely.

## Cleanup

Remove the provisioned resources with:

```bash
terraform -chdir=infra destroy -var-file=env/dev.tfvars
```
