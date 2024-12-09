resource "azurerm_firewall" "main" {
  name                = local.firewall_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags
  sku_name            = "AZFW_VNet"
  sku_tier            = "Standard"
  dns_servers         = var.dns_servers
  dns_proxy_enabled   = true

  ip_configuration {
    name                 = "main"
    subnet_id            = azurerm_subnet.firewall.id
    public_ip_address_id = azurerm_public_ip.firewall.id
  }
}

resource "azurerm_firewall_network_rule_collection" "required" {
  name                = format("%s-fw-rules-required", local.name_prefix)
  resource_group_name = azurerm_resource_group.main.name
  azure_firewall_name = azurerm_firewall.main.name
  priority            = 100
  action              = "Allow"

  rule {
    name              = "Outbound access to required HKS-RBC services"
    source_addresses  = var.app_subnet_address_space
    destination_fqdns = local.firewall_required_domains
    destination_ports = ["443", "22"]
    protocols         = ["TCP"]
  }
}

resource "azurerm_firewall_application_rule_collection" "custom" {
  count               = local.firewall_custom_outbound ? 1 : 0
  name                = format("%s-fw-rules-custom", local.name_prefix)
  resource_group_name = azurerm_resource_group.main.name
  azure_firewall_name = azurerm_firewall.main.name
  priority            = 100
  action              = "Allow"

  rule {
    name             = "Outbound access to site-specific services"
    source_addresses = var.app_subnet_address_space
    target_fqdns     = var.firewall_allowed_domains
    protocol {
      port = "443"
      type = "Https"
    }
  }
}

resource "azurerm_route_table" "main" {
  name                = format("%s-main-rt", local.name_prefix)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags

  # Route everything through the firewall
  route {
    name                   = "default"
    address_prefix         = "0.0.0.0/0"
    next_hop_type          = "VirtualAppliance"
    next_hop_in_ip_address = azurerm_firewall.main.ip_configuration[0].private_ip_address
  }
}

resource "azurerm_route_table" "gateway" {
  name                = format("%s-gateway-rt", local.name_prefix)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags

  # Gateway traffic needs to go to the internet (*not* the firewall)
  route {
    name           = "default"
    address_prefix = "0.0.0.0/0"
    next_hop_type  = "Internet"
  }
}

resource "azurerm_subnet_route_table_association" "default" {
  subnet_id      = azurerm_subnet.default.id
  route_table_id = azurerm_route_table.main.id
}

resource "azurerm_subnet_route_table_association" "app" {
  subnet_id      = azurerm_subnet.app.id
  route_table_id = azurerm_route_table.main.id
}

resource "azurerm_subnet_route_table_association" "openai" {
  subnet_id      = azurerm_subnet.openai.id
  route_table_id = azurerm_route_table.main.id
}

resource "azurerm_subnet_route_table_association" "redis" {
  subnet_id      = azurerm_subnet.redis.id
  route_table_id = azurerm_route_table.main.id
}

resource "azurerm_subnet_route_table_association" "fr" {
  subnet_id      = azurerm_subnet.fr.id
  route_table_id = azurerm_route_table.main.id
}

resource "azurerm_subnet_route_table_association" "db" {
  subnet_id      = azurerm_subnet.db.id
  route_table_id = azurerm_route_table.main.id
}

resource "azurerm_subnet_route_table_association" "gateway" {
  subnet_id      = azurerm_subnet.gateway.id
  route_table_id = azurerm_route_table.gateway.id
}

resource "azurerm_subnet_route_table_association" "gateway-pl" {
  subnet_id      = azurerm_subnet.gateway-pl.id
  route_table_id = azurerm_route_table.main.id
}

resource "azurerm_subnet_route_table_association" "fs" {
  count          = var.enable_research_env ? 1 : 0
  subnet_id      = azurerm_subnet.fs[0].id
  route_table_id = azurerm_route_table.main.id
}
