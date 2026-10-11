data "vsphere_datacenter" "dc" {
  name = var.datacenter
}

data "vsphere_host" "host" {
  name          = var.host
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_datastore" "datastore" {
  name          = var.datastore
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "network" {
  name          = var.network
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template" {
  name          = var.template_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

locals {
  # MAC fixe dans la plage VMware réservée aux adresses assignées manuellement
  # (00:50:56:00:00:00 à 00:50:56:3F:FF:FF - documenté par VMware, le reste de l'espace
  # 00:50:56:xx:xx:xx est réservé à la génération automatique de vCenter). Le premier
  # octet généré est masqué à 0x3F (& 63) : sans ça, vCenter ignore silencieusement la
  # MAC demandée et assigne la sienne à la place si elle tombe hors de cette plage -
  # cloud-init cherche alors une interface qui n'existe jamais, trouvé en conditions
  # réelles (VM retombée sur une IP DHCP au lieu de l'IP statique voulue).
  mac_address = format(
    "00:50:56:%02x:%02x:%02x",
    parseint(substr(md5(var.vm_name), 0, 2), 16) % 64,
    parseint(substr(md5(var.vm_name), 2, 2), 16),
    parseint(substr(md5(var.vm_name), 4, 2), 16),
  )

  metadata_yaml = yamlencode({
    instance-id    = var.vm_name
    local-hostname = var.vm_name
    network = {
      version = 2
      ethernets = {
        bootstrap = {
          match        = { macaddress = local.mac_address }
          set-name     = "eth0"
          addresses    = [var.vm_ip_cidr]
          gateway4     = var.vm_gateway
          nameservers = {
            addresses = var.vm_dns_servers
            search    = var.vm_dns_domain != null ? [var.vm_dns_domain] : []
          }
        }
      }
    }
  })

  # Root temporaire par mot de passe, même principe que le bootstrap Proxmox -
  # ssh_pwauth force l'activation de l'auth par mot de passe au cas où le template l'a
  # désactivée par défaut (image durcie) ; deploy-ssh-keys.yml dépose ensuite la vraie
  # clé et harden-root.yml referme tout ça pour de bon.
  # L'en-tête "#cloud-config" est obligatoire - sans lui cloud-init logue "Unhandled
  # non-multipart userdata" et ignore tout le contenu en silence (trouvé en conditions
  # réelles : le mot de passe root n'était jamais appliqué, aucune erreur bloquante).
  userdata_yaml = "#cloud-config\n${yamlencode({
    chpasswd = {
      list   = "root:${var.vm_bootstrap_root_password}"
      expire = false
    }
    ssh_pwauth = true
  })}"
}

resource "vsphere_virtual_machine" "vm" {
  name             = var.vm_name
  resource_pool_id = data.vsphere_host.host.resource_pool_id
  datastore_id     = data.vsphere_datastore.datastore.id
  host_system_id   = data.vsphere_host.host.id

  num_cpus = var.vm_vcpu
  memory   = var.vm_ram_mb
  guest_id = data.vsphere_virtual_machine.template.guest_id
  scsi_type = data.vsphere_virtual_machine.template.scsi_type

  wait_for_guest_net_timeout = 0
  wait_for_guest_ip_timeout  = 0

  network_interface {
    network_id     = data.vsphere_network.network.id
    adapter_type   = data.vsphere_virtual_machine.template.network_interface_types[0]
    mac_address    = local.mac_address
    # Sans ça, la mac_address fournie n'est qu'une suggestion que vCenter peut ignorer
    # et remplacer par la sienne (trouvé en conditions réelles : cloud-init cherchait
    # une carte dont l'adresse MAC n'a jamais existé sur la VM réelle).
    use_static_mac = true
  }

  disk {
    label            = "disk0"
    size             = var.vm_disk_gb
    eagerly_scrub    = data.vsphere_virtual_machine.template.disks[0].eagerly_scrub
    thin_provisioned = data.vsphere_virtual_machine.template.disks[0].thin_provisioned
  }

  clone {
    template_uuid = data.vsphere_virtual_machine.template.id
  }

  # guestinfo brut plutôt que le bloc vapp{} structuré de Terraform : vapp{} suppose que
  # le template a un schéma de propriétés vApp déjà déclaré (cas des appliances OVA) -
  # un template "maison" cloné depuis une install classique n'en a pas, extra_config
  # écrit directement en clé VMX guestinfo.*, que le datasource VMware de cloud-init lit
  # pareil via open-vm-tools, sans ce prérequis.
  extra_config = {
    "guestinfo.metadata"          = base64encode(local.metadata_yaml)
    "guestinfo.metadata.encoding" = "base64"
    "guestinfo.userdata"          = base64encode(local.userdata_yaml)
    "guestinfo.userdata.encoding" = "base64"
  }
}
