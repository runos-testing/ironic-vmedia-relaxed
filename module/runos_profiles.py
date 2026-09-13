"""Load provider-owned provisioning settings without changing unassigned nodes."""

import json
from pathlib import Path
from urllib.parse import urlsplit

PROFILE_PATH = Path('/shared/runos/profiles.json')


def profile_key(driver_info):
    address = urlsplit(driver_info.get('redfish_address', ''))
    system = driver_info.get('redfish_system_id', '')
    return address.netloc.lower() + '/' + system.lstrip('/')


def load_profile(driver_info, path=PROFILE_PATH):
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(document, dict) or document.get('version') != 1:
        raise ValueError('Unsupported RunOS provisioning profile document')
    profiles = document.get('machines')
    if not isinstance(profiles, dict):
        raise ValueError('RunOS provisioning profiles require a machine map')
    profile = profiles.get(profile_key(driver_info))
    if profile is not None and not isinstance(profile, dict):
        raise ValueError('Invalid RunOS machine provisioning profile')
    return profile


def apply_profile(node):
    profile = load_profile(node.driver_info)
    if profile is None:
        return
    info = dict(node.driver_info)
    base_url = profile.get('mediaBaseUrl')
    if base_url:
        if not isinstance(base_url, str):
            raise ValueError('RunOS media base URL must be a string')
        info['external_http_url'] = base_url
    else:
        info.pop('external_http_url', None)
    node.driver_info = info


def boot_order_enabled(node, default):
    profile = load_profile(node.driver_info)
    if profile is None or 'oemBootOrder' not in profile:
        return default
    value = profile['oemBootOrder']
    if not isinstance(value, bool):
        raise ValueError('RunOS OEM boot order setting must be boolean')
    return value
