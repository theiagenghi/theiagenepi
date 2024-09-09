terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 4.45"
    }
  }

  backend "s3" {
    bucket         = "genepi-theia-dev-s3-tf-state-dev-dev-genepi-theia-stacks-state"
    dynamodb_table = "genepi-theia-dev-s3-tf-state-dev-dev-genepi-theia-stacks-state-lock"
    key            = "czgenepi-staging.tfstate"
    encrypt        = true
    region         = "us-west-2"
    profile        = "theia"
  }
}