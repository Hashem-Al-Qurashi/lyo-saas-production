# Lyo SaaS - Step 1: VPC Only
# Senior DevOps: Build incrementally and validate each step

terraform {
  required_version = ">= 1.0"
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket = "lyo-saas-terraform-state"
    key    = "vpc/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# Step 1: VPC Foundation
module "vpc" {
  source = "./modules/vpc"
  
  project_name         = var.project_name
  environment          = var.environment
  vpc_cidr            = var.vpc_cidr
  public_subnet_count = var.public_subnet_count
  private_subnet_count = var.private_subnet_count
}