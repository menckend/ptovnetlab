"""eosPoller.py

Uses asyncio.to_thread and gather functions to poll multiple switches concurrently """

import asyncio
from concurrent.futures import ThreadPoolExecutor
import pyeapi
from ptovnetlab.data_classes import Switch, Connection


def invoker(switchlist_in: list, uname_in: str, passwd_in: str,
            runtype_in: str) -> tuple[list[Switch], list[Connection]]:
    """Run synchronously; provide entry-point for module; manage the asyncio eventloop;
    invoke async/threaded functions to do the real work.

    Parameters
    ---------
    switchlist_in : list
        List of Arista switches to be interrogated
    uname_in : str
        The username credential for authenticating to the switch
    passwd_in : str
        The password credential for authenticating to the switch

    Returns
    -------
    switches : list[Switch]
        List of Switch objects containing device information and configuration
    connections : list[Connection]
        List of Connection objects inferred from LLDP tables on the switches
    """

    switches, connections = asyncio.run(main(switchlist_in, uname_in, passwd_in,
                                            runtype_in))
    return switches, connections


async def main(switchlist_in2: list, uname_in2: str, passwd_in2: str,
               runtype_in2: str) -> tuple[list[Switch], list[Connection]]:
    """Connect to switches and get the data that we want from them. Use asyncio
    to_thread function to enable concurrent processing of multiple switches.

    Parameters
    ---------
    switchlist_in2 : list
        List of Arista switches to be interrogated
    uname_in2 : str
        The username credential for authenticating to the switch
    passw_in2 : str
        The password credential for authenticating to the switch

    Returns
    -------
    switches : list[Switch]
        List of Switch objects containing device information and configuration
    connections : list[Connection]
        List of Connection objects inferred from LLDP tables on the switches
    """
    # Set the maximum number of worker-threads we're willing to use
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=20))
    
    tasks = []
    print('Polling Arista switches via EOS API..')
    
    # Create tasks for each switch
    for sw_cntr2, value2 in enumerate(switchlist_in2):
        coro = asyncio.to_thread(get_sw_data, value2, uname_in2, passwd_in2, sw_cntr2)
        tasks.append(asyncio.create_task(coro))

    # Gather the data from all the EAPI polling threads
    answers = await asyncio.gather(*tasks)
    print('Finished polling switches.')
    print('')

    switches = []
    connections = []
    
    # Process the gathered data
    for val in answers:
        switch, lldp_connections = val
        switches.append(switch)
        connections.extend(lldp_connections)

    return switches, connections


def get_sw_data(switch3: str, uname_in3: str, passwd_in3: str, sw_cntr3_in: int
                ) -> tuple[Switch, list[Connection]]:
    """Connect to a switch and get the data that we want from it

    Parameters
    ---------
    switch3 : str
        The network switch to be interrogated
    uname_in3 : str
        The username credential for authenticating to the switch
    passwd_in3 : str
        The password credential for authenticating to the switch

    Returns
    -------
    switch : Switch
        Switch object containing device information and configuration
    connections : list[Connection]
        List of Connection objects representing LLDP neighbors
    """

    # Clear any existing pyeapi.client.config
    pyeapi.client.config.clear()
    # Build the pyeapi.client.config object required for connecting to the switch
    pyeapi.client.config.add_connection(switch3, host=switch3, transport='https',
                                        username=uname_in3, password=passwd_in3)
    # Connect to the switch
    node = pyeapi.connect_to(switch3)
    # Get JSON-formatted results of several 'show...' commands
    eos_output = node.enable(("show version", "show lldp neighbors",
                             "show lldp local-info"), format="json")
    
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
        gns3_node_id='',      # Will be set by gns3_worker
        docker_container_id='', # Will be set by gns3_worker
        initial_config=node.running_config.splitlines()
    )

    # Create Connection objects from LLDP neighbors
    connections = []
    for value in eos_output[1]["result"]["lldpNeighbors"]:
        connections.append(Connection(
            switch_a=str(eos_output[2]["result"]["systemName"]),
            port_a=str(value["port"]),
            switch_b=str(value["neighborDevice"]),
            port_b=str(value["neighborPort"])
        ))

    print("Finished polling: " + switch3)
    return switch, connections
