# Placeholder — overwritten at runtime by sandboxshift-setup.sh.
#
# sandboxshift-setup.sh creates an S3 bucket and DynamoDB table via AWS CLI
# (before terraform init runs), then writes this file with real values:
#
#   Bucket: sandboxshift-tfstate-<account_id>-<6char_hash>
#   Table:  sandboxshift-tfstate-lock-<6char_hash>
#
# This placeholder uses a local backend so that:
#   - `terraform validate` works without AWS credentials
#   - New clones don't fail before setup.sh has run
# The local terraform.tfstate (if any) is automatically migrated to S3
# the next time ./sandboxshift-setup.sh is run.
terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
