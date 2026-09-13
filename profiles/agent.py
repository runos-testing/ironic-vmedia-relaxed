"""Publish machine profiles and serve inspection media to assigned BMCs."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
from serial_broker import start_broker

SOURCE = Path('/profiles/profiles.json')
TARGET = Path('/shared/runos/profiles.json')
EXPORTS = Path('/tmp/runos-exports')


def export_clients(document):
    clients = set()
    for endpoint, profile in document.get('machines', {}).items():
        media = profile.get('mediaBaseUrl') or ''
        if media and not media.startswith(('http://', 'https://')):
            host = endpoint.split('/', 1)[0]
            if not re.fullmatch(r'[a-zA-Z0-9.-]+', host):
                raise ValueError('NFS requires a BMC hostname or IPv4 address without a port')
            clients.add(host)
    return sorted(clients)


def main():
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    serial_pool = start_broker()
    Path('/shared/html/redfish').mkdir(parents=True, exist_ok=True)
    os.chown('/shared/html/redfish', 997, 997)
    subprocess.Popen(['rpcbind', '-f'])
    server = None
    previous = None
    while True:
        try:
            serial_pool.reap()
            raw = SOURCE.read_text() if SOURCE.exists() else '{"version":1,"machines":{}}'
            document = json.loads(raw)
            clients = export_clients(document)
            if raw != previous:
                pending = TARGET.with_suffix('.tmp')
                pending.write_text(raw)
                pending.replace(TARGET)
                EXPORTS.write_text('/shared/html/redfish ' + ' '.join(host + '(ro)' for host in clients) + '\n' if clients else '')
                if server and server.poll() is None:
                    server.terminate()
                    server.wait(timeout=10)
                server = None
                previous = raw
                print('Published machine profiles; NFS clients:', len(clients), flush=True)
            if clients and (server is None or server.poll() is not None):
                server = subprocess.Popen(['unfsd', '-d', '-e', str(EXPORTS), '-l',
                    os.environ.get('PROVISIONING_IP', '0.0.0.0'), '-n', '2049', '-m', '20048'])
        except Exception as error:
            print('Profile distribution failed:', type(error).__name__, flush=True)
        time.sleep(2)


if __name__ == '__main__':
    main()
