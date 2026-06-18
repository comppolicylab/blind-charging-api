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

resource "azurerm_monitor_action_group" "email_alerts" {
  count               = length(var.alert_emails) > 0 ? 1 : 0
  name                = lower(format("%s-email-alerts", local.name_prefix))
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "emailalerts"
  tags                = var.tags

  dynamic "email_receiver" {
    for_each = var.alert_emails

    content {
      name                    = format("email-alert-%s", email_receiver.key)
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}

resource "azurerm_monitor_action_group" "resource_owner_alerts" {
  name                = lower(format("%s-resource-owner-alerts", local.name_prefix))
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "owneralerts"
  tags                = var.tags

  arm_role_receiver {
    name                    = "email-resource-owner"
    role_id                 = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"
    use_common_alert_schema = true
  }
}

resource "azurerm_monitor_metric_alert" "mssql_low_storage" {
  count               = 1
  name                = lower(format("%s-sql-low-storage", local.name_prefix))
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_mssql_database.main.id]
  description         = "Alert when the MSSQL database storage usage is above ${var.mssql_low_storage_alert_threshold_percent}%."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.Sql/servers/databases"
    metric_name      = "storage_percent"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = var.mssql_low_storage_alert_threshold_percent
  }

  action {
    action_group_id = azurerm_monitor_action_group.resource_owner_alerts.id
  }

  dynamic "action" {
    for_each = azurerm_monitor_action_group.email_alerts

    content {
      action_group_id = action.value.id
    }
  }
}

resource "azurerm_monitor_metric_alert" "gateway_failed_requests" {
  count               = local.create_app_gateway ? 1 : 0
  name                = lower(format("%s-gateway-failed-requests", local.name_prefix))
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_application_gateway.public[0].id]
  description         = "Alert when the Application Gateway observes more than ${var.gateway_failed_requests_alert_threshold} failed requests in 15 minutes."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.Network/applicationGateways"
    metric_name      = "FailedRequests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = var.gateway_failed_requests_alert_threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.resource_owner_alerts.id
  }

  dynamic "action" {
    for_each = azurerm_monitor_action_group.email_alerts

    content {
      action_group_id = action.value.id
    }
  }
}

resource "azurerm_monitor_metric_alert" "container_app_replica_restarts" {
  count               = 1
  name                = lower(format("%s-container-app-restarts", local.name_prefix))
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_container_app.main.id]
  description         = "Alert when the Container App observes a replica restart count above ${var.container_app_replica_restart_alert_threshold}."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "RestartCount"
    aggregation      = "Maximum"
    operator         = "GreaterThan"
    threshold        = var.container_app_replica_restart_alert_threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.resource_owner_alerts.id
  }

  dynamic "action" {
    for_each = azurerm_monitor_action_group.email_alerts

    content {
      action_group_id = action.value.id
    }
  }
}

resource "azurerm_monitor_metric_alert" "redis_used_memory" {
  count               = 1
  name                = lower(format("%s-redis-used-memory", local.name_prefix))
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azapi_resource.redis.id]
  description         = "Alert when Azure Redis Cache used memory is above ${var.redis_used_memory_alert_threshold_percent}%."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = var.tags

  criteria {
    metric_namespace = local.redis_needs_enterprise_cache ? "Microsoft.Cache/redisEnterprise" : "Microsoft.Cache/redis"
    metric_name      = "usedmemorypercentage"
    aggregation      = "Maximum"
    operator         = "GreaterThan"
    threshold        = var.redis_used_memory_alert_threshold_percent
  }

  action {
    action_group_id = azurerm_monitor_action_group.resource_owner_alerts.id
  }

  dynamic "action" {
    for_each = azurerm_monitor_action_group.email_alerts

    content {
      action_group_id = action.value.id
    }
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "key_vault_certificate_near_expiry" {
  name                 = lower(format("%s-kv-cert-near-expiry", local.name_prefix))
  resource_group_name  = azurerm_resource_group.main.name
  location             = azurerm_resource_group.main.location
  scopes               = [azurerm_log_analytics_workspace.main.id]
  description          = "Alert when Key Vault emits a certificate near-expiry audit event."
  severity             = 2
  evaluation_frequency = "PT1H"
  window_duration      = "PT1H"
  enabled              = true
  tags                 = var.tags

  criteria {
    query = <<-QUERY
      AzureDiagnostics
      | where ResourceId =~ "${azurerm_key_vault.main.id}"
      | where Category == "AuditEvent"
      | where OperationName in~ ("CertificateNearExpiry", "CertificateNearExpiryEventGridNotification")
    QUERY

    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0
  }

  action {
    action_groups = concat(
      [azurerm_monitor_action_group.resource_owner_alerts.id],
      azurerm_monitor_action_group.email_alerts[*].id,
    )
  }
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
