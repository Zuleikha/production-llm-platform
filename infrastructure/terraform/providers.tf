provider "aws" {
  region = var.aws_region

  # Illustrative default tags on every taggable resource. `terraform validate`
  # and `fmt -check` never contact AWS, so no credentials are read here.
  default_tags {
    tags = {
      Project     = "production-llm-platform"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
