# Microsoft Foundry SDK-First Agent Delivery Demo

A version-safe delivery reference for Microsoft Foundry prompt and hosted
agents. Terraform owns the Azure control plane, while a Python SDK delivery
package owns typed agent definitions, immutable versions, smoke validation,
release records, and endpoint promotion.

## Demos

| Demo | Kind | What it shows | Source | Guide |
| --- | --- | --- | --- | --- |
| `research-assistant` | Prompt | Web Search with citation validation | [`src/agents/research_assistant/`](src/agents/research_assistant/) | [Add a Prompt Agent](docs/Agents.md#add-a-prompt-agent) |
| `architecture-advisor` | Hosted | Microsoft Agent Framework container with Foundry Toolbox (Microsoft Learn MCP and an architecture decision brief skill) | [`src/agents/architecture_advisor/`](src/agents/architecture_advisor/) | [Deploy the Hosted Agent](docs/Agents.md#deploy-the-hosted-agent) |
| `incident-triage` | Hosted | [Flock](https://github.com/whiteducksoftware/flock) blackboard running a three-agent triage crew with no external tools | [`src/agents/incident_triage/`](src/agents/incident_triage/) | [Deploy the Flock Hosted Agent](docs/Agents.md#deploy-the-flock-hosted-agent) |
| `release-notes-writer` | Prompt | An admin-connected model served through [Token Control](https://tokencontrol.ai/), white duck's FinOps and governance gateway | [`src/agents/release_notes_writer/`](src/agents/release_notes_writer/) | [Deploy the Connected-Model Prompt Agent](docs/Agents.md#deploy-the-connected-model-prompt-agent) |

`release-notes-writer` needs a Token Control tenant and API key; see
[Setup](docs/Setup.md#token-control-model-gateway). The other three demos need
only the Terraform-deployed model.

## Architecture

- `infra/`: Terraform for the Foundry account and project, model deployment,
  Azure Container Registry, monitoring, and RBAC.
- `.github/workflows/`: pull-request validation plus main-branch deployment
  orchestration for reviewed agent releases.
- `src/agents/`: typed, code-first agent definitions and hosted runtime code.
- `src/delivery/`: configuration, CLI, locking, release records, smoke
  validation, and prompt/hosted SDK adapters.
- `tests/`: deterministic delivery and agent tests.

Deployments create an immutable candidate version, invoke that exact version,
run agent-specific smoke validation, and move endpoint traffic only after the
candidate passes. These service operations are not one atomic transaction.

## Quick Start

Requirements: Python 3.11, `uv`, Terraform `1.16.1`, Azure CLI, Docker for
hosted image builds, and an Azure account with resource and role-assignment
permissions.

```bash
az login
uv sync --dev
export TF_VAR_subscription_id="$(az account show --query id -o tsv)"
export TF_VAR_operator_object_ids="[\"$(az ad signed-in-user show --query id -o tsv)\"]"
terraform -chdir=infra init
terraform -chdir=infra apply -var-file=env/dev.tfvars
```

State is remote; `init` requires the backend described in
[Setup](docs/Setup.md#remote-state) to exist.

Export the project endpoint and model deployment:

```bash
export FOUNDRY_PROJECT_ENDPOINT="$(
  terraform -chdir=infra output -json foundry_project_endpoints \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["AI Foundry API"])'
)"
export FOUNDRY_MODEL_DEPLOYMENT_NAME="$(
  terraform -chdir=infra output -raw model_deployment_name
)"
```

List and deploy registered agents:

```bash
uv run deploy-agent --list
uv run deploy-agent research-assistant
```

Build and deploy a hosted example:

```bash
ACR_NAME="$(terraform -chdir=infra output -raw container_registry_name)"
IMAGE_ENV_FILE="$(scripts/publish-hosted-image.sh "$ACR_NAME" architecture-advisor)"
set -a
. "$IMAGE_ENV_FILE"
set +a
uv run deploy-agent architecture-advisor
```

The Flock example follows the same two steps:

```bash
IMAGE_ENV_FILE="$(scripts/publish-hosted-image.sh "$ACR_NAME" incident-triage)"
set -a
. "$IMAGE_ENV_FILE"
set +a
uv run deploy-agent incident-triage
```

The connected-model example runs on Token Control instead of the
Terraform-deployed model, so it needs one more export:

```bash
export FOUNDRY_CONNECTED_MODEL_DEPLOYMENT_NAME="$(
  terraform -chdir=infra output -raw connected_model_deployment_name
)"
uv run deploy-agent release-notes-writer
```

Run the local quality checks:

```bash
uv run ruff check .
uv run pytest -q
```

## Documentation

- [Setup and infrastructure](docs/Setup.md)
- [Agent development and deployment](docs/Agents.md)
- [Operations and release safety](docs/Operations.md)

## Current Scope

This is a demo and reference implementation, not a production baseline.
Terraform runs on remote state with plan on pull requests and apply on `main`,
but production-grade environment isolation and writer restrictions, and managed
evaluation gates, remain roadmap items.
