variable "proxmox_api_url" {
  description = "URL de l'API Proxmox (ex: https://192.0.2.20:8006/)"
  type        = string
  sensitive   = true
}

variable "proxmox_api_token" {
  description = "Token API Proxmox, format user@realm!tokenid=uuid"
  type        = string
  sensitive   = true
}

variable "proxmox_insecure" {
  description = "Ignorer la validation du certificat TLS (certificat auto-signé en homelab)"
  type        = bool
  default     = true
}

variable "proxmox_node" {
  description = "Nom du noeud Proxmox cible"
  type        = string
}

variable "vm_name" {
  description = "Nom de la VM (hostname)"
  type        = string
}

variable "vm_vcpu" {
  description = "Nombre de vCPU"
  type        = number
}

variable "vm_ram_mb" {
  description = "RAM en Mo"
  type        = number
}

variable "vm_disk_gb" {
  description = "Taille du disque en Go"
  type        = number
}

variable "template_vmid" {
  description = "VM ID du template Proxmox à cloner"
  type        = number
}

variable "vlan_tag" {
  description = "Tag VLAN de l'interface réseau"
  type        = number
}

variable "network_bridge" {
  description = "Bridge réseau Proxmox"
  type        = string
  default     = "vmbr0"
}

variable "disk_storage" {
  description = "Pool de stockage Proxmox pour le disque"
  type        = string
  default     = "local-lvm"
}

variable "disk_interface" {
  description = "Interface du disque à redimensionner (doit correspondre à celle du template)"
  type        = string
  default     = "scsi0"
}

variable "full_clone" {
  description = "Clone complet (true) vs clone lié (false)"
  type        = bool
  default     = true
}

# Réseau statique — volontairement obligatoire, pas de fallback DHCP : un serveur ne
# doit jamais dépendre du DHCP (IP fournie par le vrai IPAM, appliquée ici).
variable "vm_ip_address" {
  description = "IP statique en notation CIDR, ex: 192.0.2.200/24"
  type        = string
}

variable "vm_gateway" {
  description = "Passerelle par défaut"
  type        = string
}

variable "vm_dns_servers" {
  description = "Serveurs DNS à configurer sur la VM"
  type        = list(string)
  default     = []
}

# Mot de passe root temporaire, généré aléatoirement par le worker pour cette VM
# uniquement — sert une seule fois à `deploy-ssh-keys.yml` (root encore accessible par
# mot de passe juste après le clone) avant que `harden-root.yml` ne ferme root en SSH
# pour de bon. Jamais réutilisé, jamais stocké après la fin du job.
variable "vm_bootstrap_root_password" {
  description = "Mot de passe root éphémère (cloud-init), utilisé une seule fois par le post-install Ansible"
  type        = string
  sensitive   = true
}
