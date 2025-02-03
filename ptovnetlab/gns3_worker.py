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
                    task = tg2.create_task(docker_api_stuff(switch, docker_client, servername))
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




async def docker_api_stuff(switch: Switch, docker_client, servername: str = 'localhost'):
    """
    Convert config from list of strings to a string and copy it to the container

    Parameters
    ----------
    switch : Switch
        The Switch object containing the configuration and container details
    docker_client : docker.DockerClient
        The Docker client object used to interact with the Docker API.
    servername : str
        The hostname or IP address of the Docker daemon (default: 'localhost')
    """
    
    try:
        print(f"\n=== Starting configuration for switch {switch.name} ===")
        # Create a session for direct Docker API calls
        async with aiohttp.ClientSession(base_url=f"http://{servername}:2375") as session:
            # Get Docker version info
            async with session.get("/version") as response:
                if response.status == 200:
                    version_info = await response.json()
                    print(f"Docker API Version: {version_info.get('Version', 'unknown')}")
                else:
                    print(f"Warning: Could not get Docker version. Status: {response.status}")
            # Turn a list of strings into a single string with newlines
            config_string = '\n'.join(switch.initial_config)
            if not config_string:
                print(f"Warning: Empty configuration for switch {switch.name}")
                return 'skipped - empty config'

            print(f"\n=== Preparing configuration archive ===")
            # Apply ASCII encoding to the config string
            ascii_to_go = config_string.encode('ascii')
            print(f"Configuration size: {len(ascii_to_go)} bytes")
            
            # Create tar archive
            fh = BytesIO()
            with tarfile.open(fileobj=fh, mode='w') as tarch:
                info = tarfile.TarInfo('startup-config')
                info.size = len(ascii_to_go)
                bytes_to_go = BytesIO(ascii_to_go)
                tarch.addfile(info, bytes_to_go)
            
            # Get archive content
            fh.seek(0)
            archive_content = fh.read()
            print(f"Tar archive size: {len(archive_content)} bytes")

            print(f"\n=== Accessing container {switch.docker_container_id} ===")
            # Get container info
            async with session.get(f"/containers/{switch.docker_container_id}/json") as response:
                if response.status == 200:
                    container_info = await response.json()
                    print(f"Container state: {container_info['State']}")
                else:
                    print(f"Warning: Could not get container info. Status: {response.status}")
                    return 'failed - container info error'

            print(f"\n=== Starting container ===")
            # Start container
            async with session.post(f"/containers/{switch.docker_container_id}/start") as response:
                if response.status not in [204, 304]:
                    print(f"Warning: Could not start container. Status: {response.status}")
                    return 'failed - container start error'

            print("Waiting for container to become ready...")
            # Wait for container to be ready
            start_time = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start_time < 20:
                async with session.get(f"/containers/{switch.docker_container_id}/json") as response:
                    if response.status == 200:
                        container_info = await response.json()
                        if container_info['State']['Running']:
                            break
                await asyncio.sleep(1)
            else:
                print(f"Warning: Container {switch.docker_container_id} did not become ready")
                return 'failed - container not ready'

            print(f"\n=== Verifying container filesystem ===")
            # Create exec instance for ls command
            async with session.post(
                f"/containers/{switch.docker_container_id}/exec",
                json={
                    "AttachStdout": True,
                    "AttachStderr": True,
                    "Cmd": ["ls", "-la", "/"]
                }
            ) as response:
                if response.status == 201:
                    exec_data = await response.json()
                    exec_id = exec_data['Id']
                    
                    # Start exec instance with raw stream handling
                    headers = {'Content-Type': 'application/json'}
                    async with session.post(
                        f"/exec/{exec_id}/start",
                        json={"Detach": False, "Tty": False},
                        headers=headers
                    ) as exec_response:
                        if exec_response.status == 200:
                            # Read the response as chunks
                            chunks = []
                            async for chunk in exec_response.content.iter_any():
                                print(f"Debug - Chunk length: {len(chunk)}")
                                if len(chunk) > 0:
                                    # First byte is stream type (stdout/stderr)
                                    stream_type = chunk[0]
                                    # Next 3 bytes are the size
                                    size = int.from_bytes(chunk[1:4], byteorder='big')
                                    # Rest is the actual content
                                    content = chunk[4:4+size]
                                    chunks.append(content)
                            
                            # Combine all chunks
                            output = b''.join(chunks)
                            print(f"Debug - Combined output length: {len(output)}")
                            try:
                                decoded = output.decode('utf-8')
                            except UnicodeDecodeError as e:
                                print(f"Debug - UTF-8 decode error: {e}")
                                decoded = output.decode('latin-1')
                            print(f"Initial filesystem state:\n{decoded}")
                        else:
                            print(f"Warning: Exec start failed. Status: {exec_response.status}")

            print(f"\n=== Copying configuration to container ===")
            # Copy archive to container
            headers = {'Content-Type': 'application/x-tar'}
            async with session.put(
                f"/containers/{switch.docker_container_id}/archive",
                params={'path': '/'},
                headers=headers,
                data=archive_content
            ) as response:
                if response.status == 200:
                    print("Configuration file copied successfully")
                else:
                    print(f"Error copying configuration. Status: {response.status}")
                    return 'failed - file copy error'

            print(f"\n=== Moving configuration file ===")
            # Create exec instance for mv command
            async with session.post(
                f"/containers/{switch.docker_container_id}/exec",
                json={
                    "AttachStdout": True,
                    "AttachStderr": True,
                    "Cmd": ["mv", "/startup-config", "/mnt/flash/"]
                }
            ) as response:
                if response.status == 201:
                    exec_data = await response.json()
                    exec_id = exec_data['Id']
                    
                    # Start exec instance with raw stream handling
                    headers = {'Content-Type': 'application/json'}
                    async with session.post(
                        f"/exec/{exec_id}/start",
                        json={"Detach": False, "Tty": False},
                        headers=headers
                    ) as exec_response:
                        if exec_response.status == 200:
                            # Read the response as chunks
                            chunks = []
                            async for chunk in exec_response.content.iter_any():
                                print(f"Debug - Chunk length: {len(chunk)}")
                                if len(chunk) > 0:
                                    # First byte is stream type (stdout/stderr)
                                    stream_type = chunk[0]
                                    # Next 3 bytes are the size
                                    size = int.from_bytes(chunk[1:4], byteorder='big')
                                    # Rest is the actual content
                                    content = chunk[4:4+size]
                                    chunks.append(content)
                            
                            # Combine all chunks
                            output = b''.join(chunks)
                            print(f"Debug - Combined output length: {len(output)}")
                            try:
                                decoded = output.decode('utf-8')
                            except UnicodeDecodeError as e:
                                print(f"Debug - UTF-8 decode error: {e}")
                                decoded = output.decode('latin-1')
                            print(f"mv command output:\n{decoded if decoded.strip() else 'No output'}")
                            
                            # Check exec result
                            async with session.get(f"/exec/{exec_id}/json") as inspect_response:
                                if inspect_response.status == 200:
                                    inspect_data = await inspect_response.json()
                                    if inspect_data.get('ExitCode', 1) != 0:
                                        print(f"Warning: mv command failed")
                                        return 'failed - mv command error'
                                else:
                                    print(f"Warning: Could not get exec result. Status: {inspect_response.status}")
                        else:
                            print(f"Warning: mv command exec failed. Status: {exec_response.status}")
                            return 'failed - mv command error'

            print(f"\n=== Stopping container ===")
            # Stop container
            async with session.post(f"/containers/{switch.docker_container_id}/stop") as response:
                if response.status not in [204, 304]:
                    print(f"Warning: Could not stop container. Status: {response.status}")
                    return 'failed - container stop error'
        
            print(f"\n=== Successfully configured switch {switch.name} ===")
            return 'success'
        
    except Exception as e:
        print(f"Error configuring switch {switch.name}: {e}")
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
    async with session.put(gns3_url + 'templates/' + tmp_template_id, json={'adapters': switch.ethernet_interfaces + 1}) as response:
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

async def wait_for_container_ready(container, timeout=20):
    """
    Wait for the container to become ready by running a test command.

    Parameters
    ----------
    container : aiodocker.Container
        The container object to monitor.
    timeout : int
        Maximum time to wait (in seconds) for the container to become ready.

    Returns
    -------
    bool
        True if the container is ready, False otherwise.
    """
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        try:
            running_test = await container.show()
            exec_result = running_test['State']['Running']
            if exec_result == True:
                return True
        except Exception as e:
            print(f"Error checking container readiness: {e}")
        await asyncio.sleep(1)  # Wait for 1 seconds before checking again
    return False
