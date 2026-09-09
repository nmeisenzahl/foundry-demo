# Microsoft Foundry SDK-First Agent Delivery Demo

A version-safe delivery reference for Microsoft Foundry prompt and hosted
agents. Terraform owns the Azure control plane, while a Python SDK delivery
package owns typed agent definitions, immutable versions, smoke validation,
release records, and endpoint promotion.

The repository currently includes:

- `research-assistant`: a prompt agent with Web Search and citation validation.
- `architecture-advisor`: a containerized Microsoft Agent Framework hosted agent with Foundry Toolbox (Microsoft Learn MCP and architecture decision brief skill).

## Architecture

- `infra/`: Terraform for the Foundry account and project, model deployment,
  Azure Container Registry, monitoring, and RBAC.
- `src/agents/`: typed, code-first agent definitions and hosted runtime code.
- `src/delivery/`: configuration, CLI, locking, release records, smoke
  validation, and prompt/hosted SDK adapters.
- `tests/`: deterministic delivery and agent tests.

Deployments create an immutable candidate version, invoke that exact version,
run agent-specific smoke validation, and move endpoint traffic only after the
candidate passes. These service operations are not one atomic transaction.

## Quick Start

Requirements: Python 3.11, `uv`, Terraform 1.9 or later, Azure CLI, Docker for
hosted image builds, and an Azure account with resource and role-assignment
permissions.

```bash
az login
uv sync --dev
terraform -chdir=infra init
terraform -chdir=infra apply -var-file=env/dev.tfvars
```

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

Build and deploy the hosted example:

```bash
ACR_NAME="$(terraform -chdir=infra output -raw container_registry_name)"
IMAGE_ENV_FILE="$(scripts/publish-hosted-image.sh "$ACR_NAME")"
set -a
. "$IMAGE_ENV_FILE"
set +a
uv run deploy-agent architecture-advisor
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

This is a local demo and reference implementation, not a production baseline.
GitHub Actions delivery, workload identity federation, remote Terraform state,
managed evaluation gates, production network isolation, and centralized
release evidence remain roadmap items.
