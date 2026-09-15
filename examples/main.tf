# The README's worked example. test_checkov_ledger.py case 28 validates
# checkov-ledger.json against this file, so the two cannot drift apart.
resource "aws_s3_bucket" "state" {
  bucket = "example-state"
}

data "aws_iam_policy_document" "kms" {
  statement {
    # checkov:skip=CKV_AWS_109: key's own resource-based policy; "*" means "this key"
    actions   = ["kms:*"]
    resources = ["*"]
  }
}
