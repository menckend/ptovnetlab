#!/usr/bin/env python3
"""
Arista Network Device Polling Module

This module provides asynchronous functionality for polling multiple Arista 
network switches concurrently using pyeapi, extracting device information 
and LLDP connection details.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Optional

import pyeapi
from ptovnetlab.data_classes import Switch, Connection

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class AristaPollerError(Exception):
    """Custom exception for Arista Poller related errors."""
    pass

def validate_switch_credentials(switch: str, username: str, password: str) -> bool:
    """
    Validate switch connection credentials.

    Args:
        switch (str): Switch hostname or IP address
        username (str): Authentication username
        password (str): Authentication password

    Returns:
        bool: True if credentials are valid, False otherwise
    """
    try:
        pyeapi.client.config.clear()
        pyeapi.client.config.add_connection(
            switch, 
            host=switch, 
            transport='https',
            username=username, 
            password=password
        )
        node = pyeapi.connect_to(switch)
        # Attempt a simple command to verify connection
        node.enable("show version")
        return True
    except Exception as e:
        logger.error(f"Credential validation failed for {switch}: {e}")
        return False

def invoker(
    switchlist_in: List[str], 
    uname_in: str, 
    passwd_in: str,
    runtype_in: str
) -> Tuple[List[Switch], List[Connection]]:
    """
    Synchronous entry point for switch polling.

    Args:
        switchlist_in (List[str]): List of Arista switches to interrogate
        uname_in (str): Username for switch authentication
        passwd_in (str): Password for switch authentication
        runtype_in (str): Type of polling run (e.g., 'discovery', 'update')

    Returns:
        Tuple[List[Switch], List[Connection]]: Polled switches and their connections
    """
    try:
        # Validate credentials for all switches before polling
        valid_switches = [
            switch for switch in switchlist_in 
            if validate_switch_credentials(switch, uname_in, passwd_in)
        ]

        if not valid_switches:
            raise AristaPollerError("No valid switches found for polling")

        switches, connections = asyncio.run(
            main(valid_switches, uname_in, passwd_in, runtype_in)
        )
        return switches, connections
    except Exception as e:
        logger.error(f"Switch polling failed: {e}")
        raise

async def main(
    switchlist_in2: List[str], 
    uname_in2: str, 
    passwd_in2: str,
    runtype_in2: str
) -> Tuple[List[Switch], List[Connection]]:
    """
    Asynchronously poll multiple Arista switches.

    Args:
        switchlist_in2 (List[str]): Switches to poll
        uname_in2 (str): Authentication username
        passwd_in2 (str): Authentication password
        runtype_in2 (str): Type of polling run

    Returns:
        Tuple[List[Switch], List[Connection]]: Polled switches and their connections
    """
    # Set the maximum number of worker threads
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=20))
    
    logger.info(f'Polling {len(switchlist_in2)} Arista switches via EOS API...')
    
    # Create tasks for each switch
    tasks = [
        asyncio.create_task(
            asyncio.to_thread(
                get_sw_data, 
                switch, 
                uname_in2, 
                passwd_in2, 
                sw_index
            )
        ) 
        for sw_index, switch in enumerate(switchlist_in2)
    ]

    # Gather the data from all EAPI polling threads
    try:
        answers = await asyncio.gather(*tasks)
        logger.info('Finished polling switches.')

        switches = []
        connections = []
        
        # Process the gathered data
        for val in answers:
            switch, lldp_connections = val
            switches.append(switch)
            connections.extend(lldp_connections)

        return switches, connections

    except Exception as e:
        logger.error(f"Error during switch polling: {e}")
        raise AristaPollerError(f"Switch polling failed: {e}")

def get_sw_data(
    switch3: str, 
    uname_in3: str, 
    passwd_in3: str, 
    sw_cntr3_in: int
) -> Tuple[Switch, List[Connection]]:
    """
    Retrieve switch data and LLDP connections for a single switch.

    Args:
        switch3 (str): Switch hostname or IP to interrogate
        uname_in3 (str): Authentication username
        passwd_in3 (str): Authentication password
        sw_cntr3_in (int): Switch index for logging purposes

    Returns:
        Tuple[Switch, List[Connection]]: Switch object and its LLDP connections
    """
    try:
        # Clear any existing pyeapi.client.config
        pyeapi.client.config.clear()
        
        # Build the pyeapi.client.config object
        pyeapi.client.config.add_connection(
            switch3, 
            host=switch3, 
            transport='https',
            username=uname_in3, 
            password=passwd_in3
        )
        
        # Connect to the switch
        node = pyeapi.connect_to(switch3)
        
        # Get JSON-formatted results of several 'show...' commands
        eos_output = node.enable(
            ("show version", "show lldp neighbors", "show lldp local-info"), 
            format="json"
        )
        
        # Create Switch object
        switch = Switch(
            name=switch3,
            model=eos_output[0]["result"]["modelName"],
            eos_version=eos_output[0]["result"]["version"],
            system_mac=eos_output[0]["result"]["systemMacAddress"],
            serial_number=eos_output[0]["result"]["serialNumber"],
            lldp_system_name=eos_output[2]["result"]["systemName"],
            ethernet_interfaces=0,  # Will be set by arista_sanitizer
            gns3_template_id='',   # Will be set by gns3_worker
            gns3_node_id='',       # Will be set by gns3_worker
            docker_container_id='', # Will be set by gns3_worker
            initial_config=node.running_config.splitlines()
        )

        # Create Connection objects from LLDP neighbors
        connections = [
            Connection(
                switch_a=str(eos_output[2]["result"]["systemName"]),
                port_a=str(value["port"]),
                switch_b=str(value["neighborDevice"]),
                port_b=str(value["neighborPort"])
            )
            for value in eos_output[1]["result"]["lldpNeighbors"]
        ]

        logger.info(f"Finished polling switch: {switch3}")
        return switch, connections

    except Exception as e:
        logger.error(f"Error polling switch {switch3}: {e}")
        raise AristaPollerError(f"Failed to poll switch {switch3}: {e}")
