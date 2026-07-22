variable "name" {
  description = "Name prefix for ElastiCache resources."
  type        = string
}

variable "vpc_id" {
  description = "VPC id the security group lives in."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet ids for the cache subnet group."
  type        = list(string)
}

variable "node_type" {
  description = "ElastiCache node type."
  type        = string
}

variable "engine_version" {
  description = "Redis engine version."
  type        = string
}

variable "allowed_cidr_block" {
  description = "CIDR allowed to reach Redis (the VPC CIDR)."
  type        = string
}
