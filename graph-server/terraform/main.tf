# The graph server: QLever (SPARQL) on one small Compute Engine VM, reachable only from inside the VPC
# (Cloud Run via Direct VPC egress) and, for administrators, through Identity-Aware Proxy (IAP) TCP forwarding.
# See ../README.md for what runs where, the costs and the procedures.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  backend "gcs" {} # bucket and prefix in backend.hcl (terraform init -backend-config=backend.hcl)
}

provider "google" {
  project               = var.project
  region                = var.region
  zone                  = var.zone
  billing_project       = var.project
  user_project_override = true
}

locals {
  graph = jsondecode(file("${path.module}/../graph.lock.json")) # which graph file the server must serve (publish_graph.py)
  tag   = "qlever"
}

data "google_compute_network" "vpc" {
  name = var.network
}

data "google_compute_subnetwork" "subnet" {
  name   = var.subnetwork
  region = var.region
}

# ---- the graph file lives in its own bucket; publish_graph.py uploads it under its sha256
resource "google_storage_bucket" "graph" {
  name                        = "${var.project}-graph"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  storage_class               = "STANDARD"
}

resource "google_service_account" "qlever" {
  account_id   = "qlever-graph-server"
  display_name = "QLever graph server (reads the graph bucket)"
}

resource "google_storage_bucket_iam_member" "qlever_reads_graph" {
  bucket = google_storage_bucket.graph.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.qlever.email}"
}

# ---- network: a fixed internal address for the app to call, outbound internet through Cloud NAT, no public address
resource "google_compute_address" "qlever" {
  name         = "qlever-internal"
  address_type = "INTERNAL"
  subnetwork   = data.google_compute_subnetwork.subnet.id
  region       = var.region
}

resource "google_compute_router" "nat" {
  name    = "qlever-nat-router"
  network = data.google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = "qlever-nat"
  router                             = google_compute_router.nat.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"
  subnetwork {
    name                    = data.google_compute_subnetwork.subnet.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }
}

resource "google_compute_firewall" "iap" {
  name          = "qlever-allow-iap"
  network       = data.google_compute_network.vpc.id
  description   = "administrators through IAP TCP forwarding: ssh, SPARQL endpoint, QLever UI"
  source_ranges = ["35.235.240.0/20"] # Google's IAP forwarding range
  target_tags   = [local.tag]
  allow {
    protocol = "tcp"
    ports    = ["22", tostring(var.sparql_port), tostring(var.ui_port)]
  }
}

resource "google_compute_firewall" "app" {
  name          = "qlever-allow-app"
  network       = data.google_compute_network.vpc.id
  description   = "the TIB Chat app (Cloud Run, Direct VPC egress from this subnet) queries the SPARQL endpoint"
  source_ranges = [data.google_compute_subnetwork.subnet.ip_cidr_range]
  target_tags   = [local.tag]
  allow {
    protocol = "tcp"
    ports    = [tostring(var.sparql_port)]
  }
}

# ---- the index lives on its own disk: the VM can be resized or recreated without rebuilding it
resource "google_compute_disk" "data" {
  name = "qlever-data"
  type = var.data_disk_type
  size = var.data_disk_gb
  zone = var.zone
}

resource "google_compute_instance" "qlever" {
  name                      = "qlever-graph-server"
  machine_type              = var.machine_type
  zone                      = var.zone
  tags                      = [local.tag]
  allow_stopping_for_update = true # machine_type changes (index build on a large type, serving on a small one)

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
      type  = "pd-standard"
    }
  }

  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "qlever-data"
  }

  network_interface {
    subnetwork = data.google_compute_subnetwork.subnet.id
    network_ip = google_compute_address.qlever.address
    # no access_config: no public IP. outbound through Cloud NAT
  }

  service_account {
    email  = google_service_account.qlever.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin  = "TRUE"
    startup-script  = file("${path.module}/../startup.sh")
    qleverfile      = file("${path.module}/../Qleverfile")
    graph-uri       = "gs://${google_storage_bucket.graph.name}/${local.graph.object}"
    graph-sha256    = local.graph.sha256
    graph-file      = local.graph.file
    qlever-image    = var.qlever_image
    qlever-ui-image = var.qlever_ui_image
    qlever-cli      = var.qlever_cli_version
  }

  depends_on = [google_compute_router_nat.nat, google_storage_bucket_iam_member.qlever_reads_graph]
}
