variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "gpu_node_count" {
  type    = number
  default = 0
  validation {
    condition     = contains([0, 1], var.gpu_node_count)
    error_message = "Learning environment supports zero or one GPU nodes."
  }
}
