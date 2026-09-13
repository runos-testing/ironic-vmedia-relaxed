"""Read only the BMC secret that the provider enables for serial capture."""
import base64
import json
import os
from pathlib import Path
import re
import ssl
from urllib.request import Request, urlopen


def read_credentials(name):
    if not isinstance(name, str) or not re.fullmatch(r'bm-[a-z0-9-]+-bmc', name):
        raise ValueError('The serial profile has no valid BMC credential reference.')
    auth = Path('/serial-auth')
    namespace = auth.joinpath('namespace').read_text().strip()
    host = os.environ.get('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')
    port = os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')
    request = Request('https://' + host + ':' + port + '/api/v1/namespaces/' + namespace + '/secrets/' + name,
        headers={'Authorization': 'Bearer ' + auth.joinpath('token').read_text().strip()})
    context = ssl.create_default_context(cafile=str(auth / 'ca.crt'))
    with urlopen(request, timeout=10, context=context) as response:
        data = json.load(response)['data']
    return tuple(base64.b64decode(data[key]).decode() for key in ('username', 'password'))
