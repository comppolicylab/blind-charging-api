locals {
  key_vault_encryption_key_name = "encryption-key"
}

resource "azurerm_user_assigned_identity" "admin" {
  name                = local.user_assigned_admin_identity_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

resource "azurerm_user_assigned_identity" "gateway" {
  name                = local.user_assigned_gateway_identity_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
}

resource "azurerm_key_vault" "main" {
  name                            = local.key_vault_name
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  enabled_for_disk_encryption     = true
  enabled_for_template_deployment = true
  enabled_for_deployment          = true
  rbac_authorization_enabled      = true
  soft_delete_retention_days      = 7
  purge_protection_enabled        = true
  # TODO(jnu): ideally public network access is locked down, but it
  # hampers the ability to apply terraform updates.
  # public_network_access_enabled   = false
  # NOTE(jnu) - premium is required for HSM keys
  sku_name  = "premium"
  tenant_id = azurerm_user_assigned_identity.admin.tenant_id
}

# NOTE(jnu): When OpenAI is deployed in a separate location from the main resources,
# we need a dedicated key vault in that location.
resource "azurerm_key_vault" "oai" {
  count                           = local.needs_openai_kv ? 1 : 0
  name                            = format("%s-oai", local.key_vault_name)
  resource_group_name             = azurerm_resource_group.main.name
  location                        = local.openai_location
  enabled_for_disk_encryption     = true
  enabled_for_template_deployment = true
  enabled_for_deployment          = true
  rbac_authorization_enabled      = true
  soft_delete_retention_days      = 7
  purge_protection_enabled        = true
  sku_name                        = "premium"
  tenant_id                       = azurerm_user_assigned_identity.admin.tenant_id
}

resource "azurerm_role_assignment" "current_key_vault_admin" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "admin_key_vault_crypto" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = azurerm_user_assigned_identity.admin.principal_id
}

resource "azurerm_role_assignment" "admin_key_vault_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.admin.principal_id
}

resource "azurerm_role_assignment" "gateway_key_vault_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.gateway.principal_id
}

resource "azurerm_role_assignment" "current_oai_key_vault_admin" {
  count                = local.needs_openai_kv ? 1 : 0
  scope                = azurerm_key_vault.oai[0].id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "admin_oai_key_vault_crypto" {
  count                = local.needs_openai_kv ? 1 : 0
  scope                = azurerm_key_vault.oai[0].id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = azurerm_user_assigned_identity.admin.principal_id
}

resource "azurerm_key_vault_key" "encryption" {
  name         = local.key_vault_encryption_key_name
  key_vault_id = azurerm_key_vault.main.id
  key_type     = "RSA-HSM"
  key_size     = 2048
  key_opts     = ["unwrapKey", "wrapKey", "decrypt", "encrypt", "sign", "verify"]
  depends_on = [
    azurerm_role_assignment.current_key_vault_admin,
    azurerm_role_assignment.admin_key_vault_crypto,
  ]

  rotation_policy {
    automatic {
      time_before_expiry = "P30D"
    }

    expire_after = "P90D"
    # Only notify after the automatic rotation should have taken place.
    # So if we get a notification, something is wrong!
    notify_before_expiry = "P29D"
  }
}

# This key is identical to the main encryption key. It is used for OpenAI
# when OpenAI is deployed in a separate location from the main resources.
resource "azurerm_key_vault_key" "oai" {
  count        = local.needs_openai_kv ? 1 : 0
  name         = "encryption-key-openai"
  key_vault_id = azurerm_key_vault.oai[0].id
  key_type     = "RSA-HSM"
  key_size     = 2048
  key_opts     = ["unwrapKey", "wrapKey", "decrypt", "encrypt", "sign", "verify"]
  depends_on = [
    azurerm_role_assignment.current_oai_key_vault_admin,
    azurerm_role_assignment.admin_oai_key_vault_crypto,
  ]

  rotation_policy {
    automatic {
      time_before_expiry = "P30D"
    }

    expire_after         = "P90D"
    notify_before_expiry = "P29D"
  }
}

resource "azurerm_private_endpoint" "kv" {
  name                = local.key_vault_private_endpoint_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.kv.id
  tags                = var.tags
  private_service_connection {
    name                           = "cs-kv-psc"
    private_connection_resource_id = azurerm_key_vault.main.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "pdz-cs-kv"
    private_dns_zone_ids = [azurerm_private_dns_zone.kv.id]
  }
}

resource "azurerm_private_endpoint" "kvoai" {
  count               = local.needs_openai_kv ? 1 : 0
  name                = format("%s-oai", local.key_vault_private_endpoint_name)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.kv.id
  tags                = var.tags
  private_service_connection {
    name                           = "cs-kv-oai-psc"
    private_connection_resource_id = azurerm_key_vault.oai[0].id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "pdz-cs-kv-oai"
    private_dns_zone_ids = [azurerm_private_dns_zone.kv.id]
  }
}
