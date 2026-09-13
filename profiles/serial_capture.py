"""Capture serial output without exposing an interactive BMC shell."""
import base64
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pexpect
from serial_credentials import read_credentials

HOST_KEYS = Path('/shared/runos/serial_known_hosts')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b[^\[]')
LIMIT = 65536


def connection_settings(node):
    auth = Path('/ironic-auth')
    credentials = auth.joinpath('username').read_bytes() + b':' + auth.joinpath('password').read_bytes()
    request = Request(os.environ.get('IRONIC_API_URL', 'http://127.0.0.1:6385').rstrip('/') + '/v1/nodes/' + node,
        headers={'Authorization': 'Basic ' + base64.b64encode(credentials).decode(),
                 'X-OpenStack-Ironic-API-Version': '1.82'})
    with urlopen(request, timeout=10) as response:
        info = json.load(response)['driver_info']
    host = urlsplit(info['redfish_address']).hostname
    key = urlsplit(info['redfish_address']).netloc.lower() + '/' + info['redfish_system_id'].lstrip('/')
    profile = json.loads(Path('/shared/runos/profiles.json').read_text())['machines'].get(key, {})
    mode = profile.get('serialConsole', 'disabled')
    if mode not in ('idrac-ssh', 'ilo-ssh'):
        raise ValueError('The provider has not enabled a serial adapter for this machine.')
    username, password = read_credentials(profile.get('credentialsName'))
    return host, username, password, mode, key


class SerialCapture:
    def __init__(self, node, epoch):
        self.epoch = epoch
        self.expires = time.monotonic() + 45
        self.state = 'connecting'
        self.message = 'Connecting to the serial console.'
        self.text = ''
        self.truncated = False
        self.redactions = []
        self.profile_key = None
        self.mode = None
        self.client = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, args=(node,), daemon=True)
        self.thread.start()

    def run(self, node):
        host = password = ''
        try:
            host, username, password, mode, key = connection_settings(node)
            self.profile_key, self.mode = key, mode
            self.redactions = [password, host]
            HOST_KEYS.parent.mkdir(parents=True, exist_ok=True)
            args = ['-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=' + str(HOST_KEYS),
                    '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
                    '-o', 'KexAlgorithms=+diffie-hellman-group14-sha1', '-o', 'HostKeyAlgorithms=+ssh-rsa',
                    '-o', 'Ciphers=+aes128-cbc', '-o', 'MACs=+hmac-sha1',
                    '-o', 'PreferredAuthentications=keyboard-interactive,password', '-l', username, '--', host]
            if self.stop.is_set():
                return
            self.client = pexpect.spawn('ssh', args, encoding='utf-8', codec_errors='replace', echo=False)
            prompt = r'/admin1->|hpiLO->|iLO->'
            result = self.client.expect([r'(?i)password:', prompt], timeout=25)
            if result == 0:
                self.client.sendline(password)
                self.client.expect(prompt, timeout=25)
            self.client.send('console com2\r' if mode == 'idrac-ssh' else 'vsp\r')
            self.state = 'connected'
            self.message = 'Live serial output. Keyboard input is disabled.'
            previous = ''
            while not self.stop.is_set() and self.client.isalive():
                try:
                    decoded = self.client.read_nonblocking(8192, timeout=0.2)
                except pexpect.TIMEOUT:
                    continue
                except pexpect.EOF:
                    break
                if '\x1b[5n' in previous + decoded:
                    self.client.send('\x1b[0n')
                previous = decoded[-3:]
                with self.lock:
                    self.text += decoded
                    if len(self.text) > LIMIT:
                        self.text = self.text[-LIMIT:]
                        self.truncated = True
            self.state = 'stopped'
            self.message = 'The serial connection ended.'
        except Exception as error:
            self.state = 'error'
            detail = 'The BMC SSH session timed out or closed.' if isinstance(error, (pexpect.TIMEOUT, pexpect.EOF)) else str(error)
            for secret in (password, host):
                if secret:
                    detail = detail.replace(secret, '[redacted]')
            self.message = 'Serial capture failed: ' + type(error).__name__ + ': ' + detail[:200]
        finally:
            if self.client:
                self.client.close(force=True)

    def snapshot(self):
        with self.lock:
            text = ANSI.sub(' ', self.text).replace('\x00', '')
            for secret in self.redactions:
                if secret:
                    text = text.replace(secret, '[redacted]')
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
            return {'state': self.state, 'message': self.message, 'text': text,
                    'truncated': self.truncated, 'retention': 'Last 65536 characters in this live session. A site restart clears the buffer.'}

    def allowed(self):
        if self.profile_key is None:
            return True
        try:
            document = json.loads(Path('/shared/runos/profiles.json').read_text())
            return document['machines'].get(self.profile_key, {}).get('serialConsole') == self.mode
        except (OSError, ValueError, KeyError):
            return False

    def close(self):
        self.stop.set()
        if self.client:
            self.client.close(force=True)
        self.thread.join(timeout=45)
        if self.thread.is_alive():
            raise RuntimeError('The previous serial connection is still closing.')
