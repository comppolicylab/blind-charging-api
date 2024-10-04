terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.2.0"
    }
    azapi = {
      source  = "azure/azapi"
      version = "~> 1.15.0"
    }
    pkcs12 = {
      source  = "chilicat/pkcs12"
      version = "~> 0.2.5"
    }
    acme = {
      source  = "vancluever/acme"
      version = "~> 2.0"
    }
  }

  backend "azurerm" {}

  required_version = ">= 1.5.7"
}

provider "azurerm" {
  subscription_id = var.subscription_id
  environment     = var.azure_env

  features {
  }
}

provider "azapi" {
  tenant_id       = data.azurerm_client_config.current.tenant_id
  subscription_id = var.subscription_id
  environment     = var.azure_env
}

provider "pkcs12" {}

provider "acme" {
  server_url = "https://acme-v02.api.letsencrypt.org/directory"
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}
