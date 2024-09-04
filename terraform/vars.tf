variable "subscription_id" {
  type        = string
  description = "Azure subscription ID to deploy to. The subscription must be set up before running Terraform."
}

variable "debug" {
  type        = bool
  default     = false
  description = "Enable debug mode for the application."
}

variable "partner" {
  type        = string
  description = "Name of the group deploying this infrastructure. This is used for naming resources."
}

variable "db_user" {
  type        = string
  default     = "bcadmin"
  description = "Admin username for the MSSQL database. See Azure documentation for disallowed values."
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Admin password for the MSSQL database. This can be any strong string and should be rotated periodically."
}

variable "location" {
  type        = string
  default     = "usgovvirginia"
  description = "Azure region to deploy to. Available options depend on the Azure environment."
}

variable "azure_env" {
  type        = string
  default     = "usgovernment"
  description = "Azure environment to deploy to. Normally this is GovCloud, but does not have to be."
}

variable "expose_app" {
  type        = bool
  default     = true
  description = "Expose the app outside of the app environment."
}

variable "app_auth" {
  type    = string
  default = "none"
  validation {
    condition     = can(regex("^(none|preshared|client_credentials)$", var.app_auth))
    error_message = "app_auth must be one of 'none', 'preshared', or 'client_credentials'."
  }
  description = <<EOF
Authentication method for the application.

By default, no authentication is required.

`preshared` requires a shared secret to be set in `app_auth_secret`. It should be rotated periodically.

`client_credentials` requires a secret to be set in `app_auth_secret`. This is used for signing the token.
EOF
}

variable "app_auth_secret" {
  type        = string
  sensitive   = true
  nullable    = true
  default     = null
  description = "Secret used with client_credentials flow. (Required if app_auth is 'client_credentials'.)"
}

variable "registry_password" {
  type        = string
  sensitive   = true
  description = <<EOF
Password for authenticating with the container registry.

This value is issued from the Computational Policy Lab's Azure Container Registry;
the `blind-charging-api` image is only deployed there, and not in this Terraform configuration.
EOF
}

variable "tags" {
  type = map(string)
  default = {
    "environment" = "production"
    "app"         = "raceblind"
  }
  description = "Tags to apply to all resources (for bookkeeping purposes)."
}

variable "disable_content_filter" {
  type        = bool
  default     = true
  description = <<EOF
Disable the content filter for the OpenAI cognitive service.

This can only be set to false if the Azure subscription has been approved to disable filters.

This should *always* be true in production. Only set to false if you want to deploy while waiting \
for approval to disable filters. Once approved, re-deploy with this set to true.
EOF
}
