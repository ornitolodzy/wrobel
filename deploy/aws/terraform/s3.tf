# Prywatny bucket z paczką wdrożeniową:
#   infra/   - zarządza Terraform (adresy, wygenerowane hasła, klucz do serwera GPU)
#   bundle/  - wrzuca deploy.sh (pliki compose, nginx.conf, seedy, .env z kluczami API, tag obrazów)

resource "random_id" "bucket" {
  byte_length = 4
}

resource "aws_s3_bucket" "bundle" {
  bucket        = "${var.project}-bundle-${random_id.bucket.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "bundle" {
  bucket                  = aws_s3_bucket.bundle.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bundle" {
  bucket = aws_s3_bucket.bundle.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "random_password" "neo4j" {
  length  = 32
  special = false
}

resource "random_password" "admin_api_key" {
  length  = 40
  special = false
}

locals {
  registry = split("/", aws_ecr_repository.svc["api-gateway"].repository_url)[0]

  edge_ip = "10.0.1.10"
  app_ip  = "10.0.1.20"
  data_ip = "10.0.1.30"
}

# Zmienne wspólne dla compose na wszystkich hostach. Nadpisują te same klucze z .env.
resource "aws_s3_object" "infra_env" {
  bucket = aws_s3_bucket.bundle.id
  key    = "infra/infra.env"
  content = join("\n", [
    "REGISTRY=${local.registry}",
    "AWS_REGION=${var.region}",
    "PUBLIC_URL=http://${aws_eip.edge.public_ip}",
    "APP_HOST=${local.app_ip}",
    "DATA_HOST=${local.data_ip}",
    "PG_PASSWORD=${random_password.postgres.result}",
    "NEO4J_PASSWORD=${random_password.neo4j.result}",
    "ADMIN_API_KEY=${random_password.admin_api_key.result}",
    "",
  ])
}

resource "aws_s3_object" "tailscale_key" {
  count   = nonsensitive(var.tailscale_auth_key == "") ? 0 : 1
  bucket  = aws_s3_bucket.bundle.id
  key     = "infra/tailscale_auth_key"
  content = var.tailscale_auth_key
}

resource "aws_s3_object" "gpu_key" {
  count  = var.gpu_ssh_private_key_path == "" ? 0 : 1
  bucket = aws_s3_bucket.bundle.id
  key    = "infra/gpu_ssh_key"
  source = pathexpand(var.gpu_ssh_private_key_path)
  etag   = filemd5(pathexpand(var.gpu_ssh_private_key_path))
}
