"""ptovnetlab module/script

Entry-point module for ptovnetlab package. Gathers and parses run-state details
 from a list of Arista network switches.  Then creates a GNS3 virtual-
 lab project in which the interrogated devices are emulated."""


import sys
from getpass import getpass
import requests
from ptovnetlab import arista_poller, arista_sanitizer, gns3_worker


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


def p_to_v(**kwargs):
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
    """
    # Guide for which values are held in which indices of switch_vals
    # switch_vals[n][0]= switch name received as input argument
    # switch_vals[n][1]= switch model
    # switch_vals[n][2]= EOS version
    # switch_vals[n][3]= system MAC
    # switch_vals[n][4]= serial number
    # switch_vals[n][5]= LLDP system-name
    # switch_vals[n][6]= Number of Ethernet interfaces
    # switch_vals[n][7]= GNS3 image-template ID
    # switch_vals[n][8]= GNS3 node-ID
    # switch_vals[n][9]= Docker container ID
    # switch_vals[n][10]= switch vendor/platform
    # switch_vals[n][11]= QEMU VM ID

    # Initializing list of LLDP neighbor details we'll use to build a list of connections for GNS3
    connections_to_make = []

    # Initializing nested list for holding the configs of ALL the switches
    allconfigs = []

    # Initializing a list for holding a list of gns3 template_id values and corresponding EOS \
    # version values
    image_map = []
    # Set default values for all anticipated arguments
    filename = ''
    username = ''
    passwd = ''
    switchlist = []
    servername = ''
    prj_name = ''
    run_type = 'module'

    # Pull any expected (and received) arguments from kwargs into their recipient
    # objects
    if 'filename' in kwargs:
        filename = kwargs['filename']
    if 'username' in kwargs:
        username = kwargs['username']
    if 'passwd' in kwargs:
        passwd = kwargs['passwd']
    if 'switchlist' in kwargs:
        switchlist = kwargs['switchlist']
    if 'servername' in kwargs:
        servername = kwargs['servername']
    if 'prjname' in kwargs:
        prj_name = kwargs['prjname']
    if 'runtype' in kwargs:
        run_type = kwargs['runtype']
    if (filename == '' and switchlist == []):
        print("Enter a switch name and press enter.")
        print("(Press Enter without entering a name when done.")
        while True:
            line = input()
            if not line:
                break
            switchlist.append(line)
    if (filename != '') and (switchlist != []):
        print("Don't pass both filename_arg AND switchlist_arg; choose one or the other.")
        exit(1)
    if prj_name == '':
        prj_name = input('Enter a value to use for the GNS project name when modeling the \
                         production switches: ')
    if servername == '':
        servername = input('Enter the name of the GNS3 server: ')
    # Read the input switch-list file, if a filename was provided
    if filename != '':
        switchlist = read_file(filename)

    # Remove any blank entries from switchlist
    switchlist = list(filter(None, switchlist))
    # Prompt for switch/EOS credentials if none were provided
    if username == '':
        username = input('Enter username for Arista EOS login: ')
    if passwd == '':
        passwd = getpass('Enter password for Arista EOS login: ')

    # Call eos_poller.invoker to get runstate from the Arista switches
    switch_vals, connections_to_make, allconfigs = arista_poller.invoker(switchlist,
                                                                      username, passwd,
                                                                      run_type)

    # Loop through all those configs and clean them up for life in a virtual lab,
    # while also grabbing the interface count for later
    for i, val in enumerate(allconfigs):
        allconfigs[i], switch_vals[i][6] = arista_sanitizer.eos_to_ceos(val, switch_vals[i][3])

    # Create a list of the LLDP local-IDs used by our switches
    our_lldp_ids = []
    for val in switch_vals:
        our_lldp_ids.append(val[5])

    # Sanitize connections_to_make list; removing any entries in which either end
    # is NOT one of our switches  (we can't tell GNS3 to create a connection to a
    # node that doesn't exist in the project.)
    connections_to_make[:] = [connx for connx in connections_to_make if
                              list_search(our_lldp_ids, connx[0]) and
                              list_search(our_lldp_ids, connx[2])]

    # Remove A|B-inverted entries in connections_to_make
    # (connections are directionless; so A<>B is the same as B<>A)
    for i in connections_to_make:
        for j in connections_to_make:
            if i[2]+i[3] == j[0]+j[1]:
                connections_to_make.remove(j)

    # Clean up "management1" in the connections_to_make list (using eth0
    #  instead.  Prod Switch management1 interface is presented in cEOS CLI
    #  as Management0, and Docker presents it to the container as eth0, which
    # is how it presents in GNS3

    for i, val in enumerate(connections_to_make):
        connections_to_make[i][1] = val[1].lower()
        connections_to_make[i][3] = val[3].lower()
        if val[1].startswith('management'):
            for j, val2 in enumerate(switch_vals):
                if val[0] == val2[5]:
                    connections_to_make[i][1] = 'ethernet0'
        if val[3].startswith('management'):
            for j, val2 in enumerate(switch_vals):
                if val[2] == val2[5]:
                    connections_to_make[i][3] = 'ethernet0'

    # Set GNS3 URL
    gns3_url = 'http://'+servername+':3080/v2/'
    gns3_url_noapi = 'http://'+servername+':3080/static/web-ui/server/1/project/'

    # Get all of the docker image templates from the GNS3 server so we can figure out
    # which template_id value maps to a specific EOS version when we start building
    # our instances
    r = requests.get(gns3_url + 'templates', auth=('admin', 'admin'), timeout=20)
    
    for x in r.json():
        print(x)
        if x['template_type'] == 'docker':
            image_map.append([x['template_id'], x['image']])

    # Loop through image_map while looping through switch_vals, looking for docker image
    # tags that are good matches for the EOS version data captured in switch_vals,
    #  and updating switch_vals [n][7] value with the corresponding GNS3 UID 
    for i in range(len(switch_vals)):
        for j in range(len(image_map)):
            # strip any trailing garbage from the EOS version reported by the switch API
            fudgedupeosverion = ('ceos:' + predelimiter(switch_vals[i][2].lower(), '-'))
            if fudgedupeosverion == image_map[j][1].lower():
                switch_vals[i][7] = image_map[j][0]
    # create a new project with provided name and grab the project_id
    gnsprj_id = requests.post(gns3_url + 'projects', json={'name': prj_name},
                              timeout=20).json()['project_id']
    # Grab the templates object from the GNS server so we can crawl through it
    # templ_qry_resp = requests.get(gns3_url + 'templates')

    # Invoke function that handles node creation/configuration in GNS3 project
    gns3_worker.invoker(servername, gns3_url, switch_vals,
                        allconfigs, gnsprj_id, connections_to_make)
    # Done!

    # Close the GNS3 project
    requests.post(gns3_url + 'projects' + 'project_id' + 'close')
    return gns3_url_noapi + gnsprj_id


if __name__ == '__main__':
    kwdict = {}
    for arg in sys.argv[1:]:
        splarg = arg.split('=')
        if splarg[0] == 'switchlist':
            splargalt = []
            for swname in splarg[1].split():
                splargalt.append(swname)
            kwdict[splarg[0]] = splargalt
        else:
            kwdict[splarg[0]] = splarg[1]
    kwdict['runtype'] = 'script'
    import ptovnetlab.gns3_worker
    import ptovnetlab.arista_poller
    p_to_v(**kwdict)
