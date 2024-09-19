locals {
  app_gateway_name                = format("%s-rbc-app-gw", var.partner)
  frontend_ip_config_name         = format("%s-rbc-app-gw-feip", var.partner)
  frontend_https_port_name        = format("%s-rbc-app-gw-feport-http", var.partner)
  https_listener_name             = format("%s-rbc-app-gw-listener", var.partner)
  backend_address_pool_name       = format("%s-rbc-app-gw-be-pool", var.partner)
  backend_http_settings_name      = format("%s-rbc-app-gw-be-settings", var.partner)
  ssl_cert_name                   = format("%s-rbc-app-gw-cert", var.partner)
  private_link_configuration_name = format("%s-rbc-app-gw-plc", var.partner)
  probe_name                      = format("%s-rbc-app-gw-probe", var.partner)
}


### SHADY CERTIFICATE THINGS ###
# NOTE(jnu) -- This self-signed certificate is ONLY useful for bootstrapping
# a quick environment for testing. In production, exposed apps need to use
# a real certificate issued for a real custom domain.
resource "tls_private_key" "app_gateway" {
  count     = var.expose_app ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "tls_self_signed_cert" "app_gateway" {
  count           = var.expose_app ? 1 : 0
  private_key_pem = tls_private_key.app_gateway[0].private_key_pem
  subject {
    common_name  = local.app_fqdn
    organization = format("%s Race Blind Charging", var.partner)
  }
  validity_period_hours = 8760
  allowed_uses = [
    "key_encipherment",
    "digital_signature",
    "server_auth",
  ]
}

resource "pkcs12_from_pem" "app_gateway" {
  count           = var.expose_app ? 1 : 0
  password        = var.ssl_cert_password
  private_key_pem = tls_private_key.app_gateway[0].private_key_pem
  cert_pem        = tls_self_signed_cert.app_gateway[0].cert_pem
}

### END SHADY CERTIFICATE THINGS ###

resource "azurerm_application_gateway" "public" {
  count               = var.expose_app ? 1 : 0
  name                = local.app_gateway_name
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  sku {
    name     = var.waf ? "WAF_v2" : "Standard_v2"
    tier     = var.waf ? "WAF_v2" : "Standard_v2"
    capacity = 2
  }

  waf_configuration {
    enabled            = var.waf
    firewall_mode      = "Prevention"
    rule_set_type      = "OWASP"
    rule_set_version   = "3.2"
    request_body_check = false
  }

  gateway_ip_configuration {
    name      = format("%s-rbc-app-gw-ip", var.partner)
    subnet_id = azurerm_subnet.gateway.id
  }

  frontend_port {
    name = local.frontend_https_port_name
    port = 443
  }

  frontend_ip_configuration {
    name                            = local.frontend_ip_config_name
    public_ip_address_id            = azurerm_public_ip.gateway[0].id
    private_link_configuration_name = local.private_link_configuration_name
  }

  frontend_ip_configuration {
    name                          = format("%s-rbc-app-gw-feip-priv", var.partner)
    private_ip_address_allocation = "Static"
    private_ip_address            = "10.0.6.66"
    subnet_id                     = azurerm_subnet.gateway.id
  }

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

  ssl_certificate {
    name     = local.ssl_cert_name
    data     = pkcs12_from_pem.app_gateway[0].result
    password = var.ssl_cert_password
  }

  http_listener {
    name                           = local.https_listener_name
    frontend_ip_configuration_name = local.frontend_ip_config_name
    frontend_port_name             = local.frontend_https_port_name
    protocol                       = "Https"
    ssl_certificate_name           = local.ssl_cert_name
  }

  request_routing_rule {
    priority                   = 1
    name                       = format("%s-rbc-app-gw-rr", var.partner)
    rule_type                  = "Basic"
    http_listener_name         = local.https_listener_name
    backend_address_pool_name  = local.backend_address_pool_name
    backend_http_settings_name = local.backend_http_settings_name

  }

  probe {
    name                                      = local.probe_name
    protocol                                  = "Https"
    pick_host_name_from_backend_http_settings = true
    path                                      = "/api/v1/health"
    interval                                  = 30
    timeout                                   = 30
    unhealthy_threshold                       = 3
  }

  private_link_configuration {
    name = local.private_link_configuration_name
    ip_configuration {
      name                          = format("%s-rbc-app-gw-plcipcfg", var.partner)
      subnet_id                     = azurerm_subnet.gateway-pl.id
      private_ip_address_allocation = "Dynamic"
      primary                       = true
    }
  }
}
