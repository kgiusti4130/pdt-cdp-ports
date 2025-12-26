#!/usr/bin/env python3

import sys
import getpass
from netmiko import ConnectHandler


def parse_lldp_neighbors(output):
    neighbors = []

    blocks = output.split("------------------------------------------------")

    for block in blocks:
        local_intf = None
        system_desc = ""
        lines = [l.rstrip() for l in block.splitlines()]

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if line.startswith("Local Intf:"):
                local_intf = line.split("Local Intf:")[1].strip()

            elif line.startswith("System Description:"):
                # Cisco puts description on the NEXT line
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if next_line:
                        system_desc = next_line
                        break
                    j += 1

            i += 1

        if local_intf and system_desc:
            neighbors.append({
                "local_intf": local_intf,
                "system_description": system_desc
            })

    return neighbors


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

    # DEBUG — uncomment if needed
    # print(neighbors)

    ap_interfaces = [
        n["local_intf"]
        for n in neighbors
        if "ARISTA AP C-130" in n["system_description"]
    ]

    if not ap_interfaces:
        print("No Arista APs found via LLDP.")
        conn.disconnect()
        return

    print(f"\nFound Arista APs on interfaces: {ap_interfaces}")

    config_cmds = []
    for intf in ap_interfaces:
        config_cmds.extend([
            f"interface {intf}",
            "description Arista_AP_Config_tool",
            "switchport trunk allowed vlan 1,4091",
            "switchport mode trunk",
            "power inline auto",
            "spanning-tree portfast trunk",
            "spanning-tree bpduguard enable",
            "exit"
        ])

    print("\nApplying configuration...")
    print(conn.send_config_set(config_cmds))

    conn.save_config()
    conn.disconnect()
    print("\nDone.")


if __name__ == "__main__":
    main()
