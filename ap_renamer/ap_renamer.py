#!/usr/bin/python3

import re
import sys
import getpass
from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

def find_aps_in_cdp(output):
	"""Parse 'show power inline' output and return list of (device_name, interface).

	This looks for device names that either start with/contain 'C91' or 'AIR-' followed by any chars.
	It is tolerant to different column layouts by searching each line for the device pattern
	and taking the first token on the line as the interface (if present).
	"""
	dev_pat = re.compile(r'(C91[^\s]*|AIR-[^\s]+)', re.IGNORECASE)
	intf_pat = re.compile(r'^\s*(\S+)')
	results = []
	for line in output.splitlines():
		m = dev_pat.search(line)
		if not m:
			continue
		device = m.group(0).strip()
		# try to get the interface from start of line
		mi = intf_pat.match(line)
		ifname = mi.group(1) if mi else '<unknown>'
		ifname = normalize_interface_name(ifname)
		results.append((device, ifname))
	return results


def normalize_interface_name(ifname):
	"""Normalize various interface name formats to a canonical long form.

	Examples:
	 - 'Gi1/0/38' -> 'GigabitEthernet1/0/38'
	 - 'Gig 1/0/38' -> 'GigabitEthernet1/0/38'
	 - 'GigabitEthernet1/0/38' -> 'GigabitEthernet1/0/38'
	If the interface can't be normalized, return the stripped original.
	"""
	if not ifname:
		return ifname
	ifname = ifname.strip()
	# find the numeric part like 1/0/38 or 1/0
	m = re.search(r'(\d+(?:/\d+)*)', ifname)
	if not m:
		return ifname
	nums = m.group(1)
	low = ifname.lower()
	if 'gig' in low or low.startswith('gi'):
		return f'GigabitEthernet{nums}'
	if 'ten' in low or low.startswith('te'):
		return f'TenGigabitEthernet{nums}'
	if 'fast' in low or low.startswith('fa'):
		return f'FastEthernet{nums}'
	# fallback: return as-is
	return ifname


def parse_cdp_neighbors_detail(output):
	"""Parse 'show cdp neighbors detail' output and return mapping local_interface -> device_id

	Looks for blocks like:
	Device ID: <hostname>
	  Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): <...>
	and maps local interface -> hostname.
	"""
	device_re = re.compile(r'^\s*Device ID:\s*(.+)', re.IGNORECASE)
	if_re = re.compile(r'^\s*Interface:\s*([^,]+),', re.IGNORECASE)
	results = {}
	current_device = None
	for line in output.splitlines():
		m = device_re.match(line)
		if m:
			current_device = m.group(1).strip()
			continue
		if current_device:
			mi = if_re.match(line)
			if mi:
				ifname = mi.group(1).strip()
				ifname = normalize_interface_name(ifname)
				results[ifname] = current_device
				current_device = None
	return results

def main():
	# Maintain previous credentials and configuration between switches
	prev_user_lines = None
	prev_do_default = None
	prev_do_desc = None
	credentials = None  # dict with 'username' and 'password'

	while True:
		# Prompt for next switch
		ip = input("Switch IP (blank to exit): ").strip()
		if not ip:
			print("Exiting.")
			return

		# Credentials: reuse or ask
		if credentials is None:
			username = input("Username: ").strip()
			password = getpass.getpass("Password: ")
			credentials = {'username': username, 'password': password}
		else:
			use_creds = input("Use previous credentials? [Y/n]: ").strip().lower()
			if use_creds == 'n':
				username = input("Username: ").strip()
				password = getpass.getpass("Password: ")
				credentials = {'username': username, 'password': password}
			else:
				username = credentials['username']
				password = credentials['password']

		device = {
			'device_type': 'cisco_ios',
			'host': ip,
			'username': username,
			'password': password,
		}

		try:
			ssh = ConnectHandler(**device)
		except (NetmikoTimeoutException, NetmikoAuthenticationException) as e:
			print(f"Connection error: {e}")
			# Ask whether to try another switch
			again = input("Try another switch? [y/N]: ").strip().lower()
			if again == 'y':
				continue
			return
		except Exception as e:
			print(f"Unexpected error: {e}")
			again = input("Try another switch? [y/N]: ").strip().lower()
			if again == 'y':
				continue
			return

		try:
			output = ssh.send_command("show power inline")

			aps = find_aps_in_cdp(output)
			if not aps:
				print("No APs found with 'AIR-' or 'C91' in power inline.")
				# finished with this switch
				ssh.disconnect()
				again = input("Configure another switch? [y/N]: ").strip().lower()
				if again == 'y':
					continue
				return

			# Group by interface and report
			interfaces = {}
			for dev, intf in aps:
				interfaces.setdefault(intf, []).append(dev)

			# Try to gather CDP neighbor details for nicer descriptions
			try:
				cdp_detail = ssh.send_command("show cdp neighbors detail")
				cdp_map = parse_cdp_neighbors_detail(cdp_detail)
			except Exception:
				cdp_map = {}

			total_aps = sum(len(v) for v in interfaces.values())
			print(f"Found {total_aps} AP(s).")
			print("Interfaces with APs:")
			for intf, devs in sorted(interfaces.items()):
				for d in devs:
					print(f"  {intf} - {d}")

			# Ask if user wants to configure the ports
			proceed = input("Configure these interfaces? [y/N]: ").strip().lower()
			if proceed != 'y':
				print("Skipping configuration on this switch.")
				ssh.disconnect()
				again = input("Configure another switch? [y/N]: ").strip().lower()
				if again == 'y':
					continue
				return

			# Decide whether to reuse previous config lines
			if prev_user_lines is not None:
				reuse_cfg = input("Reuse previous configuration lines? [Y/n]: ").strip().lower()
				if reuse_cfg != 'n':
					user_lines = prev_user_lines
					do_default = prev_do_default
					# reuse previous description preference if present
					do_desc = prev_do_desc if prev_do_desc is not None else False
				else:
					# Ask whether to use neighbor name as description BEFORE entering config lines
					desc_choice = input("Apply CDP neighbor name as interface description? [Y/n]: ").strip().lower()
					do_desc = (desc_choice != 'n')
					# prompt for new config
					print("Enter the configuration lines to apply to each interface.")
					print("End input with a single period '.' on a line by itself.")
					user_lines = []
					while True:
						try:
							line = input()
						except EOFError:
							break
						if line.strip() == '.':
							break
						user_lines.append(line.rstrip())

					if not user_lines:
						if do_desc:
							print("No configuration lines provided; will apply CDP description(s) only.")
						else:
							print("No configuration lines provided; nothing to do.")
							ssh.disconnect()
							again = input("Configure another switch? [y/N]: ").strip().lower()
							if again == 'y':
								continue
							return
					# ask whether to default (default is No)
					default_choice = input("Default interfaces before applying config? [y/N]: ").strip().lower()
					do_default = (default_choice == 'y')
			else:
				# No previous config; prompt whether to use CDP neighbor name as description first
				# Ask whether to use neighbor name as description BEFORE entering config lines
				desc_choice = input("Apply CDP neighbor name as interface description? [Y/n]: ").strip().lower()
				do_desc = (desc_choice != 'n')
				# Now prompt for new config
				print("Enter the configuration lines to apply to each interface.")
				print("End input with a single period '.' on a line by itself.")
				user_lines = []
				while True:
					try:
						line = input()
					except EOFError:
						break
					if line.strip() == '.':
						break
					user_lines.append(line.rstrip())

				if not user_lines:
					if do_desc:
						print("No configuration lines provided; will apply CDP description(s) only.")
					else:
						print("No configuration lines provided; nothing to do.")
						ssh.disconnect()
						again = input("Configure another switch? [y/N]: ").strip().lower()
						if again == 'y':
							continue
						return
				# ask whether to default
				default_choice = input("Default interfaces before applying config? [y/N]: ").strip().lower()
				do_default = (default_choice == 'y')

			# Optionally save these lines for next switches
			save_cfg = input("Save these configuration lines for reuse on next switches? [y/N]: ").strip().lower()
			if save_cfg == 'y':
				prev_user_lines = user_lines
				prev_do_default = do_default
				prev_do_desc = do_desc

			# Show planned commands and confirm
			print("\nPlanned changes for each interface:")
			for intf in sorted(interfaces.keys()):
				if intf == '<unknown>':
					print(f"  Skipping unknown interface")
					continue
				if do_default:
					print(f"  default interface {intf}")
				print(f"  interface {intf}")
				if do_desc:
					# Prefer CDP hostname mapping, fallback to the detected neighbor name
					desc = cdp_map.get(intf) or (interfaces.get(intf, [None])[0])
					if desc:
						print(f"    description {desc}")
				for l in user_lines:
					print(f"    {l}")

			confirm = input("Proceed with applying these changes? [y/N]: ").strip().lower()
			if confirm != 'y':
				print("Cancelled by user.")
				ssh.disconnect()
				again = input("Configure another switch? [y/N]: ").strip().lower()
				if again == 'y':
					continue
				return

			# Apply changes per interface
			for intf in sorted(interfaces.keys()):
				if intf == '<unknown>':
					print("Skipping unknown interface")
					continue
				print(f"\nApplying changes to {intf}...")
				try:
					# Build config commands; include default only if requested
					cfg_commands = []
					if do_default:
						cfg_commands.append(f"default interface {intf}")
					cfg_commands += [f"interface {intf}"]
					if do_desc:
						# Prefer CDP hostname mapping, fallback to the detected neighbor name
						desc = cdp_map.get(intf) or (interfaces.get(intf, [None])[0])
						if desc:
							cfg_commands.append(f"description {desc}")
					cfg_commands += user_lines
					out_cfg = ssh.send_config_set(cfg_commands)
					# Suppress verbose device output; show concise confirmation
					print(f"Applied {len(user_lines)} config lines to {intf}")
				except Exception as e:
					print(f"Error configuring {intf}: {e}")
					# continue to next interface
					continue

			print("Configuration complete.")
		finally:
			try:
				ssh.disconnect()
			except Exception:
				pass

		# Ask whether to configure another switch
		again = input("Configure another switch? [y/N]: ").strip().lower()
		if again == 'y':
			continue
		return

if __name__ == "__main__":
	main()
