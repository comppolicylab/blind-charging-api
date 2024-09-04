resource "azurerm_mssql_server" "main" {
  name                         = format("%s-rbc-sql", var.partner)
  resource_group_name          = azurerm_resource_group.main.name
  location                     = azurerm_resource_group.main.location
  version                      = "12.0"
  administrator_login          = var.db_user
  administrator_login_password = var.db_password
  tags                         = var.tags
  # NOTE: "public" here plus the "virtual_network_rule" below corresponds
  # to where the Azure UI says "Select networks" can access the server.
  # It is *not* completely public.
  public_network_access_enabled = true
}

resource "azurerm_mssql_database" "main" {
  name           = format("%s-rbc-db", var.partner)
  server_id      = azurerm_mssql_server.main.id
  collation      = "SQL_Latin1_General_CP1_CI_AS"
  max_size_gb    = 5
  sku_name       = "S0"
  zone_redundant = false
  tags           = var.tags
  enclave_type   = "VBS"
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_mssql_firewall_rule" "app" {
  name      = format("%s-rbc-db-fw", var.partner)
  server_id = azurerm_mssql_server.main.id
  # NOTE: We open up the firewall to any IP address, but access
  # is still constrained by the virtual network rule below. This
  # means that the database is still only accessible within the VNet.
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_mssql_virtual_network_rule" "app" {
  name      = format("%s-rbc-db-vn", var.partner)
  server_id = azurerm_mssql_server.main.id
  subnet_id = azurerm_subnet.app.id
}

resource "azurerm_private_endpoint" "mssql" {
  name                = format("%s-rbc-mssql-pe", var.partner)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.default.id
  private_service_connection {
    name                           = "mssql-psc"
    private_connection_resource_id = azurerm_mssql_server.main.id
    subresource_names              = ["SqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-mssql"
    private_dns_zone_ids = [azurerm_private_dns_zone.main.id]
  }
}
