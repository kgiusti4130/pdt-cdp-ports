#!/usr/bin/env python3

import sys
import getpass
from netmiko import ConnectHandler


###############################################################################
# LLDP PARSER
###############################################################################

def parse_lldp_neighbors(output):
    neighbors = []
    blocks = output.split("------------------------------------------------")

    for block in blocks:
        local_intf = None
        system_name = ""
        system_desc = ""

        lines = [l.rstrip() for l in block.splitlines()]
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            if line.startswith("Local Intf:"):
                local_intf = line.split("Local Intf:")[1].strip()

            elif line.startswith("System Name:"):
                system_name = line.split("System Name:")[1].strip()

            elif line.startswith("System Description:"):
                # Cisco puts description on the NEXT non-empty line
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if next_line:
                        system_desc = next_line
                        break
                    j += 1
            i += 1

        if local_intf:
            neighbors.append({
                "local_intf": local_intf,
                "system_name": system_name,
                "system_description": system_desc,
            })

    return neighbors


###############################################################################
# DEVICE POLICIES
###############################################################################

DEVICE_POLICIES = [
    {
        "name": "Arista Leaf Switch",
        # Match by exact system name
        "match": lambda n: n["system_name"] == "LEAF1",
        "config": [
             "description config tool LEAF1",
             "switchport mode access",
             "switchport access vlan 1",
             "spanning-tree portfast",
             "spanning-tree bpduguard enable",
        ],
    },
    {
        "name": "Arista Access Point",
        # Match any Arista AP model
        "match": lambda n: "ARISTA AP" in n["system_description"],
        "config": [
            "description Arista_AP_Config_tool",
            "switchport trunk allowed vlan 1,4091",
            "switchport mode trunk",
            "power inline auto",
            "spanning-tree portfast trunk",
            "spanning-tree bpduguard enable",
        ],
    },
]


###############################################################################
# MAIN
###############################################################################

def main():
    ip = input("Switch IP (blank to exit): ").strip()
    if not ip:
        sys.exit(0)

    username = input("Username: ")
    password = getpass.getpass()

    device = {
        "device_type": "cisco_ios",
        "host": ip,
        "username": username,
        "password": password,
        "fast_cli": False,
    }

    print("\nConnecting to switch...")
    conn = ConnectHandler(**device)

    print("Collecting LLDP neighbors...")
    lldp_output = conn.send_command(
        "show lldp neighbors detail",
        expect_string=r"#"
    )

    neighbors = parse_lldp_neighbors(lldp_output)

    if not neighbors:
        print("No LLDP neighbors found.")
        conn.disconnect()
        return

    config_cmds = []

    for n in neighbors:
        for policy in DEVICE_POLICIES:
            if policy["match"](n):
                print(
                    f"Matched {policy['name']} on "
                    f"{n['local_intf']} (system: {n['system_name']})"
                )

                config_cmds.extend([
                    f"interface {n['local_intf']}",
                    *policy["config"],
                    "exit"
                ])
                break  # only first match per interface

    if not config_cmds:
        print("No matching device policies found.")
        conn.disconnect()
        return

    print("\nApplying configuration...")
    print(conn.send_config_set(config_cmds))

    conn.save_config()
    conn.disconnect()
    print("\nDone.")


if __name__ == "__main__":
    main()
