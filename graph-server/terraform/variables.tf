variable "project" {
  type    = string
  default = "tib-chat-1011-13273"
}

variable "region" {
  type    = string
  default = "europe-west3" # same region as the Cloud Run service
}

variable "zone" {
  type    = string
  default = "europe-west3-a"
}

variable "network" {
  type    = string
  default = "default"
}

variable "subnetwork" {
  type    = string
  default = "default"
}

variable "machine_type" {
  type        = string
  description = "e2-standard-4 (16 GB) for the one-off index build, then the serving size (see README)"
  default     = "e2-small"
}

variable "data_disk_type" {
  type    = string
  default = "pd-balanced"
}

variable "data_disk_gb" {
  type    = number
  default = 40
}

variable "sparql_port" {
  type    = number
  default = 7001
}

variable "ui_port" {
  type    = number
  default = 8176
}

variable "qlever_image" {
  type        = string
  description = "QLever server and index builder, pinned by digest (tag commit-23a793a98c, 16 October 2026)"
  default     = "docker.io/adfreiburg/qlever@sha256:32836c914eabdc9107669f154305b53708a817d1ff5b2d9881d1274dbfb174c2"
}

variable "qlever_ui_image" {
  type        = string
  description = "QLever UI, pinned by digest (tag latest of 26 April 2026)"
  default     = "docker.io/adfreiburg/qlever-ui@sha256:2bdbfac438c8d43ae5337bb547993b1880e5c43b63871c74ca180aa123644851"
}

variable "qlever_cli_version" {
  type    = string
  default = "0.6.0"
}
