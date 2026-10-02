# Terraform and the AWS provider. Every resource gets the default tags below, so
# everything this creates can be found (and its cost seen) in the AWS console.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "recoengine"
      Purpose   = "eks-learning"
      ManagedBy = "terraform"
    }
  }
}
