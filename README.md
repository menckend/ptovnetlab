# Preamble

I wrote this package (ptovnetlab) specifically to assist in *fast/low-effort* modeling of a production EVPN Layer-3 Leaf & Spine physical network of Arista switches in a pre-existing GNS3 server, using Arista cEOS docker images.  (Also, to teach myself how to write/publish Python packages, but *really* for modeling Arista switches.)  I'd like to scale it up to supporting addditional virtual-lab platforms and network-device vendors' equipment, but all things have to start *somewhere*.

- [Documentation](https://menckend.github.io/ptovnetlab/)
- [Repository](https://github.com/menckend/ptovnetlab)
- [Python Package](https://pypi.org/project/ptovnetlab/)

## What it does

- Grabs running configuration, version info, and lldp neighbor information from a list of Arista switches
-   Retreived using Arista's "eAPI"
- Sanitizes the switch configs for use in a cEOS environment
  - Removes all AAA and username configuration
  - Reformats interface names to match the cEOS interface naming convention  Ethernet_n , not Ethernt_n/1
  - Comments out incompatible commands ("queue..."/etc. not supported on cEOS)
  - Configures the system mac-address of the production switch
    - Avoids mLAG incompatibility with older versions of cEOS
      - Docker container default mac address has U/L bit set to L instead of U, which prevents MLAG from working
- Builds a table of interconnections between the switches
  - Inferred from the "show lldp neighbor" and "show lldp local" output collected from the switches
- Creates a GNS3 project
  - Instantiates a cEOS container node in the project for each switch in the input list
  - Modeled devices mirror the following properties of the switches they are modeling:
    -  cEOS version (a pre-existing GNS3 docker template using the matching cEOS version must be present) 
    -  Ethernet interface count
    -  Startup configuration
       -  "startup-config" is passed directly to the Docker API, allowing ptovnetlab to run separately from the GNS3 server
  - Creates the connections between the GNS3/cEOS nodes, mirroring all inter-switch connections discovered in LLDP tables

## What you'll need

### Python

- The ptovnetlab project was written using Python 3.12; it will run on versions as low as 3.9.
  - It relies on 'asyncio.to_thread()' (introduced in Python 3.9) 
- The host running the ptovnetlab packages will need to have Python and the packages listed in the dependencies section of pyproject.toml installed
- Once Python is installed, use pip to install ptovnetlab (which will install its dependencies as well):
  -  'pip install --user ptovnetlab'

### GNS3 server

- The ptovnetlab package was written against version 2.2.52 of GNS3 server.
  - Version 3.x of GNS3 server isn't compatible  (on my to-do list)
- The GNS3 server must be pre-configured with cEOS docker templates
  - ptovnetlab will compare the EOS version string returned by the switches you're modeling to the names you've applied to the corresponding templates  on the GNS3 server
    - The GNS templates built on the docker images need to be named as "ceos:*n.n...*" for the matching to work
- A container management service (typically dockerd or containerd) must be listening on TCP port 2375 of the GNS3 server
  - ptovnetlab makes API calls directly to the GNS3 server's container management service to copy configuration files directly onto the containers' filesystems.

#### Setting up GNS3 server

- Install GNS3 server as per GNS3 documentation (on a Linux OS)
  - Do not use any of the 3.x releases, only 2.x releases
  - Be sure to have Docker installed/running on the host that GNS3 is installed on
  - Not just the executables, but also the containerd/dockerd _services_
- Obtain cEOS images
- Import cEOS images to Docker (on the host where GNS3server is installed)
  - 'docker import -c 'VOLUME /mnt/flash/' cEOS-lab-_n.m.p_.tar.xz ceoslab:_n.m.p_.'
    - This is important; you have to include a persistent volume definition for /mnt/flash
- Create GNS3 templates for Arista cEOS images
  - Follow the GNS3 documentation for creating a new template
  - Select "existing image" when prompted
  - Enter the following string when prompted for the startup command
  -   '/sbin/init systemd.setenv=INTFTYPE=eth systemd.setenv=ETBA=1 systemd.setenv=SKIP_ZEROTOUCH_BARRIER_IN_SYSDBINIT=1 systemd.setenv=CEOS=1 systemd.setenv=EOS_PLATFORM=ceoslab systemd.setenv=container=docker systemd.setenv=MGMT_INTF=eth0
'
- 

### Arista Switches

All switches that you will be modeling will need to have:

- EAPI services accessible from the host running the ptovnetlab module
```
management api http-commands
   no shutdown
```

- And you will need to provide auth. credentials with sufficient privileges to invoke the following methods:
    - node.enable(("show version", "show lldp neighbors", "show lldp local-info"), format="json")
    - node.startup_config.splitlines()

### Instructions

### Prep

- Have Python and the ptovnetlab package installed on the host that will run ptovnetlab
- Have your login credentials for your production switches handy
- Make sure that your production switches can receive eAPI connections from your GNS3 server
- Optionally, create a file named "input-switch-list"
  -   - Populate 'input-switch-list' with the names of the switches that you want to model in gns3
      -   One switch name per line (no quotes or commas)

### Parameter/argument list

ptovnetlab uses the following arguments (passed as keyword pairs):

- filename *or* switchlist
  - No default value
  - If *both* arguments are provided, ptovnetlab will exit.
  - If *no* argument is provided, ptovnetlab will try use the input function to prompt for switch names
  - "filename" is (path and) name of the file containing the list of switches to process
    - One switch-name (FQDN or resolvable short-name) per line in the file
    - E.G.:  ./switch-list.txt
  - "switchlist" is a python list of switch-names
    - E.g.:  ["name1", "name2", "nameN"]
- servername
  - No default value
  - The name (FQDN or resolvable short-name) of the GNS3 server
  - If not provided, ptovnetlab will try to use the input function to prompt for a value
  - E.g.:  gns3server.whathwere.there
- username
  - No default value
  - The username ptovnetlab will provide to the switches when authenticating the eapi connections
  - If not provided, ptovnetlab will try to use the input function to prompt for a value
- passwd
  - No default value
  - The password ptovnetlab will provide to the switches when authenticating the eapi connections
  - If not provided, ptovnetlab will try to use the input function to prompt for a value
- prjname
  - No default value
  - The name to assign to the new project that ptovnetlab will create on the GNS3 server

### Execution

#### As a Python script

Installing ptovnetlab via pip will save you the effor of installing the additional dependencies list in pyproject.toml, but you can also just grab the contents of the ptovnetlab folder [directly from the git repository](https://github.com/menckend/ptovnetlab/tree/main/ptovnetlab) and store them on the host you'll run them from.

You'll also need to move the "ptovnetlab-cli.py" file *up* one level in the directory structure from the ptovnetlab folder after copying the entire folder to your host.  This is to work around "goofiness" with regards to how Python treats namespaces when accessing Python code as a "script" vs accessing it "as a module."

To actually run the utility, you'll enter the following command:

```
python [path-to]ptovnetlab-cli.py'
```

##### To run interactively

Enter:

```bash
python [path-to]ptovnetlab-cli.py'
```

As ptovnetlab executes, you will be prompted to respond with values for all of the parameters/arguments. No quotes or delimiters should be required as you enter the values.

- The FQDNs of the switches you want to process
  - Type a switch-name and press Enter
  - Repeat until you've entered all the switches you want to model
  - Then press Enter again
- The name of the GNS3 project to create
  - Type a project name (adhere to whatever GNS3's project-naming semantics) and press enter
- The EOS username to use when querying the switches
  - Type the name and press enter
- The EOS password to use when querying the switches
  - The getpass function is used, obscuring the password on-screen as you type it
  - The password itself isn't written to any file
  - Type the password and press Enter
- The FQDN of the GNS3 server you'll be using

##### To run non-interactively

Enter:  

```python
python [path-to]ptovnetlab-cli.py [arguments]
```

The arguments are keyword/value pairs, in the form of:

```python
keyword='value'
```

The arguments can be entered in any order, separate by a space.  Examples of each argument follow:

```text
username= 'mynameismud'
passwd= 'mypasswordisalsomud'
servername= 'gns3server.menckend.com'
switchlist= 'sw1.menckend.com sw2 sw3 sw4.menckend.com'
filename= './switchlist.txt'
prjname= 'ptovnetlab-project-dujour'
```

Remember that the switchlist and filename arguments are mutually exclusive, if you pass *both*, ptovnetlab will exit.

An example of a fully-argumented invocation would be:

```bash
python ./ptovnetlab.py username='fakeid' passwd='b@dp@ssw0rd' servername='gn3server.com' prjname='giveitanme' switchlist='switch1 switch2 switch3'
```

##### As a Python module

Install ptovnetlab with pip as described above and include an import statement ('import ptovnetlab') in your python module. E.g.

```python
from ptovnetlab import ptovnetlab

sn='gns3server.bibbity.bobbity.boo'
un='myuserid'
pw='weakpassword'
prjn='new-gns3-project-today'
sl=['switch1.internal', 'switch15.internal', 'switch1.menckend.com']

ptovnetlab.p_to_v(username=sn, passwd=pw, servername=sn, switchlist=sl, prjname=prjn)
```

> [!IMPORTANT]  
> The 'switchlist' parameter, when ptovnetlab is being accessed as a module is a dict structure, and the formatting in the example above is mandatory when specifying the switchlist data as a kwarg.
