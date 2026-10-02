# Private Docker registries for our images. The API image also runs the worker.
resource "aws_ecr_repository" "repo" {
  for_each             = toset(["api", "dashboard"])
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # `terraform destroy` removes them even with images inside
}

# Keep only the last 5 images, so old ones do not pile up storage cost.
resource "aws_ecr_lifecycle_policy" "repo" {
  for_each   = aws_ecr_repository.repo
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 5 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
}
