# Rola maszyn: SSM (zdalne komendy i sesje bez SSH), pull z ECR, odczyt paczki z S3.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.project}-node"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

data "aws_iam_policy_document" "bundle_read" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.bundle.arn]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.bundle.arn}/*"]
  }
}

resource "aws_iam_role_policy" "bundle_read" {
  name   = "bundle-read"
  role   = aws_iam_role.node.id
  policy = data.aws_iam_policy_document.bundle_read.json
}

resource "aws_iam_instance_profile" "node" {
  name = "${var.project}-node"
  role = aws_iam_role.node.name
}
