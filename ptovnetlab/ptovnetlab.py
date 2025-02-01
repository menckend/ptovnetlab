"""ptovnetlab module/script

Entry-point module for ptovnetlab package. Gathers and parses run-state details
from a list of Arista network switches. Then creates a GNS3 virtual-
lab project in which the interrogated devices are emulated."""

import sys
from getpass import getpass
import requests
from ptovnetlab import arista_poller, arista_sanitizer, gns3_worker
from ptovnetlab.data_classes import Switch, Connection


def read_file(file_to_read: str) -> list:
    """Open a file and return its contents as a list of strings

    Parameters
    ----------
    file_to_read : str
        The path of the file to be read

    Returns
    -------
    list of lines : list
        The contents of the file as a list of strings
    """
    # Open the file in read mode
    opened_file = open(file_to_read, "r")
    # Each line of the file into an entry in a list called list_of_lines
    list_of_lines = opened_file.read().splitlines()
    # Close the file that was being read
    opened_file.close()
    return list_of_lines


def list_search(list_to_search: list, item_to_find: str) -> bool:
    """Search a list for a specified string

    Parameters
    ----------
    list_to_search : list
        The list to be searched
    item_to_find : str
        The item to search for
    """
    for val in list_to_search:
        if val == item_to_find:
            return True
    return False


def predelimiter(string, delimiter):
  """Returns the section of a string before the first instance of a delimiter."""

  index = string.find(delimiter)
  if index == -1:
    return string  # Delimiter not found, return the whole string
  else:
    return string[:index]


def p_to_v(**kwargs) -> str:
    """Pull switch run-state data, massage it, and turn it into a GNS3 lab

    Parameters
    ---------
    username : str
        The username to be used in authenticating eapi calls to the switches
    passwd : str
        The password to be used in authenticating eapi calls to the switches
    filename : str
        Name of the file containing the list of switches to be interrogated
    switchlist : list
        List object of names of switches to be interrogated.
    servername : str
        The name of the remote GNS3/Docker server
    prjname : str
        The name of the project to create on the GNS3 server

    Returns
    -------
    result : str
        URL to access the created GNS3 project
    """
    # Set default values for all anticipated arguments
    filename = kwargs.get('filename', '')
    username = kwargs.get('username', '')
    passwd = kwargs.get('passwd', '')
    switchlist = kwargs.get('switchlist', [])
    servername = kwargs.get('servername', '')
    prj_name = kwargs.get('prjname', '')
    run_type = kwargs.get('runtype', 'module')

    # Handle interactive input if needed
    if not filename and not switchlist:
        print("Enter a switch name and press enter.")
        print("(Press Enter without entering a name when done.")
        while True:
            line = input()
            if not line:
                break
            switchlist.append(line)
    
    if filename and switchlist:
        print("Don't pass both filename_arg AND switchlist_arg; choose one or the other.")
        exit(1)
    
    if not prj_name:
        prj_name = input('Enter a value to use for the GNS project name when modeling the production switches: ')
    
    if not servername:
        servername = input('Enter the name of the GNS3 server: ')
    
    # Read the input switch-list file, if a filename was provided
    if filename:
        switchlist = read_file(filename)

    # Remove any blank entries from switchlist
    switchlist = list(filter(None, switchlist))
    
    # Prompt for switch/EOS credentials if none were provided
    if not username:
        username = input('Enter username for Arista EOS login: ')
    if not passwd:
        passwd = getpass('Enter password for Arista EOS login: ')

    # Call arista_poller to get runstate from the Arista switches
    switches, connections = arista_poller.invoker(switchlist, username, passwd, run_type)

    # Clean up configs for virtual lab use
    for switch in switches:
        switch = arista_sanitizer.eos_to_ceos(switch)

    # Filter out connections that don't involve our switches
    our_lldp_ids = [switch.lldp_system_name for switch in switches]
    connections = [conn for conn in connections 
                  if conn.switch_a in our_lldp_ids and conn.switch_b in our_lldp_ids]

    # Remove duplicate connections (A->B is same as B->A)
    unique_connections = []
    for conn in connections:
        reverse_exists = any(c.switch_a == conn.switch_b and 
                           c.switch_b == conn.switch_a and
                           c.port_a == conn.port_b and 
                           c.port_b == conn.port_a 
                           for c in unique_connections)
        if not reverse_exists:
            unique_connections.append(conn)
    connections = unique_connections

    # Clean up management interfaces in connections
    for conn in connections:
        if conn.port_a.lower().startswith('management'):
            conn.port_a = 'ethernet0'
        if conn.port_b.lower().startswith('management'):
            conn.port_b = 'ethernet0'

    # Set GNS3 URL
    gns3_url = f'http://{servername}:3080/v2/'
    gns3_url_noapi = f'http://{servername}:3080/static/web-ui/server/1/project/'

    # Get GNS3 templates and map EOS versions to template IDs
    r = requests.get(gns3_url + 'templates', auth=('admin', 'admin'), timeout=20)
    image_map = {x['image'].lower(): x['template_id'] 
                 for x in r.json() 
                 if x['template_type'] == 'docker'}

    # Set template IDs for switches based on their EOS version
    for switch in switches:
        eos_version = 'ceos:' + predelimiter(switch.eos_version.lower(), '-')
        if eos_version in image_map:
            switch.gns3_template_id = image_map[eos_version]

    # Create new GNS3 project
    gnsprj_id = requests.post(gns3_url + 'projects', 
                            json={'name': prj_name},
                            timeout=20).json()['project_id']

    # Create nodes and connections in GNS3
    gns3_worker.invoker(servername, gns3_url, switches, gnsprj_id, connections)

    # Close the GNS3 project
    requests.post(gns3_url + 'projects/' + gnsprj_id + '/close')
    return gns3_url_noapi + gnsprj_id


if __name__ == '__main__':
    kwdict = {}
    for arg in sys.argv[1:]:
        splarg = arg.split('=')
        if splarg[0] == 'switchlist':
            kwdict[splarg[0]] = splarg[1].split()
        else:
            kwdict[splarg[0]] = splarg[1]
    kwdict['runtype'] = 'script'
    p_to_v(**kwdict)
