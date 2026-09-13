"""Request a serial snapshot from the local broker without BMC credentials."""
import json
import socket
import sys

request = json.loads(sys.argv[1])
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(50)
    client.connect('/shared/runos/serial.sock')
    client.sendall(json.dumps(request).encode() + b'\n')
    with client.makefile('rb') as stream:
        print(stream.readline(1048576).decode().strip())
