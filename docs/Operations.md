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

## GitHub Actions Delivery

Agent deployment automation is implemented in GitHub Actions:

- `.github/workflows/validate.yml` runs on pull requests and reusable calls. It
  is secretless validation (no Azure login and no deployment writes). It lints
  the workflows themselves with `yamllint` and a checksum-verified `actionlint`,
  so invalid workflow syntax fails review instead of the deployment.
- `.github/workflows/deploy-agents.yml` deploys the affected registered agents
  from `push` on `main`.
- Manual `workflow_dispatch` runs accept `agents=all` or a comma-separated
  list of registered names and fail closed unless the run targets
  `refs/heads/main`. Manual runs ignore change detection and deploy exactly
  what the operator selected.
- The deployment matrix is rendered by `uv run deployment-matrix` from
  `.github/deployment/config.json`, so manual agent selection and CI defaults
  resolve through the same reviewed config.
- Dependabot monitors the `github-actions`, `uv`, and `docker` ecosystems
  weekly; workflow actions remain pinned by full commit SHA and dependencies by
  exact version when updates are reviewed and merged. Digest-only base images
  in the hosted `Dockerfile` need a tag alongside the digest before Dependabot
  can propose meaningful updates.

Pushes deploy only what they affect. `prepare` diffs `github.event.before`
against the pushed commit and passes the changed paths to
`deployment-matrix --changed-paths-file`. `config.json` declares `source_paths`
per agent and a top-level `shared_paths` list; a change under any shared path
selects every agent, because shared delivery code and packaging affect all of
them. A commit that touches no configured path deploys nothing, and the
`deploy` and `aggregate` jobs are skipped rather than run empty. When no
comparable previous commit exists (first push, force push, unreachable
`before`), the run falls back to the full selection.

Hosted CI publishing uses `docker/build-push-action` through the composite
`./.github/actions/build-hosted-image`. Local shell publishing via
`scripts/publish-hosted-image.sh` remains available for local operator-driven
flows.

The workflow computes hosted image tags itself from `ACR_LOGIN_SERVER`, the
configured repository, the run identifiers, and the source commit. The hosted
image CLI receives the unique `run-<run_id>-attempt-<run_attempt>` tagged
reference and the separate Build Push digest. The candidate, deployment record,
and job evidence use the canonical `registry/repository@digest` identity, not
the tagged CLI input.

`deploy-agent --record-path-file` reports the deployment record location before
delivery starts, so evidence references the exact record for that run including
failed runs, instead of guessing from directory contents.

One user-assigned identity (`AZURE_AGENT_DELIVERY_CLIENT_ID`) holds both
`AcrPush` on the registry and Foundry User on the project. Build, push, and
delivery run in one job under one federated subject, so separate publisher and
delivery identities would share the same trust and provide no isolation.

Aggregation runs without a GitHub Environment. Configure the optional
`APPLICATION_INSIGHTS_PORTAL_URL` at repository scope using Terraform's
`github_actions_repository_variables` output; keep the seven deployment,
authentication, and runtime values from `github_actions_environment_variables`
in Environment `dev`. An absent repository monitoring URL is omitted from the
CLI and shown as not configured rather than failing aggregation.

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

GitHub Actions adds target-scoped serialization in
`.github/workflows/_deploy-agent.yml`:

- Concurrency group: `foundry-<environment>-<agent_name>`
- `cancel-in-progress: false`, so an in-progress deployment of the same target
  is never canceled and a later run waits for it

GitHub keeps only one pending run per concurrency group. If a third run for the
same target arrives while one is running and one is waiting, the waiting run is
canceled and the newest one takes its place. Concurrency is not an unbounded
queue, and a canceled pending run deploys nothing.

The local lock still does not coordinate:

- Other machines running local scripts
- Foundry portal changes
- Other repositories or deployment tools

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

When smoke validation fails, deployment does not promote traffic to the
candidate. GitHub Actions still writes per-agent job evidence (`if: always()`)
and uploads artifacts, so failed runs preserve diagnostics for manifest
aggregation.

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
attached to Foundry version metadata. Foundry accepts at most 16 metadata
entries per version, so toolbox and skill dependencies are attached as compact
`name@version` values; the deployment record keeps the expanded fields. A failed record retains the last
operational phase reached and uses `status: failed` plus `failure_message` to
describe the outcome. Prompts and response bodies are not written to deployment
records.

Local records are developer evidence only. They are not a distributed lock or
a production audit system.

In aggregate manifests, status mapping is explicit. Absent job evidence is
`missing`. Recorded failed job, image build, or deploy outcomes are `failed`.
Evidence present without a successful deployment record is `incomplete`.

Per-agent artifacts are named
`deployment-<run_id>-<agent_name>-attempt-<run_attempt>` and upload `artifacts/`
as their root. Job evidence lives at `evidence/<agent_name>.json` and references
its record relative to that root, for example `deployments/<file>.json`, without
the local `artifacts/` prefix. `deploy-agent --record-path-file` reports the
exact record it wrote, so aggregation resolves that declared path directly
inside the same artifact; it never searches for a matching file, and a
reference that resolves outside its own artifact is rejected. A failure before
a record is written keeps a null record reference. A failed hosted record may omit image fields before candidate
creation; any present image fields must match job evidence. Successful hosted
records require complete, exact image identity.

Aggregation downloads matching per-agent artifacts from all attempts of the
same run into separate artifact-name directories, without merging files.
For each agent it selects the highest integer `run_attempt`, reusing a prior
successful attempt when that agent was not rerun. A newer failure replaces
older success; duplicates at the same attempt and evidence from another
`run_id` are rejected. Record lookup stays within the selected artifact and
never borrows a missing record from another attempt. The manifest retains the
originating `run_id` and integer `run_attempt` in each `job_evidence`, while
top-level run metadata describes the aggregation attempt. Every agent entry
also exposes `evidence_run_attempt` and `evidence_is_current_attempt`; missing
evidence has a null source attempt and false currentness.

`release-evidence manifest` requires `--deployment-result`, populated by the
workflow from the current matrix's `needs.deploy.result` and persisted as
`deployment_result`. Accepted values are `success`, `failure`, `cancelled`,
and `skipped`. Failure or cancellation forces the aggregate to `failed`;
a skipped matrix cannot produce a successful aggregate. Prior-attempt success
may contribute to a successful aggregate only when the current matrix result
is `success` and every selected agent has valid successful evidence. The
summary always shows the source attempt and labels older evidence as
`previous-attempt evidence` instead of current success when the matrix did
not succeed.

The manifest status is the single release gate. `Enforce release status` fails
the run when no manifest was generated or when the status is not `succeeded`,
which covers a failed deployment matrix as well as an agent whose evidence or
record is missing or incomplete. The manifest keeps deployment records as they
were written; the record schema never contains prompts or response bodies, so
aggregation does not filter their content.

## Common Failures

### Terraform Apply Returns 403

Azure RBAC propagation is eventually consistent. Wait for the new role
assignment to propagate, then repeat:

```bash
terraform -chdir=infra apply -var-file=env/dev.tfvars
```

In CI, re-run the failed `Terraform apply` job; it re-plans against current
state.

### Terraform State Is Locked

Every Terraform command uses `-lock-timeout=5m`, and the `terraform-dev`
concurrency group serialises workflow runs, so contention normally resolves
itself. A lease orphaned by an infrastructure failure or a cancelled job must
be released by hand, using the lock ID from the error message:

```bash
terraform -chdir=infra force-unlock <LOCK_ID>
```

Confirm no apply is actually running before forcing the unlock.

### Azure Login From Actions Is Rejected

`AADSTS700213` means the federated credential subject does not match the claim
GitHub emitted. Compare the credential on `foundrydemo-dev-tf` against:

```bash
gh api repos/nmeisenzahl/foundry-demo/actions/oidc/customization/sub \
  --jq '{use_immutable_subject, sub_claim_prefix}'
```

The trusted subject is that prefix followed by `:environment:dev`. A
deployment branch restriction added to the `dev` Environment produces a
different symptom: the plan job never starts on pull request branches.

### Configuration Is Rejected

Confirm:

- `FOUNDRY_PROJECT_ENDPOINT` is a non-empty Foundry project endpoint.
- `FOUNDRY_MODEL_DEPLOYMENT_NAME` is set.
- Hosted image variables contain a unique fully qualified tagged reference and
  a separate `sha256:` digest, not `repository@digest` as the CLI image input.
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

For `incident-triage`, smoke requires at least three `message` output items.
The hosted runtime emits exactly one per published blackboard artifact, so three
items can only exist if both parallel agents and the joining commander
published. A partial cascade produces fewer and fails the gate.

For `architecture-advisor`, smoke requires:
- Completed named `load_skill` setup call.
- Completed `microsoft_docs_search` or `microsoft_docs_fetch` call.
- At least one source citation from `learn.microsoft.com`, including structured
  `url_citation` annotations when the URL is not repeated in output text.

### Model Inference 401 or 403 Errors

A hosted agent container does **not** run as the project's managed identity. It
runs as a per-agent identity that Foundry mints when the agent is first created,
exposed as `instance_identity.principal_id` on the agent resource and named
`<account>-<project>-<agent>-AgentIdentity` in Entra ID. Nothing the container
calls on its own behalf inherits the project's access, so that identity needs
its own role assignment.

`incident-triage` calls model inference directly rather than through the Agents
API, using scope `https://cognitiveservices.azure.com/.default` against the
account endpoint. A project-scope assignment does not inherit upward to the
account, so the grant has to sit on the account.

The symptom is a smoke failure like `Response status is 'failed' ... Error
server_error: Blackboard produced no ImpactAssessment ... (impact_assessor:
AuthenticationError: Principal does not have access to API/Operation.)`.

1. Read the agent identity's object ID:

   ```bash
   az ad sp list \
     --filter "displayName eq '<account>-<project>-<agent>-AgentIdentity'" \
     --query "[0].id" -o tsv
   ```

2. Add it to `hosted_agent_object_ids` in `infra/env/dev.tfvars` and apply.
   `infra/rbac.tf` assigns `Foundry User` at the account scope, which inherits
   down to the project and so covers agents that use either endpoint.
3. To unblock without waiting for an apply, make the same assignment directly:

   ```bash
   az role assignment create \
     --scope "$(az cognitiveservices account show -n <account> -g <rg> --query id -o tsv)" \
     --role 53ca6127-db72-4b80-b1b0-d745d6d5456d \
     --assignee-object-id <agent-identity-object-id> \
     --assignee-principal-type ServicePrincipal
   ```

4. Subscription `Owner` is a control-plane role and conveys no Cognitive
   Services data actions; operators running the agent locally need the same
   account-scope grant, which `infra/rbac.tf` provisions for
   `operator_object_ids`.
5. Delivery retries 401/403 smoke failures three times; allow several minutes
   for RBAC propagation before redeploying if those retries are exhausted.

Because Foundry creates the identity lazily, a brand-new hosted agent has no
identity to grant until its first deployment has run. Expect the first
deployment of a new agent to fail its smoke test, then record the identity and
apply.

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

Deployment records, caches, and generated image environment files must remain
untracked. Terraform state is remote; see
[Setup](Setup.md#remote-state).

Destroy remains a local operator action and is never run by CI.

## Terraform CI/CD

`.github/workflows/terraform.yml` owns infrastructure changes. It triggers only
on changes to `infra/**` or to the workflow file itself.

| Event | Job | Behaviour |
| --- | --- | --- |
| `pull_request` | `plan` | `init`, `plan -out=tfplan`, plan rendered into the job summary. Never applies. |
| `push` to `main` | `apply` | `init`, `plan -out=tfplan`, then `apply` of that saved plan, followed by outputs in the job summary. |

Both jobs run under the `dev` GitHub Environment and authenticate with
workload identity federation as `foundrydemo-dev-tf`. There is no
`azure/login` step: the `azurerm` provider and backend exchange the Actions
OIDC token themselves.

Applying the saved plan file, rather than re-planning inside `apply`,
guarantees the applied change is exactly the one rendered in the summary
immediately above it.

A workflow-level `concurrency: terraform-dev` group serialises every run
against the single state file, with `cancel-in-progress: false` so that no run
is cancelled mid-apply and leaves an orphaned lease.

Two caveats are accepted rather than engineered around:

- A merge touching both `infra/` and agent code starts `terraform.yml` and
  `deploy-agents.yml` concurrently, with no ordering guarantee between
  infrastructure changes and agent delivery.
- Fork pull requests receive neither Environment variables nor an OIDC token,
  so the plan job cannot run for them.
