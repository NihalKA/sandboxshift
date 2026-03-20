# Remote state backend — stores terraform.tfstate in S3 instead of locally.
#
# WHY: Local state is lost if your machine dies or the file is deleted.
#      S3 state is durable, versioned, and never accidentally committed to git.
#
# BOOTSTRAP (one-time, before terraform init):
#   Run this once to create the state bucket:
#
#     aws s3api create-bucket \
#       --bucket sandboxshift-tfstate-<your-account-id> \
#       --region us-east-1
#
#     aws s3api put-bucket-versioning \
#       --bucket sandboxshift-tfstate-<your-account-id> \
#       --versioning-configuration Status=Enabled
#
#     aws s3api put-public-access-block \
#       --bucket sandboxshift-tfstate-<your-account-id> \
#       --public-access-block-configuration \
#         BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
#
# Then replace <your-account-id> below and run: terraform init
# Terraform will offer to migrate existing local state to S3 automatically.
#
# RECOVERY: If you lose access to the bucket, run:
#   aws s3 cp s3://<bucket>/sandboxshift/fargate/terraform.tfstate ./terraform.tfstate

terraform {
  backend "s3" {
    bucket  = "sandboxshift-tfstate-<your-account-id>"
    key     = "sandboxshift/fargate/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true

    # State locking — prevents concurrent applies corrupting state.
    # Uses a DynamoDB table. Create it once:
    #
    #   aws dynamodb create-table \
    #     --table-name sandboxshift-tfstate-lock \
    #     --attribute-definitions AttributeName=LockID,AttributeType=S \
    #     --key-schema AttributeName=LockID,KeyType=HASH \
    #     --billing-mode PAY_PER_REQUEST \
    #     --region us-east-1
    #
    dynamodb_table = "sandboxshift-tfstate-lock"
  }
}
