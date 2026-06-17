locals {
  redis_resource_name          = replace(local.redis_cache_name, "-", "")
  redis_needs_enterprise_cache = var.redis_sku_family == "E" || var.redis_sku_family == "F"
  redis_is_managed_redis       = var.redis_sku_family == "M" || var.redis_sku_family == "B" || var.redis_sku_family == "X" || var.redis_sku_family == "A"
  redis_uses_single_database   = local.redis_needs_enterprise_cache || local.redis_is_managed_redis
  redis_uses_cluster_client    = local.redis_needs_enterprise_cache || (local.redis_is_managed_redis && var.redis_clustering_policy != "NoCluster")
  redis_enterprise_api_version = "2024-09-01-preview"
  redis_managed_api_version    = "2024-11-01"
  redis_sku_name = lookup({
    E = "Enterprise",
    F = "EnterpriseFlash",
    C = "Standard",
    P = "Premium",
    M = "MemoryOptimized",
    B = "Balanced",
    X = "ComputeOptimized",
    A = "FlashOptimized",
  }, var.redis_sku_family, "Standard")
  redis_enterprise_full_name = "${local.redis_sku_name}_${var.redis_sku_family}${var.redis_capacity_sku}"
  redis_managed_full_name    = "${local.redis_sku_name}_${var.redis_sku_family}${var.redis_capacity_sku}"
  redis_resource_type        = local.redis_needs_enterprise_cache ? "Microsoft.Cache/redisEnterprise@${local.redis_enterprise_api_version}" : "Microsoft.Cache/redis@${local.redis_managed_api_version}"
  redis_db_resource_type     = local.redis_needs_enterprise_cache ? "Microsoft.Cache/redisEnterprise/databases@${local.redis_enterprise_api_version}" : "Microsoft.Cache/redis/databases@${local.redis_managed_api_version}"

  // Request body for creating the Redis Enterprise cache
  redis_enterprise_body = {
    sku = {
      name     = local.redis_enterprise_full_name
      capacity = var.redis_enterprise_vms
    }

    properties = {
      encryption = {
        customerManagedKeyEncryption = {
          keyEncryptionKeyIdentity = {
            identityType                   = "userAssignedIdentity"
            userAssignedIdentityResourceId = azurerm_user_assigned_identity.admin.id
          }
          keyEncryptionKeyUrl = "${azurerm_key_vault.main.vault_uri}keys/${azurerm_key_vault_key.encryption.name}/${azurerm_key_vault_key.encryption.version}"
        }
      }
      minimumTlsVersion = "1.2"
    }

    zones = []
  }

  // Request body for creating the Redis Standard cache
  redis_standard_body = {
    zones = []
    properties = {
      enableNonSslPort = false

      publicNetworkAccess = "Disabled"
      sku = {
        name     = local.redis_sku_name
        family   = var.redis_sku_family
        capacity = var.redis_capacity_sku
      }

      minimumTlsVersion = "1.2"
    }
  }
}


// In situations where we need to guarantee that any disk writes are
// encrypted with a customer managed key, we have to use the Enterprise SKU.
// We also need to support some options that the `azurerm` provider does not
// provide for us. So, we create redis using the `azapi` provider for the
// maximum flexibility, and also easiest integration with supporting both types
// of Redis deployment (i.e., Standard and Enterprise).
resource "azapi_resource" "redis" {
  count = local.redis_is_managed_redis ? 0 : 1

  // Determine the type of Redis to create (Standard or Enterprise cluster)
  type = local.redis_resource_type
  name = local.redis_resource_name

  location  = azurerm_resource_group.main.location
  parent_id = azurerm_resource_group.main.id

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.admin.id]
  }

  body = [local.redis_enterprise_body, local.redis_standard_body][local.redis_needs_enterprise_cache ? 0 : 1]

  tags = var.tags

  response_export_values = {
    id       = "id",
    hostname = "properties.hostName"
    port     = "properties.sslPort"
  }

  replace_triggers_external_values = [
    local.redis_cache_name,
    local.redis_resource_type,
  ]

  timeouts {
    create = "45m"
    update = "45m"
    delete = "45m"
  }
}

resource "azurerm_managed_redis" "redis" {
  count = local.redis_is_managed_redis ? 1 : 0

  name                  = local.redis_resource_name
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  sku_name              = local.redis_managed_full_name
  public_network_access = "Disabled"
  tags                  = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.admin.id]
  }

  customer_managed_key {
    key_vault_key_id          = azurerm_key_vault_key.encryption.id
    user_assigned_identity_id = azurerm_user_assigned_identity.admin.id
  }

  default_database {
    access_keys_authentication_enabled = true
    clustering_policy                  = var.redis_clustering_policy
    eviction_policy                    = "VolatileLRU"
  }

  depends_on = [
    azurerm_role_assignment.admin_key_vault_crypto,
  ]

}

// For the Enterprise SKU, we need to create a database cluster separately.
resource "azapi_resource" "redis_dbs" {
  count     = local.redis_needs_enterprise_cache ? 1 : 0
  type      = local.redis_db_resource_type
  name      = "default"
  parent_id = azapi_resource.redis[0].id

  body = {
    properties = {
      clientProtocol   = "Encrypted"
      clusteringPolicy = "OSSCluster"
      evictionPolicy   = "VolatileLRU"
      persistence = {
        aofEnabled = false
        rdbEnabled = false
      }
    }
  }

  response_export_values = {
    id   = "id"
    port = "properties.port"
  }

  replace_triggers_refs = ["properties.clusteringPolicy"]

  timeouts {
    create = "45m"
    update = "30m"
  }
}

// List the access keys.
// TODO(jnu) phase out access key authentication so this is not necessary.
resource "azapi_resource_action" "redis_keys" {
  count = local.redis_is_managed_redis ? 0 : 1

  // Note that for enterprise cache we use the database resource, while for
  // standard cache we use the main redis resource for this operation.
  type        = local.redis_needs_enterprise_cache ? local.redis_db_resource_type : local.redis_resource_type
  resource_id = local.redis_needs_enterprise_cache ? azapi_resource.redis_dbs[0].id : azapi_resource.redis[0].id
  action      = "listKeys"
  response_export_values = {
    primarKey    = "primaryKey"
    secondaryKey = "secondaryKey"
  }
}


resource "azurerm_private_endpoint" "redis" {
  name                = local.redis_private_endpoint_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.redis.id
  tags                = var.tags

  private_service_connection {
    name                           = "redis-psc"
    private_connection_resource_id = local.redis_is_managed_redis ? azurerm_managed_redis.redis[0].id : azapi_resource.redis[0].id
    subresource_names = [
      local.redis_needs_enterprise_cache ? "redisEnterprise" : "redisCache"
    ]
    is_manual_connection = false
  }

  private_dns_zone_group {
    name                 = "pdz-redis"
    private_dns_zone_ids = [azurerm_private_dns_zone.redis.id]
  }
}

locals {
  redis_fqdn       = local.redis_is_managed_redis ? azurerm_managed_redis.redis[0].hostname : azapi_resource.redis[0].output.properties.hostName
  redis_access_key = local.redis_is_managed_redis ? azurerm_managed_redis.redis[0].default_database[0].primary_access_key : azapi_resource_action.redis_keys[0].output.primaryKey
  redis_port       = local.redis_is_managed_redis ? azurerm_managed_redis.redis[0].default_database[0].port : (local.redis_needs_enterprise_cache ? azapi_resource.redis_dbs[0].output.properties.port : azapi_resource.redis[0].output.properties.sslPort)
}
