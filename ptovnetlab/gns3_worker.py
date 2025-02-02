"""gns3_worker.py

Creates project and nodes on GNS3 server and then populates their configuration."""

import asyncio
from io import BytesIO
import tarfile
import requests
import aiohttp
import aiodocker
from aiodocker import Docker as docker
from ptovnetlab.data_classes import Switch, Connection

def invoker(servername: str, gns3_url: str, switches: list[Switch],
            prj_id: str, connections: list[Connection]):
    """Add nodes to the new GNS3 project and push a copy of the configuration files
    to their substrate docker containers. Use asyncio/aiohttp to let post requests
    with long completion time run in the background using cooperative multitasking

    Parameters
    ----------
    servername : str
        The name of the aiohttp.ClientSession object used for the connections
    gns3_url : str
        The URL to be posted to the GNS3 server
    switches : list[Switch]
        List of Switch objects to be emulated
    prj_id : str
        The GNS3 project ID
    connections : list[Connection]
        List of connections to make between the GNS3 nodes
    """

    print('')
    print('Creating cEOS nodes in GNS3 project and pushing startup configs to each.')

    # Call asyncio.run against the main_job function
    asyncio.run(main_job(servername, gns3_url, switches, prj_id, connections))


async def main_job(servername: str, gns3_url: str, switches: list[Switch],
                  prj_id: str, connections: list[Connection]):
                
    """Iterate through the list of devices to be modeled and instantiate them in GNS3.

    Parameters
    ----------
    servername : str
        The name of the aiohttp.ClientSession object used for the connections
    gns3_url : str
        The URL to be posted to the GNS3 server
    switches : list[Switch]
        List of Switch objects to be emulated
    prj_id : str
        The GNS3 project ID
    connections : list[Connection]
        List of connections to make between the GNS3 nodes
    """
    print('')
    print('Creating the nodes in the GNS3 project.')
    timeout_seconds = 30
    session_timeout = aiohttp.ClientTimeout(total=None,sock_connect=timeout_seconds,sock_read=timeout_seconds)
    async with aiohttp.ClientSession(timeout=session_timeout) as session:
        # Set x/y coordinates for the first node on the project
        nodex = -825
        nodey = -375

        async with asyncio.TaskGroup() as tg1:
            tasks = []
            for switch in switches:
                # Call the function to make a bunch of API calls to GNS3server for a new node
                task = tg1.create_task(make_a_gns3_node(switch, session, gns3_url, nodex, nodey, prj_id))
                tasks.append(task)
                # Increment x/y coordinates for the *next* switch to be instantiated
                nodex += 150
                if nodex > 400:
                    nodex = -800
                    nodey = nodey + 200
        switches = [await task for task in tasks]
 
        # Create docker client for RESTful API
        
        docker_client = aiodocker.Docker(url=f"http://{servername}:2375")
        try:
            async with asyncio.TaskGroup() as tg2:
                tasks = []
                for switch in switches:
                    # Call the function that starts the new nodes' containers and
                    # pushes the configuration to them before stopping them
                    task = tg2.create_task(docker_api_stuff(switch, docker_client))
                    tasks.append(task)
                # Wait for all tasks to complete
                results = [await task for task in tasks]
                print("Configuration copying completed for all switches")
        finally:
            await docker_client.close()

        async with asyncio.TaskGroup() as tg3:
            for connection in connections:
                a_node_id = None
                b_node_id = None
                for switch in switches:
                    if connection.switch_a == switch.lldp_system_name:
                        a_node_id = switch.gns3_node_id
                    if connection.switch_b == switch.lldp_system_name:
                        b_node_id = switch.gns3_node_id
                
                if a_node_id and b_node_id:
                    try:
                        # More robust port parsing with error handling
                        def parse_port(port):
                            if not port.lower().startswith('ethernet'):
                                raise ValueError(f"Invalid port format: {port}. Port must start with 'ethernet'")
                            # Extract numbers after 'ethernet', before any '/'
                            port_base = port.lower().split('/')[0]
                            adapter_num = ''.join(filter(str.isdigit, port_base))
                            if not adapter_num:
                                raise ValueError(f"Could not extract adapter number from port: {port}")
                            return adapter_num

                        a_node_adapter_nbr = parse_port(connection.port_a)
                        b_node_adapter_nbr = parse_port(connection.port_b)

                        make_link_url = gns3_url + 'projects/' + prj_id + '/links'
                    except (IndexError, ValueError) as e:
                        print(f"Error parsing ports for connection {connection.switch_a}:{connection.port_a} -> {connection.switch_b}:{connection.port_b}")
                        print(f"Error details: {str(e)}")
                        continue
                    make_link_json = {'nodes': [{'adapter_number': int(a_node_adapter_nbr),
                                            'node_id': a_node_id, 'port_number': 0},
                                            {'adapter_number': int(b_node_adapter_nbr),
                                            'node_id': b_node_id, 'port_number': 0}]}

                    tg3.create_task(gns3_post(session, str(make_link_url), 'post', jsondata=make_link_json))
        return "Virtual Network Lab is ready to run."


async def docker_api_stuff(switch: Switch, docker_client):
    """
    Convert config from list of strings to a string and copy it to the container

    Parameters
    ----------
    switch : Switch
        The Switch object containing the configuration and container details
    docker_client : docker.DockerClient
        The Docker client object used to interact with the Docker API.
    """
    print(f"Processing configuration for switch {switch.name}")
    
    try:
        # Turn a list of strings into a single string with newlines
        config_string = '\n'.join(switch.initial_config)
        if not config_string:
            print(f"Warning: Empty configuration for switch {switch.name}")
            return 'skipped - empty config'
            
        # Apply ASCII encoding to the config string
        ascii_to_go = config_string.encode('ascii')
        # Turn the ASCII-encoded string into a bytes-like object
        bytes_to_go = BytesIO(ascii_to_go)
        fh = BytesIO()
        
        # Create tar archive with correct size
        with tarfile.open(fileobj=fh, mode='w') as tarch:
            info = tarfile.TarInfo('startup-config')
            info.size = len(ascii_to_go)  # Use actual config size
            tarch.addfile(info, bytes_to_go)
            
        # Retrieve our tar archive
        buffer_to_send = fh.getbuffer()

        # Access the container by its ID
        container = await docker_client.containers.get(switch.docker_container_id)
        print(f"Copying configuration to container {switch.docker_container_id}")
        
        # Copy config and execute commands
        await container.put_archive('/', data=buffer_to_send)
        await container.start()
#        print(dir(container))
        exec_config = await container.exec(cmd=['mv', '/startup-config', '/mnt/flash/'])
        exec_stream = await exec_config.start(detach=False)
        output = await exec_stream.read_out()
        if output.exit_code != 0:
            print(f"Warning: mv command failed for {switch.name}")
        await container.stop()  # Call stop() as a method
        
        print(f"Successfully configured switch {switch.name}")
        return 'success'
        
    except aiodocker.exceptions.DockerError as e:
        print(f"Docker error for switch {switch.name}: {e}")
        return f'failed - {str(e)}'
    except Exception as e:
        print(f"Unexpected error for switch {switch.name}: {e}")
        return f'failed - {str(e)}'

async def fetch_json_data_post(session, url, json_in):
    async with session.post(url, json=json_in) as response:
        json_data = await response.json()
        return json_data

async def fetch_json_data_get (session, url, json_in):
    async with session.get(url, json=json_in) as response:
        json_data = await response.json()
        return json_data


async def make_a_gns3_node(switch: Switch, session: str, gns3_url: str, nodex: int, nodey: int, prj_id: str, **kwargs) -> Switch:

    # Duplicate the existing GNS3 docker template for the device being modeled
    json_data = await fetch_json_data_post(session, gns3_url +'templates/' + switch.gns3_template_id + '/duplicate', json_in=[])
    tmp_template_id = json_data['template_id']

    # Change the interface count on the temporary template
    async with session.put(gns3_url +'projects/' + prj_id + '/templates/' + tmp_template_id, json={'adapters': switch.ethernet_interfaces + 1}) as response:
        await response.text()

    # Create the new GNS3 node and capture its GNS3 UID.
    json_data = await fetch_json_data_post(session, gns3_url +'projects/' + prj_id + '/templates/' + tmp_template_id, json_in={'x': nodex, 'y': nodey})
    switch.gns3_node_id = json_data['node_id']
    
    # Delete the temporary GNS3 template
    async with session.delete(gns3_url +'templates/' + tmp_template_id) as response:
        await response.text()

    # Change the name of the newly created node to match the device being modeled
    async with session.put(gns3_url +'projects/' + prj_id + '/nodes/' + switch.gns3_node_id, json={'name': switch.name}) as response:
        await response.text()

    # Grab the new node's Docker container's UID
    json_data = await fetch_json_data_get(session, gns3_url +'projects/' + prj_id + '/nodes/' + switch.gns3_node_id, json_in=[])
    switch.docker_container_id = json_data['properties']['container_id']

    return switch

async def gns3_post(session: str, url: str, method: str, **kwargs) -> str:
    """Send an async POST request to GNS3 server.

    Parameters
    ----------
    session : str
        The name of the aiohttp.ClientSession object used for the connections
    url : str
        The URL to be posted to the GNS3 server (includes project ID and node ID)
    method : str
        The HTTP method (get, put, post) to be used in the request
    jsondata : str
        Optional.  Any JSON to be included with the HTTP request
    """

    if 'jsondata' in kwargs:
        jsondata = kwargs['jsondata']
    if method == 'post':
        if 'jsondata' in kwargs:
            async with session.post(url, json=jsondata) as response:
                await asyncio.sleep(.2)
        else:
            async with session.post(url) as response:
                await asyncio.sleep(.2)
    if method == 'get':
        if 'jsondata' in kwargs:
            async with session.get(url, json=jsondata) as response:
                await asyncio.sleep(.2)
        else:
            async with session.get(url) as response:
                await asyncio.sleep(.2)
    if method == 'put':
        if jsondata:
            async with session.put(url, json=kwargs['jsondata']) as response:
                await asyncio.sleep(.2)
        else:
            async with session.put(url) as response:
                await asyncio.sleep(.2)
