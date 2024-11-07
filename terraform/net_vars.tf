# The variables in this file are used to configure networking.
# The default networking is usually fine. But in cases where
# networks need to be peered, it may be necessary to adjust these.

variable "virtual_network_address_space" {
  type        = list(string)
  default     = ["10.0.0.0/16"]
  description = "Virtual network address space."
}

variable "default_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.0.0/24"]
  description = "Default subnet address space."
}

variable "app_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.1.0/24"]
  description = "App subnet address space."
}

variable "redis_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.2.0/24"]
  description = "Redis subnet address space."
}

variable "form_recognizer_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.3.0/24"]
  description = "Form Recognizer subnet address space."
}

variable "database_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.4.0/24"]
  description = "Database subnet address space."
}

variable "openai_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.5.0/24"]
  description = "OpenAI subnet address space."
}

variable "gateway_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.6.0/24"]
  description = "Gateway subnet address space."
}

variable "gateway_private_ip_address" {
  type        = string
  default     = "10.0.6.66"
  description = "Gateway private IP address. This must be in the gateway subnet address space."
}

variable "gateway_private_link_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.7.0/24"]
  description = "Gateway private link subnet address space."
}

variable "file_storage_subnet_address_space" {
  type        = list(string)
  default     = ["10.0.8.0/24"]
  description = "File storage subnet address space."
}
