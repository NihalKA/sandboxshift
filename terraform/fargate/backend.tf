# Terraform state is stored locally (terraform.tfstate in this directory).
#
# Why local and not S3?
# The S3 workspace bucket is *created by* this Terraform config (main.tf).
# Storing Terraform state in S3 would require a separate pre-existing bucket —
# a circular dependency with no clean bootstrap path.
#
# For a V1 single-developer tool, local state is the correct choice:
#   - No pre-existing infrastructure required
#   - State file lives alongside the config in terraform/fargate/
#   - terraform.tfstate is gitignored (contains sensitive ARNs/IDs)
terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
