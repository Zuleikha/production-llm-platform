# Managed PostgreSQL (RDS). The master password is NEVER set in Terraform:
# manage_master_user_password = true has RDS generate it and store it in AWS
# Secrets Manager, so no secret value ever lives in this config or state as
# plaintext the way an inline `password =` would. The app reads the connection
# host from the `endpoint` output and the password from the managed secret.

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-pg"
  subnet_ids = var.subnet_ids

  tags = {
    Name = "${var.name}-pg-subnets"
  }
}

resource "aws_security_group" "this" {
  name        = "${var.name}-pg-sg"
  description = "Postgres access from within the VPC"
  vpc_id      = var.vpc_id

  ingress {
    description = "PostgreSQL from within the VPC"
    from_port   = 5432
    to_port     = 5432
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
    Name = "${var.name}-pg-sg"
  }
}

resource "aws_db_instance" "this" {
  identifier     = "${var.name}-pg"
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.master_username
  # RDS generates and rotates the master password into Secrets Manager.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]

  multi_az            = false # illustrative; Stage 9 sizes HA
  publicly_accessible = false
  skip_final_snapshot = true
  deletion_protection = false

  tags = {
    Name = "${var.name}-pg"
  }
}
