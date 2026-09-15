# A directory Checkov can never report on: it ships no policies for the
# github provider. Listed in ../checkov-ledger.json's known_invisible.
resource "github_repository" "example" {
  name = "example"
}
