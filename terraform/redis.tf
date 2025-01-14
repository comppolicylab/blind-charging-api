resource "azurerm_redis_cache" "main" {
  name                 = local.redis_cache_name
  resource_group_name  = azurerm_resource_group.main.name
  location             = azurerm_resource_group.main.location
  capacity             = var.redis_capacity_sku
  family               = "C"
  sku_name             = "Standard"
  non_ssl_port_enabled = false
  tags                 = var.tags
}

resource "azurerm_private_endpoint" "redis" {
  name                = local.redis_private_endpoint_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.redis.id
  tags                = var.tags

  private_service_connection {
    name                           = "redis-psc"
    private_connection_resource_id = azurerm_redis_cache.main.id
    subresource_names              = ["redisCache"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-redis"
    private_dns_zone_ids = [azurerm_private_dns_zone.redis.id]
  }
}

locals {
  redis_fqdn = azurerm_redis_cache.main.hostname
}
