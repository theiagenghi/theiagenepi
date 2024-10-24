variable "aws_account_id" {
  type        = string
  description = "AWS account ID to apply changes to"
  default     = "654654542669"
}

variable "aws_role" {
  type        = string
  description = "Name of the AWS role to assume to apply changes"
  default     = ""
}

variable "priority" {
  type        = number
  description = "Listener rule priority number within the given listener"
  default     = 1234
}

variable "happymeta_" {
  type        = string
  description = "Happy Path metadata. Ignored by actual terraform."
  default     = "{}"
}

variable "stack_name" {
  type        = string
  description = "Happy Path stack name"
  default     = "gedevstack-jheath"
}

variable "happy_config_secret" {
  type        = string
  description = "Happy Path configuration secret name"
  default     = "happy/env-dev-config"
}

variable "wait_for_steady_state" {
  type        = bool
  description = "Should terraform block until ECS reaches a steady state?"
  default     = true
}
