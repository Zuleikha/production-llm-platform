terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  # Local backend only. This project has no AWS account and no remote state
  # storage — the config is written to be `init`/`validate`/`fmt`-checked, never
  # applied (see the module README and ADR 0018). Do not add a real backend or
  # credentials here.
  backend "local" {}
}
