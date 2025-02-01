"""eos_sanitizer.py

For modifying the configuration extracted from an Arista hardware
 appliance's EOS configuration to make it suitable for use on a cEOS
 container in a lab environment"""

from ptovnetlab.data_classes import Switch


def eos_to_ceos(switch: Switch) -> Switch:
    """Module entry-point. Accepts a Switch object and returns it with
    a cEOS lab-ready version of the configuration

    Parameters
    ---------
    switch : Switch
        Switch object containing the configuration to sanitize

    Returns
    -------
    switch : Switch
        Updated Switch object with sanitized configuration and interface count
    """

    # List of global-config commands that we should comment out for \
    # cEOS-compatibility and lab environments in general
    badStarts = ['radius',
                 'username',
                 'aaa',
                 'ip radius',
                 'hardware speed',
                 'queue',
                 'server ',
                 'ip radius',
                 'ntp server',
                 'daemon TerminAttr',
                 '   exec /usr/bin/TerminAttr']

    # Get the number of Ethernet interfaces present in the \
    # original config
    switch.ethernet_interfaces = count_ether_interfaces(switch.initial_config)

    # Replace all references to 'Management1' in the config with \
    # 'Ethernet0'
    mgt_port_str = 'Management0'

    # Loop through the lines in each switch's configuration
    for linect, line in enumerate(switch.initial_config):
        # Replace the Management1 interface name with an extra Ethernet interface
        switch.initial_config[linect] = line.replace('Management1', mgt_port_str)
        switch.initial_config[linect] = line.replace('Management0', mgt_port_str)
        # Eliminate config lines that begin with any of the "badStarts" strings
        for oopsie in badStarts:
            if switch.initial_config[linect].startswith(oopsie):
                # Can't just delete the un-wanted lines, that would screw up
                # the iteration through the list. Better to just prepend with a '!'
                switch.initial_config[linect] = "!removed_for_cEOS-lab| " + switch.initial_config[linect]
        # Get rid of '...netN/2|3|4' interface config sections altogether
        # (can't have them getting converted to ../netN and their vestigial config
        # overwriting the actual interface config
        spurious_interface = False
        # Check to see if the current config line is a 'spurious' interface
        spurious_interface = switch.initial_config[linect].startswith(
            'interface Ethernet') and ('/2' in switch.initial_config[linect] or '/3'
                                       in switch.initial_config[linect] or '/4' in
                                       switch.initial_config[linect])
        if spurious_interface:
            # Loop through the lines in the spurious interface's config section
            # and comment them out by prepending with '!'
            next_sec = False
            shortcount = linect
            # Stop commenting out lines when we get to the end of the config
            # section (marked by a line consisting of '!')
            while not next_sec:
                if switch.initial_config[shortcount] == '!':
                    next_sec = True
                switch.initial_config[shortcount] = '!' + switch.initial_config[shortcount]
                shortcount += 1
        # Convert interface names from  '...netn/m' to '...netn'
        if switch.initial_config[linect].startswith('interface Ethernet'):
            switch.initial_config[linect] = switch.initial_config[linect].split('/')[0]

    # Add a configuration section to apply the system-mac-address of the
    # original switch to the cEOS container's configuration
    switch.initial_config = applySysMac(switch.initial_config, switch.system_mac)
    return switch


def count_ether_interfaces(config: list) -> int:
    """Accept a list of lines representing a switch config and return
    the number of Ethernet interfaces the corresponding cEOS
    container will need

    Parameters
    ---------
    config : list
        List of lines of a switch's configuration

    Returns
    -------
    my_ethercount : int
        The number of Ethernet interfaces the cEOS container version of the switch will
        need
    """
    my_ethercount = 0
    for line in config:
        # We're only counting single interfaces (not the breakout interfaces)
        if (line.startswith('interface Ethernet') and (not (line.endswith('/2') or
                                                            line.endswith('/3') or
                                                            line.endswith('/4')))):
            my_ethercount += 1
    return my_ethercount


def applySysMac(config: list, sys_mac: str) -> list:
    """Construct and append the event handler configuration to
    apply the system-mac-address of the modeled switch to the cEOS
    container's configuration

    Parameters
    ---------
    config : list
        List of lines of a switch's configuration
    sys_mac : str
        The system MAC address of the original switch

    Returns
    -------
    config : list
        Updated list of lines of a switch's configuration
    """
    
    # Create an event-handler section to append to the configuration to
    # use the original switch's MAC address
    sysMacSnip = ['', '', '', '', '', '', '']
    sysMacSnip[0] = 'event-handler onStartup'
    sysMacSnip[1] = ' trigger on-boot'
    sysMacSnip[2] = ' action bash'
    sysMacSnip[4] = '  echo $var_sysmac > /mnt/flash/system_mac_address'
    sysMacSnip[5] = '  truncate -s -1 /mnt/flash/system_mac_address'
    sysMacSnip[6] = '  EOF'
    # Remove the last line ('end') of the config  and append the system_mac_config
    # snippet(with the REAL switch's MAC address) before adding the final 'end' back
    # This will help our lab switches look more like the prod switches, but will
    # also work around the system-mac MLAG bug on cEOS
    poppedline = config.pop(-1)
    sysMacSnip[3] = '      var_sysmac=\'' + sys_mac + '\''
    for sysmacline in range(len(sysMacSnip)):
        config.append(sysMacSnip[sysmacline])
    config.append(poppedline)
    return config
