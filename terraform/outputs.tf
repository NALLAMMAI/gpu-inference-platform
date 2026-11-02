output "cluster_name" { value = google_container_cluster.learning.name }
output "cluster_zone" { value = var.zone }
output "gpu_node_count" { value = var.gpu_node_count }
