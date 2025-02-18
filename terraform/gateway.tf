locals {
  create_app_gateway                  = var.expose_app_to_private_network || var.expose_app_to_public_internet
  public_frontend_ip_config_name      = format("%s-app-gw-feip", local.name_prefix)
  private_frontend_ip_config_name     = format("%s-app-gw-feip-priv", local.name_prefix)
  frontend_https_port_name            = format("%s-app-gw-feport-https", local.name_prefix)
  frontend_http_port_name             = format("%s-app-gw-feport-http", local.name_prefix)
  https_listener_name                 = format("%s-app-gw-listener", local.name_prefix)
  http_listener_name                  = format("%s-app-gw-listener-http", local.name_prefix)
  private_http_listener_name          = format("%s-app-gw-listener-http-priv", local.name_prefix)
  http_to_https_redirect_name         = format("%s-app-gw-redirect", local.name_prefix)
  backend_address_pool_name           = format("%s-app-gw-be-pool", local.name_prefix)
  backend_http_settings_name          = format("%s-app-gw-be-settings", local.name_prefix)
  ssl_cert_name                       = format("%s-app-gw-cert", local.name_prefix)
  probe_name                          = format("%s-app-gw-probe", local.name_prefix)
  research_backend_address_pool_name  = format("%s-app-gw-be-pool-research", local.name_prefix)
  research_backend_http_settings_name = format("%s-app-gw-be-settings-research", local.name_prefix)
  research_probe_name                 = format("%s-app-gw-probe-research", local.name_prefix)
  research_rewrite_rule_set_name      = format("%s-app-gw-rewrite-rule-set-research", local.name_prefix)
  research_add_slash_redirect_name    = format("%s-app-gw-redirect-research-root", local.name_prefix)
  url_path_map_name                   = format("%s-app-gw-url-path-map", local.name_prefix)
}

resource "azurerm_web_application_firewall_policy" "gateway" {
  count               = var.waf ? 1 : 0
  name                = format("%s-app-gw-waf-policy", local.name_prefix)
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  # Allow inbound traffic from IPs in `var.allowed_ips`
  custom_rules {
    action    = "Block"
    name      = "BlockNonAllowedIPs"
    priority  = 100
    rule_type = "MatchRule"
    match_conditions {
      match_variables {
        variable_name = "RemoteAddr"
      }
      operator = "IPMatch"
      # If the variable is set to empty, we want to DENY ALL IPs.
      # To do so, we will match everything *without* negation.
      # Alternatively, if the allowed_ips list is non-empty, we want to ALLOW ONLY those IPs.
      # To do so, we will match everything *with* negate.
      match_values       = length(var.allowed_ips) > 0 ? var.allowed_ips : ["0.0.0.0/0"]
      negation_condition = length(var.allowed_ips) > 0 ? true : false
    }
  }

  managed_rules {
    managed_rule_set {
      version = "3.2"
    }
  }

  policy_settings {
    enabled                  = true
    request_body_check       = false
    request_body_enforcement = false
    mode                     = "Prevention"
  }
}

resource "azurerm_application_gateway" "public" {
  count               = local.create_app_gateway ? 1 : 0
  name                = local.app_gateway_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags

  sku {
    name     = var.waf ? "WAF_v2" : "Standard_v2"
    tier     = var.waf ? "WAF_v2" : "Standard_v2"
    capacity = 2
  }

  firewall_policy_id = var.waf ? azurerm_web_application_firewall_policy.gateway[0].id : null

  identity {
    type = "UserAssigned"
    identity_ids = [
      azurerm_user_assigned_identity.gateway.id,
    ]
  }

  gateway_ip_configuration {
    name      = format("%s-app-gw-ip", local.name_prefix)
    subnet_id = azurerm_subnet.gateway.id
  }

  private_link_configuration {
    name = local.private_link_configuration_name
    ip_configuration {
      name                          = format("%s-app-gw-plcipcfg", local.name_prefix)
      subnet_id                     = azurerm_subnet.gateway-pl.id
      private_ip_address_allocation = "Dynamic"
      primary                       = true
    }
  }

  # NOTE: we need to create a public IP even if the app is *not* exposed
  # to the public internet, because V2 gateways always require a public IP.
  #
  # There is a preview "private" deployment for Gateways that will lift this
  # requirement, but it's not yet available in GovCloud.
  # See:
  # https://learn.microsoft.com/en-us/azure/application-gateway/application-gateway-private-deployment?tabs=portal
  frontend_ip_configuration {
    name                 = local.public_frontend_ip_config_name
    public_ip_address_id = azurerm_public_ip.gateway.id
  }

  frontend_ip_configuration {
    name                            = local.private_frontend_ip_config_name
    private_link_configuration_name = local.private_link_configuration_name
    private_ip_address_allocation   = "Static"
    private_ip_address              = var.gateway_private_ip_address
    subnet_id                       = azurerm_subnet.gateway.id
  }

  // Set up an http -> https redirect

  frontend_port {
    name = local.frontend_http_port_name
    port = 80
  }

  # Only set up public IP listener if the app is exposed to the public internet
  dynamic "http_listener" {
    for_each = var.expose_app_to_public_internet ? [1] : []
    content {
      name                           = local.http_listener_name
      frontend_ip_configuration_name = local.public_frontend_ip_config_name
      frontend_port_name             = local.frontend_http_port_name
      protocol                       = "Http"
    }
  }

  http_listener {
    name                           = local.private_http_listener_name
    frontend_ip_configuration_name = local.private_frontend_ip_config_name
    frontend_port_name             = local.has_cert ? local.frontend_https_port_name : local.frontend_http_port_name
    protocol                       = local.has_cert ? "Https" : "Http"
    ssl_certificate_name           = local.has_cert ? local.ssl_cert_name : null
  }

  dynamic "request_routing_rule" {
    for_each = var.expose_app_to_public_internet ? [1] : []
    content {
      priority                    = 1
      name                        = format("%s-app-gw-rr-http-upgrade", local.name_prefix)
      rule_type                   = "Basic"
      redirect_configuration_name = local.http_to_https_redirect_name
      http_listener_name          = local.http_listener_name
    }
  }

  dynamic "redirect_configuration" {
    for_each = var.expose_app_to_public_internet ? [1] : []
    content {
      name                 = local.http_to_https_redirect_name
      redirect_type        = "Permanent"
      target_listener_name = local.https_listener_name
      include_path         = true
      include_query_string = true
    }
  }

  redirect_configuration {
    name          = local.research_add_slash_redirect_name
    redirect_type = "Permanent"
    # NOTE: the `var.host` needs to be supplied in vars if the research environment is to be reachable.
    # If it's not defined, we avoid a cryptic error here by putting a placeholder value.
    # (Of course, the rule will not do anything useful in that case.)
    target_url           = "https://${coalesce(var.host, "localhost")}/research/"
    include_path         = false
    include_query_string = true
  }

  // Set up the https listener

  frontend_port {
    name = local.frontend_https_port_name
    port = 443
  }

  dynamic "ssl_certificate" {
    for_each = local.has_cert ? [1] : []
    content {
      name                = local.ssl_cert_name
      key_vault_secret_id = azurerm_key_vault_certificate.app_gateway[0].versionless_secret_id
    }
  }

  // Only set up public IP listener if the app is exposed to the public internet
  dynamic "http_listener" {
    for_each = var.expose_app_to_public_internet ? [1] : []
    content {
      name                           = local.https_listener_name
      frontend_ip_configuration_name = local.public_frontend_ip_config_name
      frontend_port_name             = local.frontend_https_port_name
      protocol                       = "Https"
      ssl_certificate_name           = local.ssl_cert_name
    }
  }

  dynamic "request_routing_rule" {
    for_each = var.expose_app_to_public_internet ? [1] : []
    content {
      priority           = 2
      name               = format("%s-app-gw-rr", local.name_prefix)
      rule_type          = "PathBasedRouting"
      url_path_map_name  = local.url_path_map_name
      http_listener_name = local.https_listener_name
    }
  }

  request_routing_rule {
    priority           = 3
    name               = format("%s-app-gw-rr-priv", local.name_prefix)
    rule_type          = "PathBasedRouting"
    url_path_map_name  = local.url_path_map_name
    http_listener_name = local.private_http_listener_name
  }

  url_path_map {
    name                               = local.url_path_map_name
    default_backend_address_pool_name  = local.backend_address_pool_name
    default_backend_http_settings_name = local.backend_http_settings_name

    path_rule {
      name                       = "api"
      paths                      = ["/api*"]
      backend_address_pool_name  = local.backend_address_pool_name
      backend_http_settings_name = local.backend_http_settings_name
    }

    # For the research environment, make sure /research redirects to /research/
    # And then rewrite the path to remove the `"research"` prefix.
    dynamic "path_rule" {
      for_each = var.expose_research_env ? [1] : []
      content {
        name                        = "research-root"
        paths                       = ["/research"]
        redirect_configuration_name = local.research_add_slash_redirect_name
      }
    }

    dynamic "path_rule" {
      for_each = var.expose_research_env ? [1] : []
      content {
        name                       = "research"
        paths                      = ["/research/*"]
        backend_address_pool_name  = local.research_backend_address_pool_name
        backend_http_settings_name = local.research_backend_http_settings_name
        rewrite_rule_set_name      = local.research_rewrite_rule_set_name
      }
    }
  }

  // Set up the app backend

  backend_address_pool {
    name  = local.backend_address_pool_name
    fqdns = [local.app_fqdn]
  }

  backend_http_settings {
    name                                = local.backend_http_settings_name
    probe_name                          = local.probe_name
    cookie_based_affinity               = "Disabled"
    port                                = 443
    protocol                            = "Https"
    request_timeout                     = 20
    pick_host_name_from_backend_address = true
  }

  probe {
    name                                      = local.probe_name
    protocol                                  = "Https"
    pick_host_name_from_backend_http_settings = true
    path                                      = "/api/v1/health"
    interval                                  = 30
    timeout                                   = 30
    unhealthy_threshold                       = 3
    match {
      status_code = [
        "200-399",
      ]
    }
  }

  // Set up the research environment routing if requested

  dynamic "backend_http_settings" {
    for_each = var.expose_research_env ? [1] : []
    content {
      name                                = local.research_backend_http_settings_name
      probe_name                          = local.research_probe_name
      cookie_based_affinity               = "Disabled"
      port                                = 443
      protocol                            = "Https"
      request_timeout                     = 20
      pick_host_name_from_backend_address = true
    }
  }

  dynamic "backend_address_pool" {
    for_each = var.expose_research_env ? [1] : []
    content {
      name  = local.research_backend_address_pool_name
      fqdns = [local.research_app_fqdn]
    }
  }

  dynamic "probe" {
    for_each = var.expose_research_env ? [1] : []
    content {
      name                                      = local.research_probe_name
      protocol                                  = "Https"
      pick_host_name_from_backend_http_settings = true
      path                                      = "/unsupported_browser.htm"
      interval                                  = 30
      timeout                                   = 30
      unhealthy_threshold                       = 3
      match {
        status_code = [
          "200-399",
        ]
      }
    }
  }

  dynamic "rewrite_rule_set" {
    for_each = var.expose_research_env ? [1] : []
    content {
      name = local.research_rewrite_rule_set_name

      rewrite_rule {
        name          = "research"
        rule_sequence = 2
        condition {
          ignore_case = false
          pattern     = "/research(.*)"
          negate      = false
          variable    = "var_request_uri"
        }
        url {
          path = "{var_request_uri_1}"
        }
        request_header_configuration {
          header_name  = "X-RStudio-Request"
          header_value = format("https://%s/research", var.host)
        }
        request_header_configuration {
          header_name  = "X-Forwarded-Host"
          header_value = var.host
        }
        request_header_configuration {
          header_name  = "X-Forwarded-Proto"
          header_value = "https"
        }
        request_header_configuration {
          header_name  = "X-RStudio-Root-Path"
          header_value = "/research"
        }
      }
    }
  }
}
