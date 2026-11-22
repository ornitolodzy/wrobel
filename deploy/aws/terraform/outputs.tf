output "public_url" {
  description = "Web UI i API (/api/v1/...)."
  value       = "http://${aws_eip.edge.public_ip}"
}

output "app_egress_ip" {
  description = "Stały adres wychodzący APP: dopisz do whitelisty SSH na serwerze GPU, jeśli ją masz."
  value       = aws_eip.app.public_ip
}

output "region" {
  value = var.region
}

output "registry" {
  value = local.registry
}

output "bucket" {
  value = aws_s3_bucket.bundle.id
}

output "edge_instance_id" {
  value = aws_instance.node["edge"].id
}

output "app_instance_id" {
  value = aws_instance.node["app"].id
}

output "data_instance_id" {
  value = aws_instance.node["data"].id
}

output "admin_api_key" {
  description = "terraform -chdir=deploy/aws/terraform output -raw admin_api_key"
  value       = random_password.admin_api_key.result
  sensitive   = true
}
