resource "proxmox_virtual_environment_vm" "vm" {
  name      = var.vm_name
  node_name = var.proxmox_node

  clone {
    vm_id = var.template_vmid
    full  = var.full_clone
  }

  cpu {
    cores = var.vm_vcpu
  }

  memory {
    dedicated = var.vm_ram_mb
  }

  disk {
    datastore_id = var.disk_storage
    interface    = var.disk_interface
    size         = var.vm_disk_gb
  }

  network_device {
    bridge  = var.network_bridge
    vlan_id = var.vlan_tag
  }

  initialization {
    ip_config {
      ipv4 {
        address = var.vm_ip_address
        gateway = var.vm_gateway
      }
    }

    dns {
      servers = var.vm_dns_servers
    }

    # Root temporaire par mot de passe : bootstrap unique pour deploy-ssh-keys.yml,
    # fermé pour de bon juste après par harden-root.yml. Voir vm_bootstrap_root_password.
    user_account {
      username = "root"
      password = var.vm_bootstrap_root_password
    }
  }
}
