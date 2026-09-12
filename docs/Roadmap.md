# Roadmap

This roadmap contains only work that is not implemented in the current local
demo. Items are grouped by outcome rather than by historical specification
phase.

## 1. Automated Delivery and Identity

**Outcome:** Extend the implemented agent delivery automation with controlled
production writers and approved infrastructure changes.

- Add approved infrastructure apply workflows with explicit approvals.
- Make CI/CD the routine production writer and limit human production writes.

## 2. Remote State and Environment Isolation

**Outcome:** Support shared development, test/staging, and production targets
without relying on local Terraform state.

- Provision a secured Azure Blob backend with Entra authentication, locking,
  restricted access, encryption, backup, and recovery procedures.
- Migrate existing state with `terraform init -migrate-state`.
- Parameterize environment-specific Foundry projects, model deployments,
  identities, approvals, and Terraform state.
- Keep reviewed agent definitions and dependency versions consistent as they
  progress between environments.

## 3. Managed Knowledge and Evaluation Gates

**Outcome:** Extend the SDK-first lifecycle with managed knowledge and
quality gates that can block promotion. (Note: Remote MCP and shared tools via
Foundry Toolbox and Skills lifecycle are now implemented in `architecture-advisor`.)

- Add one justified managed knowledge scenario using File Search, Azure AI
  Search, or Foundry IQ rather than introducing every option.
- Define lifecycle ownership, readiness checks, permissions, and release
  references for knowledge sources, indexes, and connections.
- Add reviewed datasets, built-in or rubric evaluators, acceptance thresholds,
  and Foundry-managed evaluation runs.
- Evaluate candidates and baselines under the same pinned or recorded
  conditions.
- Fail promotion when a mandatory evaluation fails, times out, or is
  incomplete.
- Link evaluation evidence and dependency references to the release manifest.
