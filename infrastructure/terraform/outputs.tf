output "vpc_id" {
  description = "VPC id."
  value       = module.networking.vpc_id
}

output "eks_cluster_name" {
  description = "EKS cluster name."
  value       = module.cluster.cluster_name
}

output "eks_cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.cluster.cluster_endpoint
}

output "postgres_endpoint" {
  description = "RDS PostgreSQL endpoint — becomes the API's DATABASE_URL host."
  value       = module.postgres.endpoint
}

output "postgres_master_user_secret_arn" {
  description = "ARN of the RDS-managed master-user secret in Secrets Manager."
  value       = module.postgres.master_user_secret_arn
}

output "redis_primary_endpoint" {
  description = "ElastiCache primary endpoint — becomes the API's REDIS_URL host."
  value       = module.redis.primary_endpoint
}

output "s3_bucket" {
  description = "Object storage bucket name."
  value       = module.storage.bucket_id
}

output "app_secret_arns" {
  description = "ARNs of the app secret containers (ANTHROPIC/VOYAGE) — values populated out-of-band, never by Terraform."
  value       = module.secrets.secret_arns
}
