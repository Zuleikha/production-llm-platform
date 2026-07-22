variable "name" {
  description = "Name prefix for cluster resources."
  type        = string
}

variable "kubernetes_version" {
  description = "EKS control-plane Kubernetes version."
  type        = string
}

variable "subnet_ids" {
  description = "Subnet ids the cluster and node group run in (private)."
  type        = list(string)
}

variable "node_instance_types" {
  description = "Instance types for the managed node group."
  type        = list(string)
}

variable "node_desired_size" {
  description = "Desired node count."
  type        = number
}

variable "node_min_size" {
  description = "Minimum node count."
  type        = number
}

variable "node_max_size" {
  description = "Maximum node count."
  type        = number
}
