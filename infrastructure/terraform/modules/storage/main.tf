# Object storage (S3). Illustrative general-purpose bucket for artifacts/backups.
# Account id is appended for global uniqueness. Private, versioned, encrypted.

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "this" {
  bucket = "${var.name}-artifacts-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.name}-artifacts"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
