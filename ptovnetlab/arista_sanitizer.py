#!/usr/bin/env python3
"""
EOS to cEOS Configuration Sanitization Module

This module provides functionality for converting Arista hardware appliance 
EOS configurations to be suitable for use in a cEOS (containerized EOS) 
lab environment.
"""

import logging
from typing import List, Optional

from ptovnetlab.data_classes import Switch

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EosSanitizerError(Exception):
    """Custom exception for EOS to cEOS configuration sanitization errors."""
    pass

def eos_to_ceos(switch: Switch) -> Switch:
    """
    Convert an Arista switch configuration for cEOS lab environment.

    This function modifies the configuration to be compatible with 
    containerized EOS, including:
    - Replacing management interface names
    - Removing incompatible configuration lines
    - Handling interface configurations
    - Applying system MAC address

    Args:
        switch (Switch): Switch object containing the original configuration

    Returns:
        Switch: Updated Switch object with cEOS-compatible configuration
    """
    try:
        # Validate input
        if not switch or not switch.initial_config:
            raise EosSanitizerError("Invalid switch configuration: Empty config")

        # Configuration lines to be commented out in cEOS lab environment
        bad_starts = [
            'radius', 'username', 'aaa', 'ip radius', 'hardware speed', 
            'queue', 'server ', 'ntp server', 'daemon TerminAttr', 
            '   exec /usr/bin/TerminAttr'
        ]

        # Count Ethernet interfaces
        switch.ethernet_interfaces = _count_ether_interfaces(switch.initial_config)

        # Process configuration lines
        sanitized_config = _sanitize_config_lines(
            switch.initial_config, 
            bad_starts, 
            switch.system_mac
        )

        # Update switch with sanitized configuration
        switch.initial_config = sanitized_config

        logger.info(f"Converted configuration for switch: {switch.name}")
        return switch

    except Exception as e:
        logger.error(f"Configuration conversion failed for {switch.name}: {e}")
        raise EosSanitizerError(f"Conversion error: {e}")

def _sanitize_config_lines(
    config_lines: List[str], 
    bad_starts: List[str], 
    system_mac: str
) -> List[str]:
    """
    Sanitize configuration lines for cEOS compatibility.

    Args:
        config_lines (List[str]): Original configuration lines
        bad_starts (List[str]): Prefixes of lines to be commented out
        system_mac (str): System MAC address to be applied

    Returns:
        List[str]: Sanitized configuration lines
    """
    sanitized_lines = []
    mgt_port_str = 'Management0'

    for line in config_lines:
        # Replace Management1/Management0 interface names
        line = line.replace('Management1', mgt_port_str)
        line = line.replace('Management0', mgt_port_str)

        # Comment out lines starting with bad prefixes
        if any(line.startswith(bad) for bad in bad_starts):
            line = f"!removed_for_cEOS-lab| {line}"

        # Handle spurious interface configurations
        if line.startswith('interface Ethernet'):
            # Remove breakout interfaces (/2, /3, /4)
            if any(f'/{n}' in line for n in [2, 3, 4]):
                line = f"!{line}"
            # Simplify interface names (remove subinterface)
            else:
                line = line.split('/')[0]

        sanitized_lines.append(line)

    # Apply system MAC address configuration
    sanitized_lines = _apply_sys_mac(sanitized_lines, system_mac)

    return sanitized_lines

def _count_ether_interfaces(config: List[str]) -> int:
    """
    Count the number of Ethernet interfaces in the configuration.

    Args:
        config (List[str]): List of configuration lines

    Returns:
        int: Number of Ethernet interfaces
    """
    return sum(
        1 for line in config 
        if (line.startswith('interface Ethernet') and 
            not any(line.endswith(f'/{n}') for n in [2, 3, 4]))
    )

def _apply_sys_mac(config: List[str], sys_mac: str) -> List[str]:
    """
    Append system MAC address configuration to the config.

    Args:
        config (List[str]): Original configuration lines
        sys_mac (str): System MAC address to apply

    Returns:
        List[str]: Updated configuration with system MAC handler
    """
    # Create event handler to set system MAC address
    sys_mac_snippet = [
        '',
        'event-handler onStartup',
        ' trigger on-boot',
        ' action bash',
        f'      var_sysmac=\'{sys_mac}\'',
        '  echo $var_sysmac > /mnt/flash/system_mac_address',
        '  truncate -s -1 /mnt/flash/system_mac_address',
        '  EOF'
    ]

    # Remove the last line ('end') and append system MAC config
    if config and config[-1] == 'end':
        config.pop()
        config.extend(sys_mac_snippet)
        config.append('end')

    return config
