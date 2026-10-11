variable "vsphere_server" {
  description = "Adresse du vCenter (sans https://)"
  type        = string
}

variable "vsphere_user" {
  type = string
}

variable "vsphere_password" {
  type      = string
  sensitive = true
}

variable "vsphere_insecure" {
  description = "Ignorer la validation TLS (certificat auto-signé de labo)"
  type        = bool
  default     = true
}

variable "datacenter" {
  type = string
}

variable "host" {
  description = "Nom de l'hôte ESXi cible (hôte standalone, pas un vrai cluster DRS/HA)"
  type        = string
}

variable "datastore" {
  type = string
}

variable "network" {
  description = "Nom du port group / réseau vSphere"
  type        = string
}

variable "template_name" {
  description = "Nom du template à cloner (doit avoir cloud-init + open-vm-tools, voir JOURNAL.md)"
  type        = string
}

variable "vm_name" {
  type = string
}

variable "vm_vcpu" {
  type = number
}

variable "vm_ram_mb" {
  type = number
}

variable "vm_disk_gb" {
  type = number
}

# CIDR complet (ex: "192.168.1.160/24") -> cloud-init réseau v2 veut le préfixe inclus,
# pas juste l'IP seule (contrairement au bloc ip_config de bpg/proxmox).
variable "vm_ip_cidr" {
  type = string
}

variable "vm_gateway" {
  type = string
}

variable "vm_dns_servers" {
  type    = list(string)
  default = []
}

variable "vm_dns_domain" {
  type    = string
  default = null
}

# Root temporaire par mot de passe : même principe que le module Proxmox (bootstrap
# unique pour deploy-ssh-keys.yml, fermé pour de bon juste après par harden-root.yml).
variable "vm_bootstrap_root_password" {
  type      = string
  sensitive = true
}
