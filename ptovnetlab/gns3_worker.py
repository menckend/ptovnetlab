"""gns3_worker.py

Creates project and nodes on GNS3 server and then populates their configuration."""

import asyncio
from io import BytesIO
import tarfile
import requests
import aiohttp
import docker


def invoker(servername_in: str, gns3_url_in: str, sw_vals_in: list,
            allconf_in: list, prj_id_in: str, connx_in: list):
    """Add nodes to the new GNS3 project and push a copy of the configuration files
    to their substrate docker containers.  Use asyncio/aoihttp to let post requests
    with long completion time run in the background usign cooperative multitasking

    Parameters
    ----------
    servername_in : str
        The name of the aiohttp.ClientSession object used for the connections
    gns3_url_in : str
        The URL to be posted to the GNS3 server
    sw_vals_in : list
        List of needed data about the switches to be emulated
    allconf_in : list
        List-of-lists holding all of the switch's configurations
    connx_in : list
        List of connections we need to make between the GNS3 nodes we're creating
    """

    # Manage an event loop for all of the work done by gns3_node_create_async
    print('')
    print('Creating cEOS nodes in GNS3 project and pushing startup configs to each.')
    sw_vals_new = asyncio.run(gns3_nodes_create_async
                              (servername_in, gns3_url_in, sw_vals_in, allconf_in,
                               prj_id_in))
    # Only AFTER gns3_node_create_async is done, do we start populating connections
    lastwords = asyncio.run(gns3_connx_create_async(servername_in, gns3_url_in,
                                                    sw_vals_new, connx_in, prj_id_in))
    return lastwords


async def gns3_nodes_create_async(servername_in: str, gns3_url_in: str, sw_vals_in:
                                  list, allconf_in: list, prj_id_in: str):
    """Add nodes to the new GNS3 project and push a copy of the configuration files
    to their substrate docker containers.

    Parameters
    ----------
    servername_in : str
        The name of the aiohttp.ClientSession object used for the connections
    gns3_url_in : str
        The URL to be posted to the GNS3 server
    sw_vals_in : list
        List of needed data about the switches to be emulated
    all_conf_in : list
        List-of-lists holding all of the switch's configurations
    """
    print('')
    print('Creating the nodes in the GNS3 project.')
    async with aiohttp.ClientSession() as session:
        # Set x/y coordinates for the first node on the project
        nodex = -825
        nodey = -375
        # Create docker client for RESTful API
        d_clnt = docker.DockerClient(base_url='tcp://'+servername_in+':2375')
        # Initialize single string version of switch config from allconf_in
        my_string_to_go = ''
        tasks = []

        # Loop through the switches and create them in the GNS3 project
        for sw_val_ctr, sw_val in enumerate(sw_vals_in):
            looped_template_id = requests.post(gns3_url_in + 'templates/' + sw_val[7] + '/duplicate', timeout=30).json()['template_id']
            # Put request to change the # of interfaces of the temporary template
            requests.put(gns3_url_in + 'templates/' + looped_template_id,
                         json={'adapters': int(sw_val[6])+1}, timeout=30)
            # Request to instantiate a new node using the temporary template
            newnodeoutput = requests.post(gns3_url_in + 'projects/' + prj_id_in +
                                          '/templates/' + looped_template_id, timeout=30,
                                          json={'x': nodex, 'y': nodey})
            # Capture the GNS3 node_id of the virtual-switch we just created
            sw_val[8] = newnodeoutput.json()['node_id']
            # Request to delete the temporary template
            requests.delete(gns3_url_in + 'templates/' + looped_template_id, timeout=30)

            # Change the name of the GNS3 node that we just created
            requests.put(gns3_url_in + 'projects/' + prj_id_in + '/nodes/' + sw_val[8],
                         timeout=30, json={'name': sw_val[0]})
            # Capture the docker_id of the virtual-switch we just created  (container
            # re-spawned when we changed its name)
            sw_val[9] = requests.get(gns3_url_in + 'projects/' + prj_id_in + '/nodes/' +
                                     sw_val[8], timeout=30).json()['properties']['container_id']
            # Copy the modified sw_val objects contents back into sw_vals_in[sw_val_ctr]
            sw_vals_in[sw_val_ctr] = sw_val
            # Increment x/y coordinates for the *next* switch to be instantiated
            nodex += 150
            if nodex > 400:
                nodex = -800
                nodey = nodey + 200
            # Tell GNS3 to start the node that represents the current switch
            requests.post(gns3_url_in + 'projects/' + prj_id_in + '/nodes/' + sw_val[8]
                          + '/start', timeout=30)
            # Rebuild the switch-config from its current state as a list of individual
            # lines to a single string with newline characters.
            my_string_to_go = ''
            for i in allconf_in[sw_val_ctr]:
                my_string_to_go = my_string_to_go + i + "\n"
            
            # Call the new function to handle the transformation and file upload
            upload_config_to_container(d_clnt, sw_val[9], my_string_to_go)

            # Set URL for request to GNS3 to stop the node
            url = gns3_url_in+"projects/" + prj_id_in+"/nodes/" + sw_val[8] + "/stop"
            # Assign the HTTP post request for async execution
            tasks.append(asyncio.ensure_future(gns3_post(session, url, 'post')))
            await asyncio.sleep(0.2)
        await asyncio.gather(*tasks)
        switch_vals_out = sw_vals_in
        return switch_vals_out


def upload_config_to_container(docker_client, container_id, config_string):
    """
    Transform the configuration string into a tar archive and upload it to the container.

    Parameters
    ----------
    docker_client : docker.DockerClient
        The Docker client object used to interact with the Docker API.
    container_id : str
        The ID of the container to which the configuration will be uploaded.
    config_string : str
        The configuration string to be uploaded.
    """
    # Apply ASCII encoding to the config string
    ascii_to_go = config_string.encode('ascii')
    # Turn the ASCII-encoded string into a bytes-like object
    bytes_to_go = BytesIO(ascii_to_go)
    # Turn the switch-config string into a tar archive for later
    fh = BytesIO()
    with tarfile.open(fileobj=fh, mode='w') as tarch:
        info = tarfile.TarInfo('startup-config')
        info.size = len(config_string)
        tarch.addfile(info, bytes_to_go)
    # Get a docker API connection for the current switch's container
    cont1 = docker_client.containers.get(container_id)
    # Retrieve our tar archive from the file-like object ('fh') that we stored it in
    uggo = fh.getbuffer()
    # Put the startup-config onto / on the virtual-switch
    cont1.put_archive('/', data=uggo)
    # Move the startup-config from / to /mnt/flash on the virtual switch
    cont1.exec_run('mv /startup-config /mnt/flash/')

async def gns3_connx_create_async(servername_in: str, gns3_url_in: str, sw_vals_new:
                                  list, connx_in: list, prj_id_in: str):
    """Add nodes to the new GNS3 project and push a copy of the configuration files
    to their substrate docker containers.

    Parameters
    ----------
    servername_in : str
        The name of the aiohttp.ClientSession object used for the connections
    gns3_url_in : str
        The URL to be posted to the GNS3 server
    sw_vals_new : list
        List of needed data about the switches to be emulated.  GNS3 node IDs were
        added by the gns3_nodes_create function.
    connx_in : list
        List of node-to-node connections that we want to create in the GNS3 project
    """

    async with aiohttp.ClientSession() as sesh2:
        # Loop through connections_to_make and make the connections between switches
        print("Instantiating the connections between switches in the GNS3 \
              project (might take a minute):")
        cnx_urls = []
        cnx_json = []
        for n, val in enumerate(connx_in):
            a_node_id = []
            b_node_id = []
            for m, val2 in enumerate(sw_vals_new):
                if val[0] == val2[5]:
                    a_node_id = val2[8]
                if val[2] == val2[5]:
                    b_node_id = val2[8]
            a_node_adapter_nbr = str(val[1].split('/')[0].split('ethernet')[1])
            b_node_adapter_nbr = str(val[3].split('/')[0].split('ethernet')[1])
            # Make a list of URLs for the requests to create all the links
            cnx_urls.append('')
            cnx_urls[n] = gns3_url_in + 'projects/' + prj_id_in + '/links'
            cnx_json.append({})
            cnx_json[n] = {'nodes': [{'adapter_number': int(a_node_adapter_nbr),
                                      'node_id': a_node_id, 'port_number': 0},
                                     {'adapter_number': int(b_node_adapter_nbr),
                                      'node_id': b_node_id, 'port_number': 0}]}
        # Assign the HTTP post request for async execution
        tasks_2 = []
        for n, url in enumerate(cnx_urls):
            tasks_2.append(asyncio.ensure_future(gns3_post(sesh2, str(url),
                           'post', jsondata=cnx_json[n])))
            await asyncio.sleep(0.2)
        responses = await asyncio.gather(*tasks_2)
        return responses


async def gns3_post(session_in: str, url_in: str, method: str, **kwargs) -> str:
    """Send an async POST request to GNS3 server.

    Parameters
    ----------
    session_in : str
        The name of the aiohttp.ClientSession object used for the connections
    url_in : str
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
            async with session_in.post(url_in, json=jsondata) as response:
                await asyncio.sleep(.2)
        else:
            async with session_in.post(url_in) as response:
                await asyncio.sleep(.2)
    if method == 'get':
        if 'jsondata' in kwargs:
            async with session_in.get(url_in, json=jsondata) as response:
                await asyncio.sleep(.2)
        else:
            async with session_in.get(url_in) as response:
                await asyncio.sleep(.2)
    if method == 'put':
        if jsondata:
            async with session_in.put(url_in, json=kwargs['jsondata']) as response:
                await asyncio.sleep(.2)
        else:
            async with session_in.put(url_in) as response:
                await asyncio.sleep(.2)
    return response
