data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  nodes = {
    edge = {
      type    = var.edge_instance_type
      disk_gb = var.edge_disk_gb
      ip      = local.edge_ip
      sg      = aws_security_group.edge.id
      swap_gb = 2
    }
    app = {
      type    = var.app_instance_type
      disk_gb = var.app_disk_gb
      ip      = local.app_ip
      sg      = aws_security_group.app.id
      swap_gb = 4
    }
    data = {
      type    = var.data_instance_type
      disk_gb = var.data_disk_gb
      ip      = local.data_ip
      sg      = aws_security_group.data.id
      swap_gb = 2
    }
  }

  gpu_tunnel = var.gpu_ssh_target != ""
}

resource "aws_instance" "node" {
  for_each = local.nodes

  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = each.value.type
  subnet_id              = aws_subnet.public.id
  private_ip             = each.value.ip
  vpc_security_group_ids = [each.value.sg]
  iam_instance_profile   = aws_iam_instance_profile.node.name

  root_block_device {
    volume_type = "gp3"
    volume_size = each.value.disk_gb
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required" # IMDSv2
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    role           = each.key
    bucket         = aws_s3_bucket.bundle.id
    region         = var.region
    swap_gb        = each.value.swap_gb
    gpu_tunnel     = each.key == "app" && local.gpu_tunnel
    tailscale      = each.key == "app" && nonsensitive(var.tailscale_auth_key != "")
    gpu_ssh_target = var.gpu_ssh_target
    gpu_ssh_port   = var.gpu_ssh_port
    gpu_forwards   = join(" ", [for f in var.gpu_tunnel_forwards : "-L ${f}"])
  })

  # Nowy AMI albo zmiana skryptu startowego nie może po cichu skasować maszyny z bazą.
  # Wymiana na siłę: terraform apply -replace='aws_instance.node["app"]'
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  depends_on = [aws_s3_object.infra_env, aws_s3_object.gpu_key, aws_s3_object.tailscale_key]

  tags = { Name = "${var.project}-${each.key}" }
}

# Stały publiczny adres API (EDGE) i stały adres wychodzący APP (do whitelisty na serwerze GPU).
resource "aws_eip" "edge" {
  domain = "vpc"
  tags   = { Name = "${var.project}-edge" }
}

resource "aws_eip_association" "edge" {
  allocation_id = aws_eip.edge.id
  instance_id   = aws_instance.node["edge"].id
}

resource "aws_eip" "app" {
  domain = "vpc"
  tags   = { Name = "${var.project}-app" }
}

resource "aws_eip_association" "app" {
  allocation_id = aws_eip.app.id
  instance_id   = aws_instance.node["app"].id
}
