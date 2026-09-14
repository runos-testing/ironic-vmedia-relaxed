"""Arm the immediate iDRAC boot selector without scheduling a BIOS job."""
from pathlib import Path
import re
import socket
import time
from urllib.parse import urlsplit

import paramiko

HOST_KEYS = Path('/shared/ironic-ssh/known_hosts')
PROMPT = re.compile(r'(?:^|[\r\n])/admin1->\s*$')


class BootSelectionError(RuntimeError):
    """A diagnostic that contains no controller credentials or addresses."""


def authenticate(transport, username, password):
    remaining = transport.auth_password(username, password, fallback=True)
    if transport.is_authenticated():
        return
    if 'keyboard-interactive' in remaining:
        def respond(title, instructions, prompts):
            if not prompts:
                return []
            if len(prompts) != 1 or prompts[0][1] or 'password' not in prompts[0][0].lower():
                raise BootSelectionError('Unexpected controller authentication prompt')
            return [password]
        transport.auth_interactive(username, respond)
    if not transport.is_authenticated():
        raise BootSelectionError('Controller authentication did not complete')


def arm(run):
    for command in ('racadm set iDRAC.serverboot.FirstBootDevice VCD-DVD',
                    'racadm set iDRAC.serverboot.BootOnce Enabled'):
        if 'ERROR:' in run(command):
            raise BootSelectionError('Controller rejected the one-time boot setting')
    observed = run('racadm get iDRAC.serverboot')
    if not re.search(r'(?im)^FirstBootDevice=VCD-DVD\s*$', observed) or not re.search(
            r'(?im)^BootOnce=Enabled\s*$', observed):
        raise BootSelectionError('Controller one-time boot readback did not match')


def _read_prompt(channel):
    output = ''
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        data = channel.recv(4096)
        if not data:
            raise BootSelectionError('Controller closed the command session')
        output += data.decode('utf-8', errors='replace')
        if len(output) > 65536:
            raise BootSelectionError('Controller response exceeded the output limit')
        if PROMPT.search(output):
            return output.replace('\r', '')
    raise BootSelectionError('Controller command timed out')


def _trust_key(host, key):
    HOST_KEYS.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    keys = paramiko.HostKeys()
    if HOST_KEYS.exists():
        keys.load(str(HOST_KEYS))
    if keys.lookup(host):
        if not keys.check(host, key):
            raise BootSelectionError('Controller SSH host key changed')
    else:
        keys.add(host, key.get_name(), key)
        keys.save(str(HOST_KEYS))
        HOST_KEYS.chmod(0o600)


def set_one_time_boot(driver_info):
    host = urlsplit(driver_info['redfish_address']).hostname
    transport = paramiko.Transport(socket.create_connection((host, 22), timeout=15))
    try:
        # These algorithms match the measured controller firmware.
        security = transport.get_security_options()
        security.kex = ('diffie-hellman-group14-sha1',)
        security.key_types = ('ssh-rsa',)
        transport.auth_timeout = 25
        transport.start_client(timeout=20)
        _trust_key(host, transport.get_remote_server_key())
        authenticate(transport, driver_info['redfish_username'], driver_info['redfish_password'])
        channel = transport.open_session(timeout=15)
        channel.settimeout(30)
        channel.get_pty(width=200)
        channel.invoke_shell()
        _read_prompt(channel)

        def run(command):
            channel.sendall(command + '\r')
            return _read_prompt(channel)

        arm(run)
    finally:
        transport.close()
