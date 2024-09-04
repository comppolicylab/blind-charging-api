resource "azurerm_virtual_network" "main" {
  name                = format("%s-rbc-vnet", var.partner)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  address_space       = ["10.0.0.0/16"]
  tags                = var.tags
}

resource "azurerm_subnet" "default" {
  name                                          = "default"
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = ["10.0.0.0/24"]
  service_endpoints                             = ["Microsoft.CognitiveServices", "Microsoft.Sql"]
  private_link_service_network_policies_enabled = true
  default_outbound_access_enabled               = true
}

resource "azurerm_subnet" "app" {
  name                                          = "app"
  resource_group_name                           = azurerm_resource_group.main.name
  virtual_network_name                          = azurerm_virtual_network.main.name
  address_prefixes                              = ["10.0.1.0/24"]
  service_endpoints                             = ["Microsoft.CognitiveServices", "Microsoft.Sql"]
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

resource "azurerm_private_dns_zone" "main" {
  name                = format("%s.rbc.cpl.azure.com", var.partner)
  resource_group_name = azurerm_resource_group.main.name
}

resource "azurerm_private_dns_zone_virtual_network_link" "main" {
  name                  = format("%s-rbc-dns-link", var.partner)
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.main.name
  virtual_network_id    = azurerm_virtual_network.main.id
}
