# AWS Secrets Manager secret CONTAINERS only — deliberately no values.
#
# This module declares the secrets (ANTHROPIC_API_KEY, VOYAGE_API_KEY) that the
# EKS workload will read at runtime, but it NEVER writes their values. There is
# intentionally no `aws_secretsmanager_secret_version` here: putting a value in
# Terraform would commit a secret to source and store it in plaintext in state,
# exactly what this project forbids everywhere else. The values are populated
# out-of-band (console, CLI, or a CI job with scoped credentials) after apply.
# The chart's Secret is fed from these at deploy time, not from a values file.

resource "aws_secretsmanager_secret" "app" {
  for_each = toset(var.secret_names)

  name        = "${var.name}/${each.value}"
  description = "Application secret for production-llm-platform (value set out-of-band, never by Terraform)."
}
