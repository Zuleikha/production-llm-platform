# ---------------------------------------------------------------------------
# Root input variables.
#
# Region, sizing and CIDR defaults below are ILLUSTRATIVE — enough to be valid,
# NOT sized for real load. Right-sizing (instance classes, node counts, storage,
# multi-AZ, autoscaling) is Stage 9's job. See ADR 0018.
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = "AWS region to deploy into (illustrative default)."
  type        = string
  default     = "eu-west-2"
}

variable "environment" {
  description = "Deployment environment tag (e.g. prod, staging)."
  type        = string
  default     = "prod"
}

variable "name_prefix" {
  description = "Prefix applied to resource names for identification."
  type        = string
  default     = "pllm"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "AZs to spread subnets across."
  type        = list(string)
  default     = ["eu-west-2a", "eu-west-2b"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (one per AZ)."
  type        = list(string)
  default     = ["10.0.0.0/20", "10.0.16.0/20"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (one per AZ)."
  type        = list(string)
  default     = ["10.0.128.0/20", "10.0.144.0/20"]
}

variable "kubernetes_version" {
  description = "EKS control-plane Kubernetes version."
  type        = string
  default     = "1.31"
}

variable "eks_node_instance_types" {
  description = "Instance types for the EKS managed node group (illustrative)."
  type        = list(string)
  default     = ["t3.medium"]
}

variable "eks_node_desired_size" {
  description = "Desired EKS node count (illustrative, not load-sized)."
  type        = number
  default     = 2
}

variable "eks_node_min_size" {
  description = "Minimum EKS node count."
  type        = number
  default     = 2
}

variable "eks_node_max_size" {
  description = "Maximum EKS node count."
  type        = number
  default     = 4
}

variable "postgres_instance_class" {
  description = "RDS instance class (illustrative)."
  type        = string
  default     = "db.t3.micro"
}

variable "postgres_engine_version" {
  description = "RDS PostgreSQL engine version."
  type        = string
  default     = "16.4"
}

variable "postgres_allocated_storage" {
  description = "RDS allocated storage in GiB (illustrative)."
  type        = number
  default     = 20
}

variable "postgres_db_name" {
  description = "Initial database name."
  type        = string
  default     = "platform"
}

variable "postgres_username" {
  description = "RDS master username. The password is RDS-managed in Secrets Manager (never set here)."
  type        = string
  default     = "platform"
}

variable "redis_node_type" {
  description = "ElastiCache node type (illustrative)."
  type        = string
  default     = "cache.t3.micro"
}

variable "redis_engine_version" {
  description = "ElastiCache Redis engine version."
  type        = string
  default     = "7.1"
}
