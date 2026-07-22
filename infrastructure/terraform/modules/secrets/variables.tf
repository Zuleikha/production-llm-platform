variable "name" {
  description = "Name prefix for the secret containers."
  type        = string
}

variable "secret_names" {
  description = "Logical names of the application secrets to declare containers for."
  type        = list(string)
  default     = ["anthropic-api-key", "voyage-api-key"]
}
