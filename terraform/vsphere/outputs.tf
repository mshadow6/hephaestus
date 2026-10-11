output "vm_id" {
  description = "UUID vSphere de la VM créée"
  value       = vsphere_virtual_machine.vm.id
}
