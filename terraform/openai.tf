resource "azurerm_cognitive_account" "openai" {
  name                = local.openai_account_name
  resource_group_name = azurerm_resource_group.main.name
  location            = local.openai_location
  sku_name            = "S0"
  kind                = "OpenAI"
  tags                = var.tags
  # NOTE: the subdomain is coerced to lowercase on their end, so we need to do it here
  # otherwise it'll recreate itself everytime terraform is run.
  custom_subdomain_name = lower(format("%s-cs-oai", local.name_prefix))

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.admin.id]
  }

  customer_managed_key {
    key_vault_key_id   = local.needs_openai_kv ? azurerm_key_vault_key.oai[0].id : azurerm_key_vault_key.encryption.id
    identity_client_id = azurerm_user_assigned_identity.admin.client_id
  }
}

# TODO(jnu): azurerm does not support the content filter resource yet.
# See https://github.com/hashicorp/terraform-provider-azurerm/issues/22822
resource "azapi_resource" "no_content_filter" {
  count                     = var.disable_content_filter ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/raiPolicies@2023-10-01-preview"
  name                      = "NoFilter"
  parent_id                 = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false
  body = {
    name = "NoFilter"
    properties = {
      basePolicyName = "Microsoft.Default"
      type           = "UserManaged"
      mode           = "Default"
      contentFilters = [
        { name = "selfharm", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "selfharm", blocking = false, enabled = false, allowedContentLevel = "High", source = "Completion" },
        { name = "Hate", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "Sexual", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "Violence", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "Hate", blocking = false, enabled = false, allowedContentLevel = "High", source = "Completion" },
        { name = "Sexual", blocking = false, enabled = false, allowedContentLevel = "High", source = "Completion" },
        { name = "Violence", blocking = false, enabled = false, allowedContentLevel = "High", source = "Completion" },
        { name = "Jailbreak", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "Indirect Attack", blocking = false, enabled = false, source = "Completion" },
        { name = "Protected Material Text", blocking = false, enabled = false, source = "Completion" },
      ]
    }
  }
  depends_on = [azurerm_cognitive_account.openai]
}

resource "azurerm_cognitive_deployment" "llm" {
  name                 = local.openai_llm_deployment_name
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = "gpt-4o"
    version = "2024-05-13"
  }
  sku {
    name     = "Standard"
    capacity = var.openai_capacity
  }
  rai_policy_name        = var.disable_content_filter ? azapi_resource.no_content_filter[0].name : "Default"
  version_upgrade_option = "NoAutoUpgrade"
}

resource "azurerm_private_endpoint" "openai" {
  name                = local.openai_private_endpoint_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.openai.id
  tags                = var.tags
  private_service_connection {
    name                           = "cs-oai-psc"
    private_connection_resource_id = azurerm_cognitive_account.openai.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "pdz-cs-oai"
    private_dns_zone_ids = [azurerm_private_dns_zone.openai.id]
  }
}

locals {
  openai_endpoint = azurerm_cognitive_account.openai.endpoint
}
