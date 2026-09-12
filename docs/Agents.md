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
  incident_triage/       # Flock blackboard runtime, definition, Dockerfile, and lockfile
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
IMAGE_ENV_FILE="$(scripts/publish-hosted-image.sh "$ACR_NAME" architecture-advisor)"
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

## Deploy the Flock Hosted Agent

The `incident-triage` agent runs a [Flock](https://github.com/whiteducksoftware/flock)
blackboard inside the container. It is the repository's toolbox-free hosted
example: no MCP servers, no Skills, no external tools.

### Blackboard Topology

```text
IncidentReport
   |-- impact_assessor      --> ImpactAssessment
   |-- root_cause_analyst   --> RootCauseHypothesis
              \-- AND gate --> incident_commander --> ActionPlan
```

Agents subscribe to *types*, not to each other. `impact_assessor` and
`root_cause_analyst` both consume `IncidentReport`, so Flock runs them
concurrently. `incident_commander` consumes both of their output types, which
Flock treats as an AND gate and fires once, after both have published. No edges
are declared anywhere in `flock_app.py` -- the topology is a consequence of the
Pydantic contracts.

One HTTP turn drives one blackboard run. The handler publishes the caller's text
as an `IncidentReport` under a correlation ID, waits for the cascade, and emits
each resulting artifact as its own Responses output item. That makes the
multi-agent flow visible to callers and gives smoke validation something to
assert: three output items can only exist if the full cascade completed.

### Model Access

The Foundry account sets `local_auth_enabled = false`, so there are no API keys.
Flock reaches the model through DSPy and LiteLLM, authenticating with the
container's managed identity via LiteLLM's `azure_ad_token_provider` hook
(`flock.engines.auth.azure.get_default_azure_token_provider`).

That identity is **not** the project's managed identity. Foundry mints a
per-agent identity when the agent is first created and runs the container as it,
so the container inherits none of the project's access. Record its object ID in
`hosted_agent_object_ids` (`infra/env/dev.tfvars`) so `infra/rbac.tf` grants it
`Foundry User` on the account and `Monitoring Metrics Publisher` on Application
Insights:

```bash
az ad sp list \
  --filter "displayName eq '<account>-<project>-incident-triage-AgentIdentity'" \
  --query "[0].id" -o tsv
```

The identity does not exist until the agent has been deployed once, so a new
hosted agent fails its first smoke test, then needs its ID recorded and one
`terraform apply`. See [Operations](Operations.md) for the symptom and the
one-off unblock.

Two runtime details are load-bearing and easy to lose:

- LiteLLM cannot infer a model family from an Azure *deployment* name, so its
  parameter mapping never fires. Current Foundry chat models reject
  `max_tokens`, which `DSPyEngine` always sends, so the agent passes
  `additional_drop_params=["max_tokens"]`. `DSPyEngine` reserves
  `max_completion_tokens` without ever setting it, so no replacement cap can be
  supplied and the model's default output limit applies.
- Flock-level token streaming is disabled. The handler emits whole artifacts
  rather than tokens, and LiteLLM's `StreamWrapper` fails under the hosting
  runtime's GenAI tracing instrumentation.

The inference base URL is derived from the injected `FOUNDRY_PROJECT_ENDPOINT`
and validated in `model_endpoint.py`. Both `AZURE_API_BASE` and
`AZURE_API_VERSION` override the derived values; the API version must cover
whichever model the project deploys.

### Deploy

```bash
ACR_NAME="$(terraform -chdir=infra output -raw container_registry_name)"
IMAGE_ENV_FILE="$(scripts/publish-hosted-image.sh "$ACR_NAME" incident-triage)"
set -a
. "$IMAGE_ENV_FILE"
set +a
uv run deploy-agent incident-triage
```

As with `architecture-advisor`, Foundry provisions the agent's managed identity
only after the first hosted version exists. Grant that identity
`Monitoring Metrics Publisher` on Application Insights and the `Foundry User`
role on the Foundry **account**, then redeploy:

```bash
APPI_ID="$(terraform -chdir=infra output -raw application_insights_id)"
ACCOUNT_ID="$(terraform -chdir=infra output -raw foundry_account_id)"
AGENT_SP_ID="$(az ad sp list --filter "startswith(displayName, '$(terraform -chdir=infra output -raw foundry_account_name)-$(terraform -chdir=infra output -raw foundry_project_name)-incident-triage')" --query "[0].id" -o tsv)"
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
  --role "53ca6127-db72-4b80-b1b0-d745d6d5456d" \
  --scope "$ACCOUNT_ID"
```

The account scope matters: this agent calls model inference directly rather than
the Agents API, and a project-scope assignment does not inherit upward to the
account. `infra/rbac.tf` provisions the same account-scope grant for
`operator_object_ids` so operators can run the agent locally.

### Resource Tier

Foundry accepts only fixed CPU/memory pairs -- `(0.25, 0.5Gi)`, `(0.5, 1Gi)`,
`(1, 2Gi)`, `(2, 4Gi)` -- and rejects anything else at `create_version` time,
i.e. mid-deployment. `HostedAgentSpec` validates the pair on construction so an
invalid tier fails in `pytest` instead.

Measured peak RSS for a full three-agent cascade is ~310 MiB, so the default
`(1, 2Gi)` tier applies.

### Dependency Overrides

`flock-core` pins `opentelemetry-api` and `opentelemetry-sdk` to `1.34.1`, while
every release of `azure-ai-agentserver-core` requires `>=1.43`. Both use only
stable OpenTelemetry 1.x APIs, so `src/agents/incident_triage/pyproject.toml`
forces the newer runtime through `[tool.uv] override-dependencies` rather than
forking either dependency. `googleapis-common-protos` is overridden for the same
reason: `flock-core`'s deprecated Jaeger exporter caps it below what the OTLP
exporter needs, and nothing here exports to Jaeger.

## Hosted Local Development

```bash
cd src/agents/architecture_advisor   # or src/agents/incident_triage
uv sync --dev
uv run pytest -q
uv run ruff check .
uv run python main.py
```

The server listens on port `8088`. Running it locally needs the two variables
Foundry would otherwise inject:

```bash
export FOUNDRY_PROJECT_ENDPOINT="$(
  terraform -chdir=../../../infra output -json foundry_project_endpoints \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["AI Foundry API"])'
)"
export AZURE_AI_MODEL_DEPLOYMENT_NAME="$(
  terraform -chdir=../../../infra output -raw model_deployment_name
)"
uv run python main.py
```

For `incident-triage`, one request should return three output items:

```bash
curl -s localhost:8088/responses \
  -H 'content-type: application/json' \
  -d '{"input":"Checkout returns HTTP 500 for 30% of requests since the 14:05 deploy."}' \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["output"]))'
```

Each hosted project has its own `pyproject.toml`, `uv.lock`, `Dockerfile`, and
`.dockerignore`, and selects a single root-level test file:
`tests/test_hosted_runtime_architecture_advisor.py` and
`tests/test_hosted_runtime_incident_triage.py`. Both are collected but skipped
in the root Python 3.11 environment, where hosted runtime dependencies are not
installed. The files are named per agent because both hosted projects have a
top-level `main.py`, which would otherwise collide on the module name.

In the deployed container, Foundry injects `FOUNDRY_PROJECT_ENDPOINT`,
`AZURE_AI_MODEL_DEPLOYMENT_NAME`, and Application Insights configuration into
the hosted runtime.
