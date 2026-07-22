# ---------------------------------------------------------------------------
# Root module — wires the six infrastructure modules together.
#
# VALIDATED, NEVER APPLIED. This project has no AWS account. `terraform init`
# (local backend) + `terraform validate` + `terraform fmt -check` are the CI-safe
# gate. `terraform plan`/`apply` need real credentials this project does not have
# and are deliberately out of scope (ADR 0018), the same treatment this repo
# already gives paid LLM APIs.
# ---------------------------------------------------------------------------

locals {
  name = var.name_prefix
}

module "networking" {
  source = "./modules/networking"

  name                 = local.name
  vpc_cidr             = var.vpc_cidr
  availability_zones   = var.availability_zones
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
}

module "cluster" {
  source = "./modules/cluster"

  name                = local.name
  kubernetes_version  = var.kubernetes_version
  subnet_ids          = module.networking.private_subnet_ids
  node_instance_types = var.eks_node_instance_types
  node_desired_size   = var.eks_node_desired_size
  node_min_size       = var.eks_node_min_size
  node_max_size       = var.eks_node_max_size
}

module "postgres" {
  source = "./modules/postgres"

  name               = local.name
  vpc_id             = module.networking.vpc_id
  subnet_ids         = module.networking.private_subnet_ids
  instance_class     = var.postgres_instance_class
  engine_version     = var.postgres_engine_version
  allocated_storage  = var.postgres_allocated_storage
  db_name            = var.postgres_db_name
  master_username    = var.postgres_username
  allowed_cidr_block = var.vpc_cidr
}

module "redis" {
  source = "./modules/redis"

  name               = local.name
  vpc_id             = module.networking.vpc_id
  subnet_ids         = module.networking.private_subnet_ids
  node_type          = var.redis_node_type
  engine_version     = var.redis_engine_version
  allowed_cidr_block = var.vpc_cidr
}

module "storage" {
  source = "./modules/storage"

  name = local.name
}

module "secrets" {
  source = "./modules/secrets"

  name = local.name
}
