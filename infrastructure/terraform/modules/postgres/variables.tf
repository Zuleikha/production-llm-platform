variable "name" {
  description = "Name prefix for RDS resources."
  type        = string
}

variable "vpc_id" {
  description = "VPC id the security group lives in."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet ids for the DB subnet group."
  type        = list(string)
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
}

variable "engine_version" {
  description = "PostgreSQL engine version."
  type        = string
}

variable "allocated_storage" {
  description = "Allocated storage in GiB."
  type        = number
}

variable "db_name" {
  description = "Initial database name."
  type        = string
}

variable "master_username" {
  description = "Master username. Password is RDS-managed in Secrets Manager, never set in Terraform."
  type        = string
}

variable "allowed_cidr_block" {
  description = "CIDR allowed to reach Postgres (the VPC CIDR)."
  type        = string
}
