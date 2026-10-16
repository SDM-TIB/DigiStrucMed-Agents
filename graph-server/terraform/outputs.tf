output "sparql_endpoint" {
  description = "set as GRAPH_ENDPOINT of the app (reachable inside the VPC only)"
  value       = "http://${google_compute_address.qlever.address}:${var.sparql_port}"
}

output "graph_bucket" {
  value = google_storage_bucket.graph.name
}

output "instance" {
  value = google_compute_instance.qlever.name
}

output "machine_type" {
  value = google_compute_instance.qlever.machine_type
}
