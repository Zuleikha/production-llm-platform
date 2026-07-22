# Managed Redis (ElastiCache). Used purely as the conversation cache — Postgres
# stays the source of truth (ADR 0008) — so this is a single-node illustrative
# setup with no persistence. Stage 9 owns replication/failover sizing.
#
# No auth token is set here on purpose: a token would be a secret, and this
# project keeps secret VALUES out of Terraform. Access is restricted at the
# network layer (VPC-internal security group) instead. A hardened build would
# enable transit encryption + an auth token sourced from Secrets Manager.

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name}-redis"
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "this" {
  name        = "${var.name}-redis-sg"
  description = "Redis access from within the VPC"
  vpc_id      = var.vpc_id

  ingress {
    description = "Redis from within the VPC"
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.name}-redis-sg"
  }
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = "${var.name}-redis"
  description          = "Conversation cache for the production-llm-platform API"

  engine         = "redis"
  engine_version = var.engine_version
  node_type      = var.node_type
  port           = 6379

  num_cache_clusters = 1

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = [aws_security_group.this.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = false

  tags = {
    Name = "${var.name}-redis"
  }
}
