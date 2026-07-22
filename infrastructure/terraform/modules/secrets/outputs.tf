output "secret_arns" {
  description = "Map of logical secret name -> Secrets Manager ARN (containers only, no values)."
  value       = { for k, s in aws_secretsmanager_secret.app : k => s.arn }
}
