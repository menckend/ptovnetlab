from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Switch:
    """Represents a switch being modeled in the virtual lab."""
    name: str  # Switch name received as input argument
    model: str  # Switch model
    eos_version: str  # EOS version
    system_mac: str  # System MAC address
    serial_number: str  # Serial number
    lldp_system_name: str  # LLDP system name
    ethernet_interfaces: int  # Number of Ethernet interfaces
    gns3_template_id: str  # GNS3 image-template ID
    gns3_node_id: str  # GNS3 node-ID
    docker_container_id: str  # Docker container ID
    initial_config: List = field(default_factory=list)  # The modeled device's configuration
    vendor_platform: str = ''  # Switch vendor/platform (optional)
    qemu_vm_id: str = ''  # QEMU VM ID (optional)

@dataclass
class Connection:
    """Represents a connection between two switches in the virtual lab."""
    switch_a: str  # LLDP system name of the first switch
    port_a: str  # Port on the first switch
    switch_b: str  # LLDP system name of the second switch
    port_b: str  # Port on the second switch
