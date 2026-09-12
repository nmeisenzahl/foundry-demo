# Reviewed, non-secret configuration for the dev environment. Tracked in
# git; never add credentials, tokens, or keys here.
#
# subscription_id and operator_object_ids are intentionally absent: they
# come from the environment in both local and CI runs.
#   export TF_VAR_subscription_id="$(az account show --query id -o tsv)"
#   export TF_VAR_operator_object_ids="[\"$(az ad signed-in-user show --query id -o tsv)\"]"

location                   = "swedencentral"
project_name               = "foundrydemo"
model_name                 = "gpt-5.6-terra"
model_version              = "2026-07-09"
model_sku_name             = "GlobalStandard"
model_capacity             = 500
github_repository          = "nmeisenzahl/foundry-demo"
github_environment_name    = "dev"
github_oidc_subject_prefix = "repo:nmeisenzahl@28020936/foundry-demo@1362938755"

# Per-agent identities Foundry mints on first agent creation. They only exist
# after a hosted agent has been deployed once, so a brand-new agent needs one
# `terraform apply` after its first deployment. Refresh with:
#   az ad sp list --filter "displayName eq 'foundrydemo-ai-npje6-foundrydemo-dev-<agent>-AgentIdentity'" --query "[0].id" -o tsv
hosted_agent_object_ids = {
  "architecture-advisor" = "67b83d63-285a-473d-9cb1-2d4d096de400"
  "incident-triage"      = "df0e72ef-f65d-4398-98ed-39870cc7e91a"
}
