terraform {
  backend "gcs" {
    bucket = "gpu-inference-tfstate-4643030840"
    prefix = "gke/terraform/state"
  }
}
