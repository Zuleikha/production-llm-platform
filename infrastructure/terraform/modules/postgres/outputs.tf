output "endpoint" {
  description = "RDS endpoint (host:port)."
  value       = aws_db_instance.this.endpoint
}

output "port" {
  description = "RDS port."
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Initial database name."
  value       = aws_db_instance.this.db_name
}

output "security_group_id" {
  description = "Postgres security group id."
  value       = aws_security_group.this.id
}

output "master_user_secret_arn" {
  description = "ARN of the RDS-managed master-user secret in Secrets Manager."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
