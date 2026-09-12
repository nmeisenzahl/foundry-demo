# Foundry "bring your own model": an admin-connected model whose inference
# requests leave Azure through Token Control, where they are priced, budgeted,
# rate-limited, and audited before they reach the model.
#
# The connection is a control-plane ARM resource, so Terraform owns it. Only
# prompt agents can use it -- Foundry does not support connected models for
# hosted agents.
resource "azapi_resource" "token_control" {
  count = var.token_control == null ? 0 : 1

  type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview"
  name      = local.names.token_control_connection
  parent_id = azurerm_cognitive_account_project.main.id

  # The ModelGateway category is preview-only and absent from the provider's
  # embedded schema, so provider-side validation would reject a valid body.
  # The service remains the authority on what it accepts.
  schema_validation_enabled = false

  body = {
    properties = {
      category = "ModelGateway"
      target   = var.token_control.base_url
      authType = "ApiKey"
      # Scoped to this project rather than every project on the account.
      isSharedToAll = false
      metadata = merge(
        {
          # Token Control serves the OpenAI v1 shape, so Foundry calls
          #   {target}/chat/completions
          # and passes the deployment name in the request body. The key travels
          # in the `api-key` header, which is Foundry's default for a
          # ModelGateway connection, so no custom authConfig is needed.
          #
          # The Azure OpenAI shape is not an option here: this gateway answers
          # /deployments/{name}/chat/completions with
          # "Error Code: 10034, This functionality is not supported".
          deploymentInPath = tostring(var.token_control.deployment_in_path)

          # Static discovery is mandatory, not merely preferred: the gateway
          # answers 405 on every model-listing route, so Foundry has nothing to
          # discover. Complex metadata values must be JSON strings.
          #
          # `format = "OpenAI"` is Microsoft's guidance for any gateway whose
          # chat completions endpoint honours the OpenAI contract, whoever
          # hosts the model. Verified: this one returns tool_calls for a
          # tool_choice=required request.
          models = jsonencode([{
            name = var.token_control.deployment_name
            properties = {
              model = {
                name    = var.token_control.model_name
                version = var.token_control.model_version
                format  = "OpenAI"
              }
            }
          }])
        },
        # Sending an empty api-version would append a meaningless `?api-version=`
        # to every request, so the key is omitted rather than blanked.
        trimspace(var.token_control.inference_api_version) == "" ? {} : {
          inferenceAPIVersion = var.token_control.inference_api_version
        }
      )
    }
  }

  sensitive_body = {
    properties = {
      credentials = {
        key = var.token_control_api_key
      }
    }
  }
}
