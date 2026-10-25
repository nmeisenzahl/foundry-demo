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
  release_notes_writer/  # Prompt agent on an admin-connected (Token Control) model
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
- `completed_tool_name_counts`
- `source_domain_counts`

The `research-assistant` requires a completed `web_search_call` and a
`url_citation` annotation. The `release-notes-writer` requires the opposite:
a message and no completed tool call at all, because a connected model cannot
serve the hosted tools. This keeps agent-specific acceptance logic out of the
shared delivery orchestrator.

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

### Hosting Model

`main.py` wraps the crew in a `FlockApplication` and serves it through Flock's
`FoundryResponsesAdapter` (`flock.integrations.foundry`), which sits on the
official `azure-ai-agentserver-responses` host. The adapter owns HTTP, SSE,
background mode, cancellation, and graceful shutdown.

- **One isolated workflow per request.** The `response_id` becomes the workflow
  ID, and `build_flock(context, ...)` builds a fresh blackboard for it. There is
  no shared blackboard and no turn lock, so requests run concurrently.
- **Stateless turns.** `history_mode="stateless"`: the request text becomes an
  `IncidentReport`; earlier conversation turns are not loaded.
- **Three public outputs.** `ImpactAssessment`, `RootCauseHypothesis`, and
  `ActionPlan` are the application's `output_types`. Each is streamed as its own
  Responses message item as soon as it is published, rendered as the type name
  followed by its JSON. `ActionPlan` is the required output: a workflow that
  ends without one fails. Three message items can only exist if the full
  cascade completed, which is what smoke validation asserts.
- **Safe failures.** An agent failure ends the response as `failed` with
  `server_error` and the message `agent_failed: ...`; exception text never
  reaches the caller. Flock logs the failing agent and the exception to the
  container log.
- **Readiness.** `GET /readiness` returns 503 until the application has
  validated its contract (on the first probe), and again while it drains on
  `SIGTERM`. A missing `FOUNDRY_PROJECT_ENDPOINT` or
  `AZURE_AI_MODEL_DEPLOYMENT_NAME` keeps the replica unready and is logged.
- **Capacity.** At most `MAX_ACTIVE_WORKFLOWS` (default `4`) workflows run per
  replica; further requests fail immediately with `rate_limit_exceeded` instead
  of queueing. A workflow has 300 seconds to finish.
- **Identity.** Requests must carry the gateway's `x-agent-user-id` header,
  which Foundry sets. Outside Foundry, set `FLOCK_FOUNDRY_LOCAL_DEV=1`.
- **Telemetry.** The Foundry host configures OpenTelemetry. `FLOCK_AUTO_TRACE`
  is `false` in the image and in `main.py`, so Flock does not install its own
  tracer provider first.
- **No crash recovery.** Resilient background tasks stay disabled: Flock does
  not checkpoint a running workflow, so a response interrupted by a hard crash
  stays `in_progress` and has to be resubmitted.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_ACTIVE_WORKFLOWS` | `4` | Concurrent workflows admitted per replica |
| `MAX_COMPLETION_TOKENS` | `4000` | Output-token cap per model call |
| `FLOCK_FOUNDRY_LOCAL_DEV` | unset | `1` accepts requests without the gateway identity header; refused inside Foundry |

### Model Access

The Foundry account sets `local_auth_enabled = false`, so there are no API keys.
Flock reaches the model through DSPy and LiteLLM, authenticating with the
container's managed identity via LiteLLM's `azure_ad_token_provider` hook.

Calls go to the Foundry **project** endpoint, as the official samples do:
`api_base` is the injected `FOUNDRY_PROJECT_ENDPOINT` and `api_version` is
`v1`, so LiteLLM calls `<FOUNDRY_PROJECT_ENDPOINT>/openai/v1`. The token
provider (`get_default_azure_token_provider`) requests scope
`https://ai.azure.com/.default`; it is created once per process and shared by
every workflow. Each model call also forwards the request's
`x-agent-foundry-call-id`.

That identity is **not** the project's managed identity. Foundry mints a
per-agent identity when the agent is first created and runs the container as it,
so the container inherits none of the project's access. Per the
[Flock Foundry guide](https://github.com/whiteducksoftware/flock/blob/0.5.700/docs/guides/foundry.md),
project-endpoint inference needs no extra role for that identity. The
account-scope `Foundry User` grant from `infra/rbac.tf` stays in place
regardless, together with `Monitoring Metrics Publisher` on Application
Insights. Record the identity's object ID in `hosted_agent_object_ids`
(`infra/env/dev.tfvars`) so both are provisioned:

```bash
az ad sp list \
  --filter "displayName eq '<account>-<project>-incident-triage-AgentIdentity'" \
  --query "[0].id" -o tsv
```

The identity does not exist until the agent has been deployed once. See
[Operations](Operations.md) for the symptom and the one-off unblock.

Two runtime details are load-bearing and easy to lose:

- Current Foundry chat models reject `max_tokens`, and LiteLLM cannot infer a
  model family from an Azure *deployment* name, so its own parameter mapping
  never fires. `DSPyEngine(max_completion_tokens=...)` sends
  `max_completion_tokens` instead and never sends `max_tokens`. The cap comes
  from `MAX_COMPLETION_TOKENS`.
- Flock-level token streaming is disabled. The adapter streams whole artifacts,
  so per-token streaming buys nothing.

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
`Monitoring Metrics Publisher` on Application Insights and, matching
`infra/rbac.tf`, the `Foundry User` role on the Foundry **account**, then
redeploy:

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

An account-scope assignment inherits down to the project, so it covers the
project endpoint too. `infra/rbac.tf` provisions the same grant for
`operator_object_ids` so operators can run the agent locally.

### Resource Tier

Foundry accepts only fixed CPU/memory pairs -- `(0.25, 0.5Gi)`, `(0.5, 1Gi)`,
`(1, 2Gi)`, `(2, 4Gi)` -- and rejects anything else at `create_version` time,
i.e. mid-deployment. `HostedAgentSpec` validates the pair on construction so an
invalid tier fails in `pytest` instead.

Measured peak RSS for a full three-agent cascade is ~310 MiB, so the default
`(1, 2Gi)` tier applies. Every concurrent workflow builds its own blackboard,
so raise the tier before raising `MAX_ACTIVE_WORKFLOWS` far beyond the default.

### Dependencies

`src/agents/incident_triage/pyproject.toml` depends on
`flock-core[azure,foundry]`. The `foundry` extra brings
`azure-ai-agentserver-responses` and `azure-ai-agentserver-core` (2.1.x), and
`azure` brings `azure-identity`. `uv.lock` pins the exact versions. No
dependency overrides are needed: `flock-core` 0.5.700 itself requires
OpenTelemetry `>=1.43`.

### Run Locally

Export `FOUNDRY_PROJECT_ENDPOINT` and `AZURE_AI_MODEL_DEPLOYMENT_NAME` as in
[Hosted Local Development](#hosted-local-development). Locally,
`DefaultAzureCredential` falls back to your `az login` identity, which needs the
operator grant described above. Then:

```bash
cd src/agents/incident_triage
FLOCK_FOUNDRY_LOCAL_DEV=1 uv run python main.py

# In a second shell:
curl -s -o /dev/null -w '%{http_code}\n' localhost:8088/readiness
curl -s localhost:8088/responses \
  -H 'content-type: application/json' \
  -d '{"input":"Checkout returns HTTP 500 for 30% of requests since the 14:05 deploy."}' \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["output"]))'
```

Readiness should answer `200` and the request should return `3` output items.
Add `"stream": true` to see each artifact arrive as its own output item.

## Deploy the Connected-Model Prompt Agent

The `release-notes-writer` agent does not run on the Terraform-deployed Azure
OpenAI model the other three agents share. It runs on an **admin-connected
model**: a Foundry `ModelGateway` connection whose target is
[Token Control](https://tokencontrol.ai/), where every inference request is
priced, budgeted, rate-limited, and audited before it reaches a model.

Microsoft calls this *bring your own model*. Two constraints follow from it,
and both shape the agent:

- **Prompt agents only.** Foundry does not support connected models for hosted
  agents, so this demo cannot be a container.
- **Most tools are unavailable.** Web Search, Bing grounding, SharePoint,
  Memory Search, Browser Automation, and Microsoft Fabric all refuse to run
  against a connected model. `release-notes-writer` therefore declares no tools
  at all, and its smoke run asserts that none were called.

### Model Reference

A connected model is addressed as `<connection-name>/<model-name>`, not as a
deployment name. Terraform owns both halves and composes them:

```bash
terraform -chdir=infra output -raw connected_model_deployment_name
# foundrydemo-token-control/gpt-5.2
```

The spec declares `connected_model_env_var="FOUNDRY_CONNECTED_MODEL_DEPLOYMENT_NAME"`,
so for this agent that variable takes precedence over
`FOUNDRY_MODEL_DEPLOYMENT_NAME`. Every other agent ignores it.

`DeploymentConfig.get_connected_model` validates the shape before the first
Foundry call. Foundry itself reports a bad reference as `model not found` only
at invocation time, once a candidate version already exists; failing on the
configuration instead keeps that dangling version from being created.

### Deploy

```bash
export FOUNDRY_CONNECTED_MODEL_DEPLOYMENT_NAME="$(
  terraform -chdir=infra output -raw connected_model_deployment_name
)"
uv run deploy-agent release-notes-writer
```

The release path is identical to every other agent: immutable candidate
version, invoke that exact version, validate, then move traffic.

### Telling a Connected Deployment Apart

Deployment records carry `model_deployment_name`, which is the only place the
governed path is visible in this repository's own artifacts:

```json
{ "agent_name": "research-assistant",   "model_deployment_name": "foundrydemo-chat-model" }
{ "agent_name": "release-notes-writer", "model_deployment_name": "foundrydemo-token-control/gpt-5.2" }
```

It is a resource name, not a secret: no endpoint and no credential. The request
itself is visible in the Token Control dashboard, which is where the demo's
actual claim is verified.

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

`incident-triage` additionally needs `FLOCK_FOUNDRY_LOCAL_DEV=1`, because it
otherwise rejects requests without the Foundry gateway's identity header; see
[Run Locally](#run-locally) for the request to send.

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
