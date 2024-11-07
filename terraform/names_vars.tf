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


############################################
# Derived names
#
# Most of our "default" names are dependent on the `partner` variable,
# so derive them all here.

locals {
  container_app_env_name                = coalesce(var.container_app_env_name, format("%s-rbc-env", var.partner))
  container_app_name                    = coalesce(var.container_app_name, format("%s-rbc-app", var.partner))
  mssql_server_name                     = coalesce(var.mssql_server_name, format("%s-rbc-sql", var.partner))
  mssql_database_name                   = coalesce(var.mssql_database_name, format("%s-rbc-db", var.partner))
  mssql_private_endpoint_name           = coalesce(var.mssql_private_endpoint_name, format("%s-rbc-mssql-pe", var.partner))
  form_recognizer_name                  = coalesce(var.form_recognizer_name, format("%s-rbc-cs-fr", var.partner))
  form_recognizer_private_endpoint_name = coalesce(var.form_recognizer_private_endpoint_name, format("%s-rbc-cs-fr-pe", var.partner))
  app_gateway_name                      = coalesce(var.app_gateway_name, format("%s-rbc-app-gw", var.partner))
  private_link_configuration_name       = coalesce(var.app_gateway_private_link_configuration_name, format("%s-rbc-app-gw-plc", var.partner))
  log_analytics_workspace_name          = coalesce(var.log_analytics_workspace_name, format("%s-rbc-law", var.partner))
  application_insights_name             = coalesce(var.application_insights_name, format("%s-rbc-mon-insights", var.partner))
  virtual_network_name                  = coalesce(var.virtual_network_name, format("%s-rbc-vnet", var.partner))
  openai_account_name                   = coalesce(var.openai_account_name, format("%s-rbc-cs-oai", var.partner))
  openai_llm_deployment_name            = coalesce(var.openai_llm_deployment_name, format("%s-rbc-oai-llm", var.partner))
  openai_private_endpoint_name          = coalesce(var.openai_private_endpoint_name, format("%s-rbc-cs-oai-pe", var.partner))
  redis_cache_name                      = coalesce(var.redis_cache_name, format("%s-rbc-redis", var.partner))
  redis_private_endpoint_name           = coalesce(var.redis_private_endpoint_name, format("%s-rbc-redis-pe", var.partner))
  research_app_name                     = coalesce(var.research_app_name, format("%s-rbc-research", var.partner))
  research_storage_account_name         = coalesce(var.research_storage_account_name, replace(format("%srbcdata", var.partner), "-", ""))
}
