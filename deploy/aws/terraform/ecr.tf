locals {
  images = toset([
    "api-gateway",
    "frontend",
    "discord-bot",
    "verification-pipeline",
    "page-scrapper",
    "ocr",
    "context-graph",
  ])
}

resource "aws_ecr_repository" "svc" {
  for_each     = local.images
  name         = "${var.project}/${each.key}"
  force_delete = true # terraform destroy usuwa też obrazy
}

resource "aws_ecr_lifecycle_policy" "svc" {
  for_each   = aws_ecr_repository.svc
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Zostaw 5 ostatnich obrazów"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}
