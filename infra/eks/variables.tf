variable "region" {
  description = "AWS region for everything."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Prefix for resource names; also the EKS cluster's name."
  type        = string
  default     = "recoengine"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes version (one behind the newest, for add-on support)."
  type        = string
  default     = "1.35"
}

variable "node_instance_type" {
  description = "Worker node size. t3.medium: 2 vCPU, 4 GiB, ~17 pods each."
  type        = string
  default     = "t3.medium"
}

variable "node_count" {
  description = "Worker nodes (the autoscaler may go from 1 up to node_count + 1)."
  type        = number
  default     = 2
}

variable "budget_email" {
  description = "Where AWS emails a warning when this month's spend passes budget_usd."
  type        = string
}

variable "budget_usd" {
  description = "Monthly spend that triggers the warning email."
  type        = number
  default     = 20
}

variable "app_domain" {
  description = "Hostname the app is served on with HTTPS (its DNS is managed elsewhere, e.g. GoDaddy). Empty: no certificate."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "owner/name of the GitHub repo whose main branch may deploy (CI/CD)."
  type        = string
  default     = "sachin-11/recommendation-engine-"
}

variable "github_repository_with_ids" {
  description = "The same repo as owner@owner_id/name@repo_id, the form GitHub's OIDC `sub` claim uses."
  type        = string
  default     = "sachin-11@44609635/recommendation-engine-@1384500853"
}
