"""gns3_worker.py

Creates project and nodes on GNS3 server and then populates their configuration."""

import asyncio
from io import BytesIO
import tarfile
import requests
import aiohttp
#import docker
import aiodocker
from aiodocker import Docker as docker

def invoker(servername: str, gns3_url: str, sw_vals: list,
            allconf: list, prj_id: str, connx_list: list):
    """Add nodes to the new GNS3 project and push a copy of the configuration files
    to their substrate docker containers.  Use asyncio/aoihttp to let post requests
    with long completion time run in the background usign cooperative multitasking

    Parameters
    ----------
    servername : str
        The name of the aiohttp.ClientSession object used for the connections
    gns3_url : str
        The URL to be posted to the GNS3 server
    sw_vals : list
        List of needed data about the switches to be emulated
    allconf : list
        List-of-lists holding all of the switch's configurations
    connx : list
        List of connections we need to make between the GNS3 nodes we're creating
    """

    
    print('')
    print('Creating cEOS nodes in GNS3 project and pushing startup configs to each.')


    # Call asyncio.run against the main_job function
    sw_vals_new = asyncio.run(main_job(
                              servername, gns3_url, sw_vals, allconf, 
                              prj_id, connx_list))
    return


async def main_job(servername: str, gns3_url: str, sw_vals: list,
            allconf: list, prj_id: str, connx_list: list):
                
    """Iterate through the list of devices to be modeled and instantiate them in GNS3.

    Parameters
    ----------
    servername : str
        The name of the aiohttp.ClientSession object used for the connections
    gns3_url : str
        The URL to be posted to the GNS3 server
    sw_vals : list
        List of needed data about the switches to be emulated
    all_conf : list
        List-of-lists holding all of the switch's configurations
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
            tasks=[]
            for sw_val_ctr, sw_val in enumerate(sw_vals):
                # Call the function to make a bunch of API calls to GNS3server for a new node
                task = tg1.create_task(make_a_gns3_node(sw_val, session, gns3_url, nodex, nodey, prj_id))
                tasks.append(task)
                # Increment x/y coordinates for the *next* switch to be instantiated
                nodex += 150
                if nodex > 400:
                    nodex = -800
                    nodey = nodey + 200
        sw_vals = [await task for task in tasks]
 
        async with asyncio.TaskGroup() as tg2:
            # Create docker client for RESTful API
            docker = aiodocker.Docker(url="http://"+servername+"2375")
            for sw_val_ctr, sw_val in enumerate(sw_vals):
                # Call the function that starts the new nodes' containers and
                # pushes the configuration to them before stopping them
                tg2.create_task(docker_api_stuff(sw_val, allconf[sw_val_ctr], docker))
            docker.close

        async with asyncio.TaskGroup() as tg3:
            cnx_urls = []
            cnx_json = []
            for n, val in enumerate(connx_list):
                a_node_id = []
                b_node_id = []
                for m, val2 in enumerate(sw_vals):
                    if val[0] == val2[5]:
                        a_node_id = val2[8]
                    if val[2] == val2[5]:
                        b_node_id = val2[8]
                a_node_adapter_nbr = str(val[1].split('/')[0].split('ethernet')[1])
                b_node_adapter_nbr = str(val[3].split('/')[0].split('ethernet')[1])

                make_link_url = gns3_url + 'projects/' + prj_id + '/links'
                make_link_json = {'nodes': [{'adapter_number': int(a_node_adapter_nbr),
                                        'node_id': a_node_id, 'port_number': 0},
                                        {'adapter_number': int(b_node_adapter_nbr),
                                        'node_id': b_node_id, 'port_number': 0}]}

                tg3.create_task(gns3_post(session, str(make_link_url), 'post', jsondata=make_link_json))
        return "Virtual Network Lab is ready to run."


async def docker_api_stuff(swval, config, docker_conn):
    """
    Convert config from list of strings to a string and copy it to the container

    Parameters
    ----------
    swval : list
        The list of device properties for the virtual node we're creating
    config : list
        The configuration list to be uploaded to the container
    docker_client : docker.DockerClient
        The Docker client object used to interact with the Docker API.
    """
    # Turn a list of strings into a buffer-like-object containing a tar archive
    my_string_to_go = ''
    for i in config:
        my_string_to_go = my_string_to_go + i + "\n"
    # Apply ASCII encoding to the config string
    ascii_to_go = my_string_to_go.encode('ascii')
    # Turn the ASCII-encoded string into a bytes-like object
    bytes_to_go = BytesIO(ascii_to_go)
    fh = BytesIO()
    with tarfile.open(fileobj=fh, mode='w') as tarch:
        info = tarfile.TarInfo('startup-config')
        info.size = len(config)
        tarch.addfile(info, bytes_to_go)
    # Retrieve our tar archive from the file-like object ('fh') that we stored it in
    buffer_to_send = fh.getbuffer()

    try:
        # Access the container by its ID
        container = await docker.containers.get(swval[9])
        await container.put_archive('/', data=buffer_to_send)
        await container.start()
        await container.exec_run('mv /startup-config /mnt/flash/')
        await container.stop
    except aiodocker.exceptions.DockerError as e:
            print(f"Docker error: {e}")
    finally:
        return 'done'

async def fetch_json_data_post(session, url, json_in):
    async with session.post(url, json=json_in) as response:
        json_data = await response.json()
        return json_data

async def fetch_json_data_get (session, url, json_in):
    async with session.get(url, json=json_in) as response:
        json_data = await response.json()
        return json_data


async def make_a_gns3_node(sw_val: list, session: str, gns3_url: str, nodex, nodey, prj_id, **kwargs) -> str:

    # Duplicate the existing GNS3 docker template for the device being modeled
    json_data = await fetch_json_data_post(session, gns3_url +'templates/' + sw_val[7] + '/duplicate', json_in=[])
    tmp_template_id = json_data['template_id']

    # Change the interface count on the temporary template
    async with session.put(gns3_url +'projects/' + prj_id + '/templates/' + tmp_template_id, json={'adapters': int(sw_val[6])+1}) as response:
        await response.text()

    # Create the new GNS3 node and capture its GNS3 UID.
    json_data = await fetch_json_data_post(session, gns3_url +'projects/' + prj_id + '/templates/' + tmp_template_id, json_in={'x': nodex, 'y': nodey})
    sw_val[8] = json_data['node_id']
    
    # Delete the temporary GNS3 template
    async with session.delete(gns3_url +'templates/' + tmp_template_id) as response:
        await response.text()

    # Change the name of the newly created node to match the device being modeled
    async with session.put(gns3_url +'projects/' + prj_id + '/nodes/' + sw_val[8], json={'name':sw_val[0]}) as response:
        await response.text()

    # Grab the new node's Docker container's UID
    json_data = await fetch_json_data_get(session, gns3_url +'projects/' + prj_id + '/nodes/' + sw_val[8], json_in=[])
    sw_val[9] = json_data['properties']['container_id']

    return sw_val

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
    return response
