"""Serve serial requests through a local socket accessible to the site agent."""
import json
import os
from pathlib import Path
import re
import socketserver
import threading

from serial_capture import SerialCapture
from serial_pool import SerialPool

SOCKET = '/shared/runos/serial.sock'
POOL = SerialPool(SerialCapture)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            request = json.loads(self.rfile.readline(8192))
            node, epoch, action = request['node'], request['epoch'], request['action']
            if not re.fullmatch(r'[a-f0-9-]{36}', node) or not isinstance(epoch, str) or len(epoch) > 200:
                raise ValueError('Invalid serial target')
            if action not in ('start', 'read', 'stop'):
                raise ValueError('Unsupported serial action')
            response = POOL.request(node, epoch, action)
        except Exception as error:
            response = {'state': 'error', 'message': type(error).__name__, 'text': ''}
        self.wfile.write(json.dumps(response).encode() + b'\n')


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def start_broker():
    Path(SOCKET).parent.mkdir(parents=True, exist_ok=True)
    Path(SOCKET).unlink(missing_ok=True)
    server = Server(SOCKET, Handler)
    os.chmod(SOCKET, 0o600)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return POOL
