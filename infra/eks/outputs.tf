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

output "certificate_arn" {
  description = "For the Ingress's alb.ingress.kubernetes.io/certificate-arn annotation."
  value       = one(aws_acm_certificate.app[*].arn)
}

output "certificate_validation" {
  description = "The DNS record to add at the domain's registrar so ACM issues the certificate."
  value = [for o in flatten(aws_acm_certificate.app[*].domain_validation_options) : {
    type  = o.resource_record_type
    name  = o.resource_record_name
    value = o.resource_record_value
  }]
}
