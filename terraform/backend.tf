terraform {
  # The state bucket name is passed at init time so it isn't committed:
  #   terraform init -backend-config="bucket=<YOUR_STATE_BUCKET>"
  # GitHub Actions passes it from the TF_STATE_BUCKET repository variable.
  backend "gcs" {
    prefix = "gke/terraform/state"
  }
}
