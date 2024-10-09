locals {
  research_app_name      = format("%s-rbc-research", var.partner)
  research_app_fqdn      = format("%s.%s", local.research_app_name, azurerm_container_app_environment.main.default_domain)
  storage_domain         = "file.core.${local.is_gov_cloud ? "usgovcloudapi.net" : "windows.net"}"
  research_smb_share_url = format("//%s.%s/%s", azurerm_storage_account.research[0].name, local.storage_domain, azurerm_storage_share.research[0].name)
}

resource "azurerm_storage_account" "research" {
  count                    = var.enable_research_env ? 1 : 0
  name                     = replace(format("%srbcdata", var.partner), "-", "")
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  tags                     = var.tags
}

resource "azurerm_storage_share" "research" {
  count                = var.enable_research_env ? 1 : 0
  name                 = "rbcdatafs"
  storage_account_name = azurerm_storage_account.research[0].name
  quota                = 10 # Gigabytes
}

resource "azurerm_container_app_environment_storage" "research" {
  count                        = var.enable_research_env ? 1 : 0
  name                         = format("%s-data-caes", local.research_app_name)
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.research[0].name
  share_name                   = azurerm_storage_share.research[0].name
  access_key                   = azurerm_storage_account.research[0].primary_access_key
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app" "research" {
  count                        = var.enable_research_env ? 1 : 0
  name                         = local.research_app_name
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  tags                         = var.tags
  revision_mode                = "Single"

  timeouts {
    create = "30m"
    update = "30m"
  }

  identity {
    type = "SystemAssigned"
  }

  secret {
    name  = "registry-password"
    value = var.registry_password
  }

  secret {
    name  = "research-password"
    value = var.research_password
  }

  secret {
    name  = "smb-password"
    value = azurerm_storage_account.research[0].primary_access_key
  }

  registry {
    server               = var.research_image_registry
    username             = var.partner
    password_secret_name = "registry-password"
  }

  ingress {
    allow_insecure_connections = false
    target_port                = 8787
    external_enabled           = true
    transport                  = "http"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {

    min_replicas = 1
    max_replicas = 1

    container {
      name   = "rbc-research"
      image  = local.research_image_tag
      cpu    = 2.0
      memory = "4Gi"

      env {
        name        = "PASSWORD"
        secret_name = "research-password"
      }

      env {
        name  = "SMB_MOUNT_PATH"
        value = "/data"
      }

      env {
        name  = "SMB_SHARE_URL"
        value = local.research_smb_share_url
      }

      env {
        name  = "SMB_USER"
        value = azurerm_storage_account.research[0].name
      }

      env {
        name        = "SMB_PASSWORD"
        secret_name = "smb-password"
      }

      liveness_probe {
        host             = "127.0.0.1"
        path             = "/unsupported_browser.htm"
        port             = 8787
        transport        = "HTTP"
        initial_delay    = 5
        interval_seconds = 15
      }
    }
  }
}
