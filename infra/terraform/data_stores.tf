resource "aws_ecr_repository" "app" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_db_subnet_group" "main" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}

# pgvector ships with RDS for PostgreSQL 16. CREATE EXTENSION runs as the master user in the one-off migrate task.
resource "aws_db_instance" "main" {
  identifier                  = var.name
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.db_instance_class
  allocated_storage           = 20
  storage_encrypted           = true
  db_name                     = "gatekeeper"
  username                    = "gatekeeper_owner"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  backup_retention_period     = 7
  deletion_protection         = true
  skip_final_snapshot         = false
  final_snapshot_identifier   = "${var.name}-final"
  publicly_accessible         = false
}

# Values are set out of band with `aws secretsmanager put-secret-value`, so they never enter Terraform state.
locals {
  secret_names = [
    "database-url-app",
    "database-url-migrate",
    "app-db-password",
    "mcp-db-password",
    "shopify-client-secret",
    "shopify-webhook-secret",
    "anthropic-api-key",
    "reviewer-tokens",
  ]
}

resource "aws_secretsmanager_secret" "app" {
  for_each = toset(local.secret_names)
  name     = "${var.name}/${each.key}"
}
