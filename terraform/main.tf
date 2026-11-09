resource "google_container_cluster" "learning" {
  name                     = "gpu-inference-learning"
  location                 = var.zone
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = false

  release_channel { channel = "REGULAR" }
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
}

resource "google_container_node_pool" "cpu" {
  name       = "cpu-pool"
  cluster    = google_container_cluster.learning.name
  location   = var.zone
  node_count = 1

  node_config {
    machine_type = "e2-standard-2"
    image_type   = "COS_CONTAINERD"
    disk_size_gb = 100
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}

resource "google_container_node_pool" "gpu" {
  name       = "gpu-l4-pool"
  cluster    = google_container_cluster.learning.name
  location   = var.zone
  node_count = var.gpu_node_count

  node_config {
    machine_type = "g2-standard-4"
    image_type   = "COS_CONTAINERD"
    disk_size_gb = 100
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    guest_accelerator {
      type  = "nvidia-l4"
      count = 1
      gpu_driver_installation_config {
        gpu_driver_version = "DEFAULT"
      }
    }

    labels = { workload = "gpu-inference" }
    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }
  }
}
