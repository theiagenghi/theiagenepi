terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 4.45"
    }
  }

  backend "s3" {
    bucket         = "genepi-prod-s3-tf-state-prod-prod-genepi-theia-stacks-new-state"
    dynamodb_table = "genepi-prod-s3-tf-state-prod-prod-genepi-theia-stacks-new-state-lock"
    key            = "czgenepi-prod.tfstate"
    encrypt        = true
    region         = "us-west-2"
    profile        = "theia-prod"
  }
}