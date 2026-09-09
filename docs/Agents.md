# Agent Development and Deployment

The delivery package is an uncontainerized Python 3.11 `uv` project. Prompt
definitions run as Foundry-managed prompt agents. Hosted application code is
isolated and containerized because the image is the deployed runtime boundary.

## Project Structure

```text
src/agents/
  common/                # Shared contracts and validators
  research_assistant/    # Prompt agent definition, tools, and instructions
  architecture_advisor/  # Hosted runtime, definition, Toolbox spec, Dockerfile, and lockfile
  registry.py            # Discovers packages through their AGENT_SPEC export
  __init__.py            # Public agent contracts and registry API
```

Each agent owns a subfolder under `src/agents/`. Shared release orchestration
stays under `src/delivery/`.

## Add a Prompt Agent

Create a package such as `src/agents/support_assistant/` with a `spec.py`:

```python
import inspect

from foundry_demo.agents.common.base import PromptAgentSpec


SUPPORT_ASSISTANT_SPEC = PromptAgentSpec(
    name="support-assistant",
    description="Answers product-support questions.",
    instructions=inspect.cleandoc(
        """
        You are a product support assistant.
        Give concise answers and state when the available information is insufficient.
        """
    ),
    tools_factory=list,
    smoke_prompt="Explain that you are ready to answer a support question.",
    smoke_tool_choice=None,
)

AGENT_SPEC = SUPPORT_ASSISTANT_SPEC
```

Export `AGENT_SPEC` from the package's `__init__.py`. The registry discovers
agent packages automatically and fails fast when the export is missing or
invalid.

Add `foundry_demo.agents.support_assistant` to the
`[tool.setuptools].packages` list in `pyproject.toml`. The test suite verifies
that source agent packages are both discovered and packaged.

Validate and deploy the new agent:

```bash
uv run ruff check .
uv run pytest -q
uv run deploy-agent --list
uv run deploy-agent support-assistant
```

## Deploy a Prompt Agent

The CLI defaults to `research-assistant` when no name is supplied:

```bash
uv run deploy-agent
uv run deploy-agent research-assistant
```

Unknown options, extra positional arguments, and combining `--list` with an
agent name are rejected. An unknown agent name writes a failed deployment
record and exits non-zero. Parser-rejected invocations, `--list`, and `--help`
do not create records.

On success, the command prints the agent name and local audit-record path:

```text
Successfully deployed research-assistant (record: .../artifacts/deployments/..._uuid.json)
```

## Smoke Acceptance

Every agent defines its smoke prompt and may provide custom evidence
validation. Set `smoke_tool_choice="required"` only when a successful smoke
response must execute a tool.

Custom validators can inspect:

- `response_id`
- `response_status`
- `output_text_present`
- `output_item_counts`
- `completed_output_item_counts`
- `annotation_counts`

The `research-assistant` requires a completed `web_search_call` and a
`url_citation` annotation. This keeps agent-specific acceptance logic out of
the shared delivery orchestrator.

## Deploy the Hosted Agent

The `architecture-advisor` uses Python 3.13, Microsoft Agent Framework, the
Foundry Responses protocol, Microsoft Foundry Toolbox, and an immutable container image.

### Skill and Toolbox Delivery

During delivery, the Azure AI Projects SDK:
1. Creates an immutable Foundry Skill version (`architecture-decision-brief`) from `SKILL.md` via `project.beta.skills.create_from_files(...)`.
2. Creates an immutable Foundry Toolbox version (`architecture-advisor-toolbox`) via `project.toolboxes.create_version(...)`, referencing allowlisted Microsoft Learn MCP tools (`microsoft_docs_search`, `microsoft_docs_fetch`) and pinned to the exact Skill version.
3. Injects the versioned endpoint (`TOOLBOX_ENDPOINT=<project>/toolboxes/<name>/versions/<version>/mcp?api-version=v1`) into the hosted container definition.

Inside the container, `FoundryToolbox(credential, url=TOOLBOX_ENDPOINT)` exposes the tools, and `toolbox.as_skills_provider(disable_load_skill_approval=True, disable_read_skill_resource_approval=True)` exposes the architecture decision brief skill.

Terraform provisions Azure Container Registry and grants:

- The current operator permission to push images
- The Foundry project identity permission to pull images
- The operator the Foundry Project Manager role

Foundry provisions the hosted agent's dedicated instance managed identity only
after the first hosted version is created. Build, publish, and deploy once so
that identity exists:

```bash
terraform -chdir=infra apply -var-file=env/dev.tfvars
ACR_NAME="$(terraform -chdir=infra output -raw container_registry_name)"
IMAGE_ENV_FILE="$(scripts/publish-hosted-image.sh "$ACR_NAME")"
set -a
. "$IMAGE_ENV_FILE"
set +a
uv run deploy-agent architecture-advisor
```

Then grant the identity `Monitoring Metrics Publisher` on Application Insights and `Azure AI User` on the Foundry project so it can authenticate to the Toolbox endpoint:

```bash
APPI_ID="$(terraform -chdir=infra output -raw application_insights_id)"
PROJECT_ID="$(terraform -chdir=infra output -raw foundry_project_id)"
AGENT_SP_ID="$(az ad sp list --filter "startswith(displayName, '$(terraform -chdir=infra output -raw foundry_account_name)-$(terraform -chdir=infra output -raw foundry_project_name)-architecture-advisor')" --query "[0].id" -o tsv)"
if [ -z "$AGENT_SP_ID" ]; then
  echo "Hosted agent service principal was not found." >&2
  exit 1
fi
az role assignment create \
  --assignee-object-id "$AGENT_SP_ID" \
  --assignee-principal-type "ServicePrincipal" \
  --role "Monitoring Metrics Publisher" \
  --scope "$APPI_ID"
az role assignment create \
  --assignee-object-id "$AGENT_SP_ID" \
  --assignee-principal-type "ServicePrincipal" \
  --role "Azure AI User" \
  --scope "$PROJECT_ID"
```

Wait for role-assignment propagation, then redeploy:

```bash
uv run deploy-agent architecture-advisor
```

The publisher:

- Builds `linux/amd64`
- Rejects the mutable `latest` tag
- Pushes a unique image reference
- Records the resolved image digest

The hosted definition binds to the digest and is hashed in full, including its
model, protocol, CPU, memory, environment, and immutable image. Delivery first
initializes the immutable Toolbox MCP endpoint, then waits for the candidate
version to become `active`. Smoke creates a session pinned to that version,
forces a named `load_skill` call, and performs a separate grounded comparison.
The version is promoted only when the combined evidence includes the Skill call,
a Microsoft Learn function call, and a `learn.microsoft.com` citation.

## Hosted Local Development

```bash
cd src/agents/architecture_advisor
uv sync --dev
uv run pytest -q
uv run ruff check .
uv run python main.py
```

The hosted project has its own `pyproject.toml`, `uv.lock`, `Dockerfile`, and
`.dockerignore`. Its root-level test is `tests/test_hosted_runtime.py`. The
hosted environment selects that test; the root Python 3.11 environment collects
but skips it because hosted runtime dependencies are not installed there.

The server listens on port `8088`. Foundry injects
`FOUNDRY_PROJECT_ENDPOINT`, `AZURE_AI_MODEL_DEPLOYMENT_NAME`, and Application
Insights configuration into the hosted runtime.
