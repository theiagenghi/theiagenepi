provider "aws" {
  region              = "us-west-2"
  allowed_account_ids = [var.aws_account_id]
  profile             = "theia"
}
