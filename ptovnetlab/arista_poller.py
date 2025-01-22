"""eosPoller.py

Uses asyncio.to_thread and gather functions to poll multiple switches concurrently """

import asyncio
from concurrent.futures import ThreadPoolExecutor
import pyeapi


def invoker(switchlist_in: list, uname_in: str, passwd_in: str,
            runtype_in: str) -> tuple[list, list, list]:
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
    svsout : list
        Values of the switch that we're interested in
    lldpsout : list
        List of connections inferred from LLDP tables on the switches
    cfgsout : list
        List-of-lists holding all of the switch's configurations
            'all_configs_out[0]' is the configuration of the first item in switchlist
            'all_configs_out[0][0] is the 1st line of the 1st switch's configuration
    """

    svsout, lldpsout, cfgsout = asyncio.run(main(switchlist_in, uname_in, passwd_in,
                                            runtype_in))
    return svsout, lldpsout, cfgsout


async def main(switchlist_in2: list, uname_in2: str, passwd_in2: str,
               runtype_in2: str) -> tuple[list, list, list]:
    """Connect to  switches and get the data that we want from them.  Use asyncio
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
    sw_vals_out2 : list
        Values of the switch that we're interested in
    lldpngbrs_out2 : list
        List of connections inferred from LLDP tables on the switches
    all_configs_out2 : list
        List-of-lists holding all of the switch's configurations
            'all_configs_out2[0]' is the configuration of the first item in switchlist
            'all_configs_out2[0][0] is the 1st line of the 1st switch's configuration
    """
    # Set the maximum number of worker-threads we're willing to use
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=20))
    # Initialize the lists we will eventually return
    switchcount2 = len(switchlist_in2)
    sw_vals_out2 = [[]] * (switchcount2)
    allconfigs_out2 = [[]] * (switchcount2)
    lldpngbrs_out2 = []
    tasks = []

    print('Polling Arista switches via EOS API..')
    # Enumerate the switchlist, spawning a new asyncio thread/task to get data from each
    for sw_cntr2, value2 in enumerate(switchlist_in2):
        coro = asyncio.to_thread(get_sw_data, value2, uname_in2, passwd_in2, sw_cntr2)
        tasks.append(asyncio.create_task(coro))

    # Gather the data from all the EAPI polling threads

    answers = [] * (switchcount2)
    answers = await asyncio.gather(*tasks)
    print('Finished polling switches.')
    print('')
    # Loop through the EAPI responses and split each response into the right buckets
    # Each item in "answers" is the data returned by get_sw_data for a single switch.
    # (A list switch properties; a list of LLDP neighbors, and a list of lines in the
    # startup config.)  We don't want to three objects for every switch, we want to
    # return three objects, each of which has the information for *all* of the switches
    for val in answers:
        # Item [0] in each "answer" is the list of switch properties (switch_vals)
        sw_vals_out2[val[3]] = val[0]
        # Item [0] in each "answer" is the list of LLDP neighbors for the switch
        for i, entry in enumerate(val[1]):
            lldpngbrs_out2.append(entry)
        allconfigs_out2[val[3]] = val[2]
    return sw_vals_out2, lldpngbrs_out2, allconfigs_out2


def get_sw_data(switch3: str, uname_in3: str, passwd_in3: str, sw_cntr3_in: int
                ) -> tuple[list, list, list]:
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
    this_sw_vals : list
        Values of the switch that we're interested in
    this_sw_lldp_nbrs : list
        The switch's LLDP neighbors table (local/remote device-name and port-I)
    this_sw_cfg : list
        The switch's startup configuration
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
    # Pluck the specific bits out data we want from the "show" cmds' output
    eos_output_model = eos_output[0]["result"]["modelName"]
    eos_output_ver = eos_output[0]["result"]["version"]
    eos_output_mac = eos_output[0]["result"]["systemMacAddress"]
    eos_output_serial = eos_output[0]["result"]["serialNumber"]
    eos_output_lldpname = eos_output[2]["result"]["systemName"]
    # Create this_switch_data list to return (include empty indecesfor later)
    this_sw_vals = ([switch3, eos_output_model, eos_output_ver, eos_output_mac,
                    eos_output_serial, eos_output_lldpname, '', '', '', '', '', ''])
    # Create this_sw_lldpnbrs list to return
    this_sw_lldpnbrs = []
    for value in eos_output[1]["result"]["lldpNeighbors"]:
        this_sw_lldpnbrs.append([str(eos_output_lldpname), str(value["port"]),
                                 str(value["neighborDevice"]),
                                 str(value["neighborPort"])])
    # Create this_sw_cfg list to return
    this_sw_cfg = []
    this_sw_cfg = node.running_config_config.splitlines()
    print("Finished polling: " + switch3)
    return this_sw_vals, this_sw_lldpnbrs, this_sw_cfg, int(sw_cntr3_in)
