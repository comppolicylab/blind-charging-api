# This file is used to configure names used in the application.
#
# By default, we use names derived from the `var.partner` variable.
# Optionally, you can override these names according to your needs.
#
# Note that Terraform creates many objects in a deployment, and some
# of these still use hard-coded names so as not to get overwhelming.
#
# If more names need to be configurable, please add them here.

variable "resource_group_name" {
  type        = string
  description = "Name of the resource group to deploy to."
  default     = "RaceBlindCharging"
}

variable "app_infra_resource_group_name" {
  type        = string
  description = "Name of the resource group to deploy the app infrastructure to."
  default     = "rbc-app-env-rg"
}

variable "container_app_env_name" {
  type        = string
  default     = ""
  description = "Name of the Container App Environment resource."
}

variable "container_app_name" {
  type        = string
  default     = ""
  description = "Name of the Container App resource."
}

variable "mssql_server_name" {
  type        = string
  default     = ""
  description = "Name of the MSSQL Server resource."
}

variable "mssql_database_name" {
  type        = string
  default     = ""
  description = "Name of the MSSQL Database resource."
}

variable "mssql_private_endpoint_name" {
  type        = string
  default     = ""
  description = "Name of the MSSQL Private Endpoint resource."
}

variable "form_recognizer_name" {
  type        = string
  default     = ""
  description = "Name of the Form Recognizer resource."
}

variable "form_recognizer_private_endpoint_name" {
  type        = string
  default     = ""
  description = "Name of the Form Recognizer Private Endpoint resource."
}

variable "app_gateway_name" {
  type        = string
  default     = ""
  description = "Name of the Application Gateway resource."
}

variable "app_gateway_private_link_configuration_name" {
  type        = string
  default     = ""
  description = "Name of the Application Gateway Private Link Configuration resource."
}

variable "log_analytics_workspace_name" {
  type        = string
  default     = ""
  description = "Name of the Log Analytics Workspace resource."
}

variable "application_insights_name" {
  type        = string
  default     = ""
  description = "Name of the Application Insights resource."
}

variable "monitor_private_endpoint_name" {
  type        = string
  default     = ""
  description = "Name of the Monitor Private Endpoint resource."
}

variable "virtual_network_name" {
  type        = string
  default     = ""
  description = "Name of the Virtual Network resource."
}

variable "default_subnet_name" {
  type        = string
  default     = "default"
  description = "Name of the default subnet."
}

variable "app_subnet_name" {
  type        = string
  default     = "app"
  description = "Name of the app subnet."
}

variable "redis_subnet_name" {
  type        = string
  default     = "redis"
  description = "Name of the Redis subnet."
}

variable "form_recognizer_subnet_name" {
  type        = string
  default     = "fr"
  description = "Name of the Form Recognizer subnet."
}

variable "database_subnet_name" {
  type        = string
  default     = "db"
  description = "Name of the database subnet."
}

variable "openai_subnet_name" {
  type        = string
  default     = "openai"
  description = "Name of the OpenAI subnet."
}

variable "gateway_subnet_name" {
  type        = string
  default     = "gateway"
  description = "Name of the gateway subnet."
}

variable "monitor_subnet_name" {
  type        = string
  default     = "monitor"
  description = "Name of the monitor subnet."
}

variable "gateway_private_link_subnet_name" {
  type        = string
  default     = "gateway-pl"
  description = "Name of the gateway private link subnet."
}

variable "file_storage_subnet_name" {
  type        = string
  default     = "fs"
  description = "Name of the file storage subnet."
}

variable "key_vault_subnet_name" {
  type        = string
  default     = "kv"
  description = "Name of the key vault subnet."
}

variable "openai_account_name" {
  type        = string
  default     = ""
  description = "Name of the OpenAI account."
}

variable "openai_llm_deployment_name" {
  type        = string
  default     = ""
  description = "Name of the OpenAI LLM deployment."
}

variable "openai_private_endpoint_name" {
  type        = string
  default     = ""
  description = "Name of the OpenAI private endpoint."
}

variable "key_vault_private_endpoint_name" {
  type        = string
  default     = ""
  description = "Name of the Key Vault private endpoint."
}

variable "redis_cache_name" {
  type        = string
  default     = ""
  description = "Name of the Redis cache."
}

variable "redis_private_endpoint_name" {
  type        = string
  default     = ""
  description = "Name of the Redis private endpoint."
}

variable "research_app_name" {
  type        = string
  default     = ""
  description = "Name of the research environment Azure Container App."
}

variable "research_storage_account_name" {
  type        = string
  default     = ""
  description = "Name of the research environment storage account. This must be globally unique."
}

variable "research_storage_share_name" {
  type        = string
  default     = "rbcdatafs"
  description = "Name of the research environment storage share."
}

variable "analytics_storage_account_name" {
  type        = string
  default     = ""
  description = "Name of the analytics storage account."
}

variable "analytics_store_private_endpoint_name" {
  type        = string
  default     = ""
  description = "Name of the analytics store private endpoint."
}

variable "firewall_name" {
  type        = string
  default     = ""
  description = "Name of the firewall resource."
}

variable "key_vault_name" {
  type        = string
  default     = ""
  description = "Name of the key vault resource."
}

variable "user_assigned_admin_identity_name" {
  type        = string
  default     = ""
  description = "Name of the user-assigned identity resource for the admin user."
}

############################################
# Derived names
#
# Most of our "default" names are dependent on the `partner` variable,
# so derive them all here.

locals {
  # If the workspace is "default" don't use it as part of the name prefix.
  # If it's not "default", use the format `{partner}-rbc-{workspace}`.
  name_prefix                           = terraform.workspace == "default" ? format("%s-rbc", var.partner) : format("%s-rbc-%s", var.partner, terraform.workspace)
  container_app_env_name                = coalesce(var.container_app_env_name, lower(format("%s-env", local.name_prefix)))
  container_app_name                    = coalesce(var.container_app_name, lower(format("%s-app", local.name_prefix)))
  mssql_server_name                     = coalesce(var.mssql_server_name, lower(format("%s-sql", local.name_prefix)))
  mssql_database_name                   = coalesce(var.mssql_database_name, lower(format("%s-db", local.name_prefix)))
  mssql_private_endpoint_name           = coalesce(var.mssql_private_endpoint_name, format("%s-mssql-pe", local.name_prefix))
  form_recognizer_name                  = coalesce(var.form_recognizer_name, lower(format("%s-cs-fr", local.name_prefix)))
  form_recognizer_private_endpoint_name = coalesce(var.form_recognizer_private_endpoint_name, lower(format("%s-cs-fr-pe", local.name_prefix)))
  app_gateway_name                      = coalesce(var.app_gateway_name, lower(format("%s-app-gw", local.name_prefix)))
  private_link_configuration_name       = coalesce(var.app_gateway_private_link_configuration_name, lower(format("%s-app-gw-plc", local.name_prefix)))
  log_analytics_workspace_name          = coalesce(var.log_analytics_workspace_name, lower(format("%s-law", local.name_prefix)))
  application_insights_name             = coalesce(var.application_insights_name, lower(format("%s-mon-insights", local.name_prefix)))
  monitor_private_endpoint_name         = coalesce(var.monitor_private_endpoint_name, lower(format("%s-mon-pe", local.name_prefix)))
  virtual_network_name                  = coalesce(var.virtual_network_name, lower(format("%s-vnet", local.name_prefix)))
  openai_account_name                   = coalesce(var.openai_account_name, lower(format("%s-cs-oai", local.name_prefix)))
  openai_llm_deployment_name            = coalesce(var.openai_llm_deployment_name, lower(format("%s-oai-llm", local.name_prefix)))
  openai_private_endpoint_name          = coalesce(var.openai_private_endpoint_name, lower(format("%s-cs-oai-pe", local.name_prefix)))
  key_vault_private_endpoint_name       = coalesce(var.key_vault_private_endpoint_name, lower(format("%s-kv-pe", local.name_prefix)))
  redis_cache_name                      = coalesce(var.redis_cache_name, lower(format("%s-redis", local.name_prefix)))
  redis_private_endpoint_name           = coalesce(var.redis_private_endpoint_name, lower(format("%s-redis-pe", local.name_prefix)))
  research_app_name                     = coalesce(var.research_app_name, lower(format("%s-research", local.name_prefix)))
  research_storage_account_name         = coalesce(var.research_storage_account_name, lower(replace(format("%sdata", local.name_prefix), "-", "")))
  analytics_storage_account_name        = coalesce(var.analytics_storage_account_name, lower(replace(format("%sadata", local.name_prefix), "-", "")))
  analytics_store_private_endpoint_name = coalesce(var.analytics_store_private_endpoint_name, lower(format("%s-asa-pe", local.name_prefix)))
  firewall_name                         = coalesce(var.firewall_name, lower(format("%s-fw", local.name_prefix)))
  key_vault_name                        = coalesce(var.key_vault_name, lower(format("%s-kv", local.name_prefix)))
  user_assigned_admin_identity_name     = lower(format("%s-ua-admin", local.name_prefix))
  user_assigned_gateway_identity_name   = lower(format("%s-ua-gateway", local.name_prefix))
}
