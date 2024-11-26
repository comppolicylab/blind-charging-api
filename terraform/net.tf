resource "azurerm_virtual_network" "main" {
  name                = local.virtual_network_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  address_space       = var.virtual_network_address_space
  tags                = var.tags
}

resource "azurerm_subnet" "default" {
  name                                          = var.default_subnet_name
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.default_subnet_address_space
  private_link_service_network_policies_enabled = false
  default_outbound_access_enabled               = false
}

resource "azurerm_subnet" "app" {
  name                                          = var.app_subnet_name
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.app_subnet_address_space
  private_endpoint_network_policies             = "Enabled"
  private_link_service_network_policies_enabled = true
  default_outbound_access_enabled               = true

  delegation {
    name = "rbc-app-env"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "redis" {
  name                                          = var.redis_subnet_name
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.redis_subnet_address_space
  private_link_service_network_policies_enabled = true
  default_outbound_access_enabled               = false
}

resource "azurerm_subnet" "fr" {
  name                                          = var.form_recognizer_subnet_name
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.form_recognizer_subnet_address_space
  private_link_service_network_policies_enabled = true
  default_outbound_access_enabled               = false
}

resource "azurerm_subnet" "db" {
  name                                          = var.database_subnet_name
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.database_subnet_address_space
  private_link_service_network_policies_enabled = true
  default_outbound_access_enabled               = false
}

resource "azurerm_subnet" "openai" {
  name                                          = var.openai_subnet_name
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.openai_subnet_address_space
  private_link_service_network_policies_enabled = true
  default_outbound_access_enabled               = false
}

resource "azurerm_subnet" "gateway" {
  name                                          = var.gateway_subnet_name
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.gateway_subnet_address_space
  private_link_service_network_policies_enabled = true
  default_outbound_access_enabled               = false
}

resource "azurerm_subnet" "gateway-pl" {
  name                 = var.gateway_private_link_subnet_name
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = var.gateway_private_link_subnet_address_space
  # Confusingly, this must be false to enable private link.
  private_link_service_network_policies_enabled = false
  default_outbound_access_enabled               = true
}

resource "azurerm_subnet" "fs" {
  count                                         = var.enable_research_env ? 1 : 0
  name                                          = var.file_storage_subnet_name
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.file_storage_subnet_address_space
  private_link_service_network_policies_enabled = true
  default_outbound_access_enabled               = false
}

resource "azurerm_subnet" "firewall" {
  name                                          = "AzureFirewallSubnet"
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = var.firewall_subnet_address_space
  private_link_service_network_policies_enabled = false
  default_outbound_access_enabled               = false
}

resource "azurerm_private_dns_zone" "openai" {
  name                = "privatelink.openai.azure.${local.is_gov_cloud ? "us" : "com"}"
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone" "fr" {
  name                = "privatelink.cognitiveservices.azure.${local.is_gov_cloud ? "us" : "com"}"
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone" "mssql" {
  name                = "privatelink.database.${local.is_gov_cloud ? "usgovcloudapi.net" : "windows.net"}"
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone" "redis" {
  name                = "privatelink.redis.cache.${local.is_gov_cloud ? "usgovcloudapi.net" : "windows.net"}"
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone" "fs" {
  count               = var.enable_research_env ? 1 : 0
  name                = "privatelink.file.core.${local.is_gov_cloud ? "usgovcloudapi.net" : "windows.net"}"
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_zone" "app" {
  name                = azurerm_container_app_environment.main.default_domain
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_private_dns_a_record" "app_wildcard" {
  name                = "*"
  zone_name           = azurerm_private_dns_zone.app.name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 300
  records             = [azurerm_container_app_environment.main.static_ip_address]
  tags                = var.tags
}

resource "azurerm_private_dns_a_record" "app_exact" {
  name                = "@"
  zone_name           = azurerm_private_dns_zone.app.name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 300
  records             = [azurerm_container_app_environment.main.static_ip_address]
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "app" {
  name                  = format("%s-app-dns-link", local.name_prefix)
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.app.name
  virtual_network_id    = azurerm_virtual_network.main.id
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "openai" {
  name                  = format("%s-openai-dns-link", local.name_prefix)
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.openai.name
  virtual_network_id    = azurerm_virtual_network.main.id
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "fr" {
  name                  = format("%s-fr-dns-link", local.name_prefix)
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.fr.name
  virtual_network_id    = azurerm_virtual_network.main.id
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "mssql" {
  name                  = format("%s-mssql-dns-link", local.name_prefix)
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.mssql.name
  virtual_network_id    = azurerm_virtual_network.main.id
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "redis" {
  name                  = format("%s-redis-dns-link", local.name_prefix)
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.redis.name
  virtual_network_id    = azurerm_virtual_network.main.id
  tags                  = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "fs" {
  count                 = var.enable_research_env ? 1 : 0
  name                  = format("%s-fs-dns-link", local.name_prefix)
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.fs[0].name
  virtual_network_id    = azurerm_virtual_network.main.id
  tags                  = var.tags
}

# NOTE: Azure requires Gateways to have a public IP, even if they
# won't be reachable on the public internet.
resource "azurerm_public_ip" "gateway" {
  name                = format("%s-gateway-ip", local.name_prefix)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_public_ip" "firewall" {
  name                = format("%s-firewall-ip", local.name_prefix)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_virtual_network_peering" "app_to_cms" {
  count                        = var.peered_network_id != null ? 1 : 0
  name                         = format("%s-app-to-cms-peering", local.name_prefix)
  resource_group_name          = azurerm_resource_group.main.name
  virtual_network_name         = azurerm_virtual_network.main.name
  remote_virtual_network_id    = var.peered_network_id
  allow_virtual_network_access = true
}
