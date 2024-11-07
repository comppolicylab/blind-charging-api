resource "azurerm_mssql_server" "main" {
  name                         = local.mssql_server_name
  resource_group_name          = azurerm_resource_group.main.name
  location                     = azurerm_resource_group.main.location
  version                      = "12.0"
  administrator_login          = var.db_user
  administrator_login_password = var.db_password
  tags                         = var.tags
  # All access to the database must go through private endpoint.
  public_network_access_enabled = false
}

resource "azurerm_mssql_database" "main" {
  name           = local.mssql_database_name
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

resource "azurerm_private_endpoint" "mssql" {
  name                = local.mssql_private_endpoint_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.db.id
  tags                = var.tags

  private_service_connection {
    name                           = "mssql-psc"
    private_connection_resource_id = azurerm_mssql_server.main.id
    subresource_names              = ["SqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-mssql"
    private_dns_zone_ids = [azurerm_private_dns_zone.mssql.id]
  }
}

locals {
  mssql_fqdn = azurerm_mssql_server.main.fully_qualified_domain_name
}
