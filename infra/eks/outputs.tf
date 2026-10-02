output "cluster_name" {
  value = aws_eks_cluster.main.name
}

output "region" {
  value = var.region
}

output "kubeconfig_command" {
  description = "Run this to point kubectl at the cluster."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.main.name}"
}

output "ecr_repositories" {
  value = { for k, r in aws_ecr_repository.repo : k => r.repository_url }
}

output "vpc_id" {
  description = "For the load balancer controller's Helm install."
  value       = aws_vpc.main.id
}
