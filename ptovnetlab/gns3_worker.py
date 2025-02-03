#!/usr/bin/env python3
"""
GNS3 Network Topology Management Module

This module provides asynchronous functionality for creating and 
configuring network topologies in GNS3, including device creation, 
configuration, and network infrastructure setup.
"""

import asyncio
import logging
from io import BytesIO
import tarfile
from typing import List, Dict, Optional, Union, Tuple, Any

import aiohttp
import aiodocker
from aiodocker import Docker as docker

from ptovnetlab.data_classes import Switch, Connection

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class GNS3WorkerError(Exception):
    """Custom exception for GNS3 Worker related errors."""
    pass

class ContainerConfigurationError(GNS3WorkerError):
    """Exception raised for errors during container configuration."""
    pass

def invoker(
    servername: str, 
    gns3_url: str, 
    switches: List[Switch],
    prj_id: str, 
    connections: List[Connection]
) -> str:
    """
    Synchronous entry point for creating GNS3 project nodes and connections.

    This function provides a synchronous wrapper for the asynchronous main_job,
    allowing the module to be called from synchronous code.

    Args:
        servername (str): The name of the aiohttp.ClientSession object
        gns3_url (str): The URL for the GNS3 server
        switches (List[Switch]): List of Switch objects to be emulated
        prj_id (str): The GNS3 project ID
        connections (List[Connection]): List of connections to make between nodes

    Returns:
        str: Status message indicating completion or error

    Raises:
        GNS3WorkerError: If there are issues during the GNS3 project setup
    """
    try:
        logger.info('Initiating GNS3 project node and connection creation')
        result = asyncio.run(
            main_job(servername, gns3_url, switches, prj_id, connections)
        )
        logger.info('GNS3 project setup completed successfully')
        return result
    except Exception as e:
        logger.error(f'GNS3 project setup failed: {e}')
        raise GNS3WorkerError(f'Project setup failed: {e}')

async def main_job(
    servername: str, 
    gns3_url: str, 
    switches: List[Switch],
    prj_id: str, 
    connections: List[Connection]
) -> str:
    """
    Asynchronously create GNS3 nodes, configure containers, and establish connections.

    Args:
        servername (str): The name of the aiohttp.ClientSession object
        gns3_url (str): The URL for the GNS3 server
        switches (List[Switch]): List of Switch objects to be emulated
        prj_id (str): The GNS3 project ID
        connections (List[Connection]): List of connections to make between nodes

    Returns:
        str: Status message indicating completion
    """
    logger.info('Creating nodes in the GNS3 project.')
    
    # Configure session timeout
    timeout_seconds = 30
    session_timeout = aiohttp.ClientTimeout(
        total=None, 
        sock_connect=timeout_seconds, 
        sock_read=timeout_seconds
    )

    async with aiohttp.ClientSession(timeout=session_timeout) as session:
        # Position nodes in the project
        nodex, nodey = -825, -375

        # Create nodes
        async with asyncio.TaskGroup() as tg1:
            node_tasks = []
            for switch in switches:
                task = tg1.create_task(
                    make_a_gns3_node(
                        switch, session, gns3_url, nodex, nodey, prj_id
                    )
                )
                node_tasks.append(task)
                
                # Update node positioning
                nodex += 150
                if nodex > 400:
                    nodex = -800
                    nodey += 200

        # Update switches with node information
        switches = [await task for task in node_tasks]

        # Configure Docker containers
        docker_client = aiodocker.Docker(url=f"http://{servername}:2375")
        try:
            async with asyncio.TaskGroup() as tg2:
                config_tasks = []
                for switch in switches:
                    task = tg2.create_task(
                        docker_api_config(switch, docker_client, servername)
                    )
                    config_tasks.append(task)
                
                # Wait for configuration tasks
                results = [await task for task in config_tasks]
                logger.info("Configuration copying completed for all switches")
        finally:
            await docker_client.close()

        # Establish connections between nodes
        async with asyncio.TaskGroup() as tg3:
            for connection in connections:
                a_node_id, b_node_id = _find_connection_nodes(switches, connection)
                
                if a_node_id and b_node_id:
                    try:
                        a_adapter = _parse_port(connection.port_a)
                        b_adapter = _parse_port(connection.port_b)

                        make_link_url = f"{gns3_url}projects/{prj_id}/links"
                        make_link_json = {
                            'nodes': [
                                {
                                    'adapter_number': int(a_adapter),
                                    'node_id': a_node_id, 
                                    'port_number': 0
                                },
                                {
                                    'adapter_number': int(b_adapter),
                                    'node_id': b_node_id, 
                                    'port_number': 0
                                }
                            ]
                        }

                        tg3.create_task(
                            gns3_post(
                                session, 
                                str(make_link_url), 
                                'post', 
                                jsondata=make_link_json
                            )
                        )
                    except ValueError as e:
                        logger.error(
                            f"Error parsing ports for connection "
                            f"{connection.switch_a}:{connection.port_a} -> "
                            f"{connection.switch_b}:{connection.port_b}: {e}"
                        )

        return "Virtual Network Lab is ready to run."

def _find_connection_nodes(
    switches: List[Switch], 
    connection: Connection
) -> Tuple[Optional[str], Optional[str]]:
    """
    Find node IDs for a given connection.

    Args:
        switches (List[Switch]): List of switches
        connection (Connection): Connection to find nodes for

    Returns:
        Tuple[Optional[str], Optional[str]]: Node IDs for switches A and B
    """
    a_node_id = next(
        (switch.gns3_node_id for switch in switches 
         if switch.lldp_system_name == connection.switch_a), 
        None
    )
    b_node_id = next(
        (switch.gns3_node_id for switch in switches 
         if switch.lldp_system_name == connection.switch_b), 
        None
    )
    return a_node_id, b_node_id

def _parse_port(port: str) -> str:
    """
    Parse port string to extract adapter number.

    Args:
        port (str): Port string (e.g., 'Ethernet1/1')

    Returns:
        str: Extracted adapter number

    Raises:
        ValueError: If port format is invalid
    """
    if not port.lower().startswith('ethernet'):
        raise ValueError(f"Invalid port format: {port}. Port must start with 'ethernet'")
    
    # Extract numbers after 'ethernet', before any '/'
    port_base = port.lower().split('/')[0]
    adapter_num = ''.join(filter(str.isdigit, port_base))
    
    if not adapter_num:
        raise ValueError(f"Could not extract adapter number from port: {port}")
    
    return adapter_num

async def docker_api_config(
    switch: Switch, 
    docker_client: aiodocker.Docker, 
    servername: str = 'localhost'
) -> str:
    """
    Configure Docker container with switch startup configuration.

    Args:
        switch (Switch): Switch object containing configuration
        docker_client (aiodocker.Docker): Docker client for API interactions
        servername (str, optional): Docker daemon hostname. Defaults to 'localhost'.

    Returns:
        str: Configuration status
    """
    try:
        logger.info(f"Starting configuration for switch {switch.name}")
        
        # Validate configuration
        if not switch.initial_config:
            logger.warning(f"Empty configuration for switch {switch.name}")
            return 'skipped - empty config'

        # Prepare configuration string
        config_string = '\n'.join(switch.initial_config)
        ascii_config = config_string.encode('ascii')
        
        # Create tar archive
        fh = BytesIO()
        with tarfile.open(fileobj=fh, mode='w') as tarch:
            info = tarfile.TarInfo('startup-config')
            info.size = len(ascii_config)
            bytes_to_go = BytesIO(ascii_config)
            tarch.addfile(info, bytes_to_go)
        
        fh.seek(0)
        archive_content = fh.read()
        
        logger.info(f"Configuration archive created for {switch.name}")

        # Async Docker API session
        async with aiohttp.ClientSession(base_url=f"http://{servername}:2375") as session:
            # Start container
            await _start_container(session, switch.docker_container_id)
            
            # Copy configuration to container
            await _copy_config_to_container(
                session, 
                switch.docker_container_id, 
                archive_content
            )
            
            # Move configuration file
            await _move_config_file(session, switch.docker_container_id)
            
            # Stop container
            await _stop_container(session, switch.docker_container_id)

        logger.info(f"Successfully configured switch {switch.name}")
        return 'success'

    except Exception as e:
        logger.error(f"Error configuring switch {switch.name}: {e}")
        return f'failed - {str(e)}'

async def _start_container(session: aiohttp.ClientSession, container_id: str) -> None:
    """
    Start a Docker container.

    Args:
        session (aiohttp.ClientSession): Async HTTP session
        container_id (str): Docker container ID

    Raises:
        ContainerConfigurationError: If container start fails
    """
    async with session.post(f"/containers/{container_id}/start") as response:
        if response.status not in [204, 304]:
            raise ContainerConfigurationError(
                f"Could not start container. Status: {response.status}"
            )
    
    # Wait for container to be ready
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < 20:
        async with session.get(f"/containers/{container_id}/json") as response:
            if response.status == 200:
                container_info = await response.json()
                if container_info['State']['Running']:
                    return
        await asyncio.sleep(1)
    
    raise ContainerConfigurationError(f"Container {container_id} did not become ready")

async def _copy_config_to_container(
    session: aiohttp.ClientSession, 
    container_id: str, 
    archive_content: bytes
) -> None:
    """
    Copy configuration archive to container.

    Args:
        session (aiohttp.ClientSession): Async HTTP session
        container_id (str): Docker container ID
        archive_content (bytes): Configuration archive content

    Raises:
        ContainerConfigurationError: If file copy fails
    """
    headers = {'Content-Type': 'application/x-tar'}
    async with session.put(
        f"/containers/{container_id}/archive",
        params={'path': '/'},
        headers=headers,
        data=archive_content
    ) as response:
        if response.status != 200:
            raise ContainerConfigurationError(
                f"Error copying configuration. Status: {response.status}"
            )

async def _move_config_file(
    session: aiohttp.ClientSession, 
    container_id: str
) -> None:
    """
    Move configuration file to correct location in container.

    Args:
        session (aiohttp.ClientSession): Async HTTP session
        container_id (str): Docker container ID

    Raises:
        ContainerConfigurationError: If file move fails
    """
    # Create exec instance for mv command
    async with session.post(
        f"/containers/{container_id}/exec",
        json={
            "AttachStdout": True,
            "AttachStderr": True,
            "Cmd": ["mv", "/startup-config", "/mnt/flash/"]
        }
    ) as response:
        if response.status != 201:
            raise ContainerConfigurationError(
                f"Could not create mv exec. Status: {response.status}"
            )
        
        exec_data = await response.json()
        exec_id = exec_data['Id']

        # Start exec instance
        async with session.post(
            f"/exec/{exec_id}/start",
            json={"Detach": False, "Tty": False},
            headers={'Content-Type': 'application/json'}
        ) as exec_response:
            if exec_response.status != 200:
                raise ContainerConfigurationError(
                    f"mv command exec failed. Status: {exec_response.status}"
                )

async def _stop_container(
    session: aiohttp.ClientSession, 
    container_id: str
) -> None:
    """
    Stop a Docker container.

    Args:
        session (aiohttp.ClientSession): Async HTTP session
        container_id (str): Docker container ID

    Raises:
        ContainerConfigurationError: If container stop fails
    """
    async with session.post(f"/containers/{container_id}/stop") as response:
        if response.status not in [204, 304]:
            raise ContainerConfigurationError(
                f"Could not stop container. Status: {response.status}"
            )

async def make_a_gns3_node(
    switch: Switch, 
    session: aiohttp.ClientSession, 
    gns3_url: str, 
    nodex: int, 
    nodey: int, 
    prj_id: str
) -> Switch:
    """
    Create a GNS3 node for a switch.

    Args:
        switch (Switch): Switch to create a node for
        session (aiohttp.ClientSession): Async HTTP session
        gns3_url (str): Base URL for GNS3 API
        nodex (int): X coordinate for node placement
        nodey (int): Y coordinate for node placement
        prj_id (str): GNS3 project ID

    Returns:
        Switch: Updated switch with GNS3 node details
    """
    try:
        # Duplicate template
        async with session.post(
            f'{gns3_url}templates/{switch.gns3_template_id}/duplicate', 
            json=[]
        ) as response:
            response.raise_for_status()
            json_data = await response.json()
            tmp_template_id = json_data['template_id']

        # Update interface count
        async with session.put(
            f'{gns3_url}templates/{tmp_template_id}', 
            json={'adapters': switch.ethernet_interfaces + 1}
        ) as response:
            response.raise_for_status()

        # Create node
        async with session.post(
            f'{gns3_url}projects/{prj_id}/templates/{tmp_template_id}', 
            json={'x': nodex, 'y': nodey}
        ) as response:
            response.raise_for_status()
            json_data = await response.json()
            switch.gns3_node_id = json_data['node_id']

        # Delete temporary template
        async with session.delete(f'{gns3_url}templates/{tmp_template_id}'):
            pass

        # Rename node
        async with session.put(
            f'{gns3_url}projects/{prj_id}/nodes/{switch.gns3_node_id}', 
            json={'name': switch.name}
        ) as response:
            response.raise_for_status()

        # Get container ID
        async with session.get(
            f'{gns3_url}projects/{prj_id}/nodes/{switch.gns3_node_id}'
        ) as response:
            response.raise_for_status()
            json_data = await response.json()
            switch.docker_container_id = json_data['properties']['container_id']

        return switch

    except aiohttp.ClientResponseError as e:
        logger.error(f"GNS3 node creation failed: {e}")
        raise GNS3WorkerError(f"Failed to create GNS3 node: {e}")

async def gns3_post(
    session: aiohttp.ClientSession, 
    url: str, 
    method: str, 
    **kwargs
) -> None:
    """
    Send an async request to GNS3 server.

    Args:
        session (aiohttp.ClientSession): Async HTTP session
        url (str): URL to send request to
        method (str): HTTP method (get, post, put)
        **kwargs: Additional arguments for the request

    Raises:
        GNS3WorkerError: If request fails
    """
    try:
        jsondata = kwargs.get('jsondata', {})
        
        if method == 'post':
            async with session.post(url, json=jsondata) as response:
                response.raise_for_status()
        elif method == 'get':
            async with session.get(url, json=jsondata) as response:
                response.raise_for_status()
        elif method == 'put':
            async with session.put(url, json=jsondata) as response:
                response.raise_for_status()
        
        # Small delay to prevent overwhelming the server
        await asyncio.sleep(0.2)

    except aiohttp.ClientResponseError as e:
        logger.error(f"GNS3 API request failed: {e}")
        raise GNS3WorkerError(f"API request failed: {e}")
