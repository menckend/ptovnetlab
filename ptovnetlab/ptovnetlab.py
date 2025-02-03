"""PTovNetLab: Automated Network Lab Creation

This module converts physical network switch configurations into a GNS3 virtual lab.
"""

import sys
import logging
from typing import List, Dict, Optional, Tuple
from getpass import getpass
import requests

from ptovnetlab import arista_poller, arista_sanitizer, gns3_worker
from ptovnetlab.data_classes import Switch, Connection

# Custom Exceptions
class PTovNetLabError(Exception):
    """Base exception for PTovNetLab errors."""
    pass

class InputValidationError(PTovNetLabError):
    """Raised when input validation fails."""
    pass

class NetworkConfigurationError(PTovNetLabError):
    """Raised when network configuration processing fails."""
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def validate_input(
    filename: Optional[str] = None, 
    switchlist: Optional[List[str]] = None, 
    prj_name: Optional[str] = None, 
    servername: Optional[str] = None
) -> None:
    """
    Validate input parameters for the virtual lab creation.

    Args:
        filename: Optional path to a file containing switch names
        switchlist: Optional list of switch names
        prj_name: Optional project name
        servername: Optional GNS3 server name

    Raises:
        InputValidationError: If input parameters are invalid
    """
    if filename and switchlist:
        raise InputValidationError(
            "Cannot provide both filename and switchlist. Choose one method."
        )
    
    if not (filename or switchlist):
        logger.warning("No switch sources provided. Entering interactive mode.")

    if not prj_name:
        raise InputValidationError("Project name is required")

    if not servername:
        raise InputValidationError("GNS3 server name is required")

def collect_switch_list(
    filename: Optional[str] = None, 
    switchlist: Optional[List[str]] = None
) -> List[str]:
    """
    Collect switch list from file or interactive input.

    Args:
        filename: Optional path to a file containing switch names
        switchlist: Optional list of switch names

    Returns:
        List of switch names
    """
    if filename:
        try:
            with open(filename, 'r') as f:
                return [line.strip() for line in f if line.strip()]
        except IOError as e:
            raise InputValidationError(f"Error reading switch list file: {e}")
    
    if switchlist:
        return switchlist

    # Interactive input
    logger.info("Enter switch names (press Enter without input to finish):")
    interactive_list = []
    while True:
        switch = input().strip()
        if not switch:
            break
        interactive_list.append(switch)
    
    return interactive_list

def authenticate_switches() -> Tuple[str, str]:
    """
    Authenticate switches interactively.

    Returns:
        Tuple of (username, password)
    """
    username = input('Enter username for Arista EOS login: ')
    passwd = getpass('Enter password for Arista EOS login: ')
    return username, passwd

def process_switch_configurations(
    switches: List[Switch], 
    connections: List[Connection]
) -> Tuple[List[Switch], List[Connection]]:
    """
    Process and sanitize switch configurations.

    Args:
        switches: List of switches
        connections: List of connections

    Returns:
        Processed switches and connections
    """
    # Sanitize configurations
    for switch in switches:
        switch = arista_sanitizer.eos_to_ceos(switch)

    # Filter connections involving only the current switches
    our_lldp_ids = {switch.lldp_system_name for switch in switches}
    connections = [
        conn for conn in connections 
        if conn.switch_a in our_lldp_ids and conn.switch_b in our_lldp_ids
    ]

    # Remove duplicate connections
    unique_connections = []
    for conn in connections:
        if not any(
            c.switch_a == conn.switch_b and 
            c.switch_b == conn.switch_a and
            c.port_a == conn.port_b and 
            c.port_b == conn.port_a 
            for c in unique_connections
        ):
            unique_connections.append(conn)

    # Clean management interfaces
    for conn in unique_connections:
        conn.port_a = 'ethernet0' if conn.port_a.lower().startswith('management') else conn.port_a
        conn.port_b = 'ethernet0' if conn.port_b.lower().startswith('management') else conn.port_b

    return switches, unique_connections

def p_to_v(**kwargs) -> str:
    """
    Convert physical network to virtual lab.

    Args:
        **kwargs: Flexible keyword arguments for configuration

    Returns:
        URL to the created GNS3 project
    """
    try:
        # Extract and validate inputs
        filename = kwargs.get('filename', '')
        switchlist = kwargs.get('switchlist', [])
        username = kwargs.get('username', '')
        passwd = kwargs.get('passwd', '')
        servername = kwargs.get('servername', '')
        prj_name = kwargs.get('prjname', '')
        run_type = kwargs.get('runtype', 'module')

        validate_input(filename, switchlist, prj_name, servername)
        
        # Collect switch list
        switchlist = collect_switch_list(filename, switchlist)
        
        # Authenticate if credentials not provided
        if not (username and passwd):
            username, passwd = authenticate_switches()

        # Poll switch configurations
        switches, connections = arista_poller.invoker(switchlist, username, passwd, run_type)
        
        # Process switch configurations
        switches, connections = process_switch_configurations(switches, connections)

        # Set GNS3 URLs
        gns3_url = f'http://{servername}:3080/v2/'
        gns3_url_noapi = f'http://{servername}:3080/static/web-ui/server/1/project/'

        # Get and map GNS3 templates
        r = requests.get(gns3_url + 'templates', auth=('admin', 'admin'), timeout=20)
        image_map = {
            x['image'].lower(): x['template_id'] 
            for x in r.json() 
            if x['template_type'] == 'docker'
        }

        # Set template IDs for switches
        for switch in switches:
            eos_version = 'ceos:' + switch.eos_version.lower().split('-')[0]
            if eos_version in image_map:
                switch.gns3_template_id = image_map[eos_version]

        # Create GNS3 project
        gnsprj_id = requests.post(
            gns3_url + 'projects', 
            json={'name': prj_name},
            timeout=20
        ).json()['project_id']

        # Create nodes and connections
        gns3_worker.invoker(servername, gns3_url, switches, gnsprj_id, connections)

        logger.info(f"Successfully created GNS3 project: {prj_name}")
        return gns3_url_noapi + gnsprj_id

    except (InputValidationError, NetworkConfigurationError) as e:
        logger.error(f"Configuration error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in virtual lab creation: {e}")
        raise PTovNetLabError(f"Virtual lab creation failed: {e}")

def main():
    """
    Main entry point for script execution.
    """
    try:
        kwdict = {}
        for arg in sys.argv[1:]:
            splarg = arg.split('=')
            if splarg[0] == 'switchlist':
                kwdict[splarg[0]] = splarg[1].split()
            else:
                kwdict[splarg[0]] = splarg[1]
        kwdict['runtype'] = 'script'
        p_to_v(**kwdict)
    except PTovNetLabError as e:
        logger.error(f"PTovNetLab execution failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
