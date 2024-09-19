resource "azurerm_cognitive_account" "fr" {
  name                  = format("%s-rbc-cs-fr", var.partner)
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  sku_name              = "S0"
  kind                  = "FormRecognizer"
  tags                  = var.tags
  custom_subdomain_name = format("%s-rbc-cs-fr", var.partner)
}

resource "azurerm_private_endpoint" "fr" {
  name                = format("%s-rbc-cs-fr-pe", var.partner)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.fr.id
  private_service_connection {
    name                           = "cs-fr-psc"
    private_connection_resource_id = azurerm_cognitive_account.fr.id
    subresource_names              = ["account"]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "pdz-cs-fr"
    private_dns_zone_ids = [azurerm_private_dns_zone.fr.id]
  }
}

locals {
  fr_endpoint = azurerm_cognitive_account.fr.endpoint
}
