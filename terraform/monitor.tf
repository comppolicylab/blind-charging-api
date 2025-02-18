resource "azurerm_storage_account" "analytics" {
  name                     = local.analytics_storage_account_name
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  tags                     = var.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.admin.id]
  }

  customer_managed_key {
    key_vault_key_id          = azurerm_key_vault_key.encryption.versionless_id
    user_assigned_identity_id = azurerm_user_assigned_identity.admin.id
  }

  infrastructure_encryption_enabled = true
  public_network_access_enabled     = true
  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices", "Logging", "Metrics"]
    virtual_network_subnet_ids = [azurerm_subnet.monitor.id]
  }
}

resource "azurerm_log_analytics_workspace" "main" {
  name                       = local.log_analytics_workspace_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  internet_ingestion_enabled = false
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.admin.id]
  }
  sku                  = "PerGB2018"
  retention_in_days    = 30
  tags                 = var.tags
  cmk_for_query_forced = true
}

resource "azurerm_log_analytics_linked_storage_account" "logs" {
  data_source_type      = "CustomLogs"
  resource_group_name   = azurerm_resource_group.main.name
  workspace_resource_id = azurerm_log_analytics_workspace.main.id
  storage_account_ids   = [azurerm_storage_account.analytics.id]
}

resource "azurerm_log_analytics_linked_storage_account" "query" {
  data_source_type      = "Query"
  resource_group_name   = azurerm_resource_group.main.name
  workspace_resource_id = azurerm_log_analytics_workspace.main.id
  storage_account_ids   = [azurerm_storage_account.analytics.id]
}

resource "azurerm_log_analytics_linked_storage_account" "ingestion" {
  data_source_type      = "Ingestion"
  resource_group_name   = azurerm_resource_group.main.name
  workspace_resource_id = azurerm_log_analytics_workspace.main.id
  storage_account_ids   = [azurerm_storage_account.analytics.id]
}

resource "azurerm_log_analytics_linked_storage_account" "alerts" {
  data_source_type      = "Alerts"
  resource_group_name   = azurerm_resource_group.main.name
  workspace_resource_id = azurerm_log_analytics_workspace.main.id
  storage_account_ids   = [azurerm_storage_account.analytics.id]
}

resource "azurerm_application_insights" "main" {
  name                       = local.application_insights_name
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  application_type           = "web"
  workspace_id               = azurerm_log_analytics_workspace.main.id
  tags                       = var.tags
  internet_ingestion_enabled = false
}

resource "azurerm_monitor_private_link_scope" "main" {
  name                  = lower(format("%s-ampls", local.application_insights_name))
  resource_group_name   = azurerm_resource_group.main.name
  ingestion_access_mode = "PrivateOnly"
}

resource "azurerm_monitor_private_link_scoped_service" "main" {
  name                = lower(format("%s-amplsservice", local.application_insights_name))
  resource_group_name = azurerm_resource_group.main.name
  scope_name          = azurerm_monitor_private_link_scope.main.name
  linked_resource_id  = azurerm_application_insights.main.id
}

resource "azurerm_monitor_private_link_scoped_service" "law" {
  name                = lower(format("%s-amplsservice", local.log_analytics_workspace_name))
  resource_group_name = azurerm_resource_group.main.name
  scope_name          = azurerm_monitor_private_link_scope.main.name
  linked_resource_id  = azurerm_log_analytics_workspace.main.id
}

resource "azurerm_private_endpoint" "monitor" {
  name                = local.monitor_private_endpoint_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  subnet_id           = azurerm_subnet.monitor.id
  tags                = var.tags

  private_service_connection {
    name                           = "monitor-psc"
    private_connection_resource_id = azurerm_monitor_private_link_scope.main.id
    subresource_names              = ["azuremonitor"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "pdz-monitor"
    private_dns_zone_ids = [azurerm_private_dns_zone.monitor.id]
  }
}
