# Operations and Release Safety

This guide describes the guarantees and limits of the local delivery workflow.
It is not a production operations baseline.

## Release Lifecycle

Each deployment:

1. Acquires a local lock for the project and agent.
2. Resolves and preserves the currently served version when one exists.
3. Creates a unique immutable candidate version.
4. Waits for hosted candidates to become active.
5. Invokes the exact candidate version.
6. Runs generic and agent-specific smoke validation.
7. Moves 100% of endpoint traffic only after smoke succeeds.
8. Writes an atomic JSON deployment record and releases the lock.

On the first deployment, Foundry initially routes to `latest`. The endpoint can
be reached immediately by principals that already have project access.
Operational policy must withhold publication and additional consumer access
until validation succeeds.

On later deployments, the workflow pins the currently served version before it
creates the candidate. Version creation, validation, and routing are separate
service operations and are not one atomic transaction.

## Routing Invariants

- Candidate versions are immutable and retained for auditability.
- Existing routes must resolve to one supported active version before a new
  candidate is created.
- Promotion always assigns 100% of traffic to an explicit version.
- Unsupported route shapes and missing active versions fail closed.
- A local route reread can detect some conflicts but is not atomic
  compare-and-swap protection.

If a route update reports a transport error or timeout, the deployer reads the
service state:

- Candidate observed at 100%: record
  `confirmed_after_transport_error`.
- Any other or unreadable state: record `uncertain` and fail the deployment.

Do not assume every failed route request left the previous route unchanged.

## Locking and Concurrency

The local process lock enforces one writer per project agent on the current
machine. Different agents can deploy concurrently.

The lock does not coordinate:

- Other machines
- GitHub Actions or other pipelines
- Foundry portal changes
- Other repositories or deployment tools

Production automation needs target-scoped pipeline concurrency and a controlled
writer model.

## Smoke Validation

Generic smoke acceptance requires:

- Response status `completed`
- Non-empty output text
- Any agent-specific validator to pass

For `research-assistant`, a passing response must also contain:

- At least one completed `web_search_call`
- At least one structured `url_citation`

This proves exact-version invocation, required tool execution, generated text,
and citation plumbing. It does not evaluate factual correctness or overall
answer quality.

Hosted deployments additionally fail if provisioning reports `failed` or does
not reach `active` before the readiness timeout.

## Deployment Records

Records default to `artifacts/deployments/`. Override the location with
`FOUNDRY_DEPLOYMENT_RECORD_DIR`.

Each record includes:

- Schema and deployment identifiers
- Destination project endpoint
- Agent and candidate versions
- Previous and observed active versions
- Phase, status, timestamps, and failure message
- Generic smoke evidence
- Full prompt or hosted definition SHA-256 and Git metadata
- Immutable hosted image reference and digest when applicable
- Repository and GitHub workflow metadata when available
- Dirty-worktree status
- Cutover outcome

The same definition hash, hosted image evidence, and audit identifiers are
attached to Foundry version metadata. A failed record retains the last
operational phase reached and uses `status: failed` plus `failure_message` to
describe the outcome. Prompts and response bodies are not written to deployment
records.

Local records are developer evidence only. They are not a distributed lock or
a production audit system.

## Common Failures

### Terraform Apply Returns 403

Azure RBAC propagation is eventually consistent. Wait for the new role
assignment to propagate, then repeat:

```bash
terraform -chdir=infra apply -var-file=env/dev.tfvars
```

### Configuration Is Rejected

Confirm:

- `FOUNDRY_PROJECT_ENDPOINT` is a non-empty Foundry project endpoint.
- `FOUNDRY_MODEL_DEPLOYMENT_NAME` is set.
- Hosted image variables contain an immutable reference and a `sha256:` digest.
- No hosted image uses the `latest` tag.

### Deployment Is Already Running

Wait for the existing local deployment of the same project agent to finish.
The second process intentionally fails instead of waiting or sharing the lock.

### Hosted Candidate Never Becomes Active

Inspect the Foundry provisioning error and container/runtime logs. The deployer
does not promote candidates that report `failed` or exceed the readiness
timeout.

### Smoke Validation Fails

Use the deployment record's smoke evidence and failure message. Failed
candidates remain available for diagnosis, but traffic is not intentionally
promoted to them.

For `architecture-advisor`, smoke requires:
- Completed named `load_skill` setup call.
- Completed `microsoft_docs_search` or `microsoft_docs_fetch` call.
- At least one source citation from `learn.microsoft.com`, including structured
  `url_citation` annotations when the URL is not repeated in output text.

### Toolbox 401 or 403 Errors

The hosted agent container authenticates to the Toolbox endpoint using its managed identity with scope `https://ai.azure.com/.default`. If the agent logs `401 Unauthorized` or `403 Forbidden` when connecting to `TOOLBOX_ENDPOINT`:
1. Ensure the agent's service principal has the `Azure AI User` role assigned on the Foundry project.
2. In the initial bootstrap deployment, the hosted service principal is created only after version 1 is registered; assign the role and redeploy.
3. Delivery retries 401/403 smoke failures three times; allow several minutes for Azure RBAC propagation before redeploying if those retries are exhausted.

### Failure Recovery

If Skill or Toolbox candidate creation fails:
- The error terminates delivery before any agent candidate is created.
- The active agent route remains untouched.
- Re-running delivery will package and create new immutable versions.

## Resource Cleanup

Destroy the Azure resources:

```bash
terraform -chdir=infra destroy -var-file=env/dev.tfvars
```

Local Terraform state, deployment records, caches, and generated image
environment files must remain untracked.
