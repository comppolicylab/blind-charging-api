resource "azurerm_cognitive_account" "openai" {
  name                  = format("%s-rbc-cs-oai", var.partner)
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  sku_name              = "S0"
  kind                  = "OpenAI"
  tags                  = var.tags
  custom_subdomain_name = format("%s-rbc-cs-oai", var.partner)
}

# TODO(jnu): azurerm does not support the content filter resource yet.
# See https://github.com/hashicorp/terraform-provider-azurerm/issues/22822
resource "azapi_resource" "no_content_filter" {
  count                     = var.disable_content_filter ? 1 : 0
  type                      = "Microsoft.CognitiveServices/accounts/raiPolicies@2023-10-01-preview"
  name                      = "NoFilter"
  parent_id                 = azurerm_cognitive_account.openai.id
  schema_validation_enabled = false
  body = jsonencode({
    name = "NoFilter"
    properties = {
      basePolicyName = "Microsoft.Default"
      type           = "UserManaged"
      mode           = "Default"
      contentFilters = [
        { name = "hate", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "sexual", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "selfharm", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "violence", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "hate", blocking = false, enabled = false, allowedContentLevel = "High", source = "Completion" },
        { name = "sexual", blocking = false, enabled = false, allowedContentLevel = "High", source = "Completion" },
        { name = "selfharm", blocking = false, enabled = false, allowedContentLevel = "High", source = "Completion" },
        { name = "violence", blocking = false, enabled = false, allowedContentLevel = "High", source = "Completion" },
        { name = "jailbreak", blocking = false, enabled = false, allowedContentLevel = "High", source = "Prompt" },
        { name = "indirect_attack", blocking = false, enabled = false, source = "Completion" },
        { name = "protected_material_text", blocking = false, enabled = false, source = "Completion" },
      ]
    }
  })
  depends_on = [azurerm_cognitive_account.openai]
}

resource "azurerm_cognitive_deployment" "llm" {
  name                 = format("%s-rbc-oai-llm", var.partner)
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = "gpt-4o"
    version = "2024-05-13"
  }
  sku {
    name     = "Standard"
    capacity = 80
  }
  rai_policy_name        = var.disable_content_filter ? azapi_resource.no_content_filter[0].name : "Default"
  version_upgrade_option = "NoAutoUpgrade"
}

resource "azurerm_private_endpoint" "openai" {
  name                = format("%s-rbc-cs-oai-pe", var.partner)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.openai.id
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
