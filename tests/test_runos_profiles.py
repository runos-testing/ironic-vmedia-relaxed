import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

try:
    from ironic.drivers.modules.redfish import runos_profiles
except ImportError:
    spec = importlib.util.spec_from_file_location('runos_profiles', Path(__file__).parents[1] / 'module/runos_profiles.py')
    runos_profiles = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runos_profiles)


class ProfilesTest(unittest.TestCase):
    def test_profiles_match_the_management_endpoint_and_system(self):
        info = {'redfish_address': 'https://bmc.example.test',
                'redfish_system_id': '/redfish/v1/Systems/1'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.json'
            path.write_text(json.dumps({'version': 1, 'machines': {
                'bmc.example.test/redfish/v1/Systems/1': {'mediaBaseUrl': 'media.example.test:/images'}
            }}))
            self.assertEqual(runos_profiles.load_profile(info, path)['mediaBaseUrl'], 'media.example.test:/images')
            self.assertIsNone(runos_profiles.load_profile({**info, 'redfish_system_id': '/redfish/v1/Systems/2'}, path))

    def test_absent_document_preserves_unassigned_machine_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(runos_profiles.load_profile({}, Path(directory) / 'absent'))

    def test_invalid_document_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'profiles.json'
            path.write_text('{"version": 2}')
            with self.assertRaisesRegex(ValueError, 'Unsupported'):
                runos_profiles.load_profile({}, path)

    def test_apply_preserves_credentials_and_changes_only_profile_fields(self):
        node = SimpleNamespace(driver_info={'redfish_address': 'https://bmc.example.test', 'redfish_password': 'placeholder'})
        with patch.object(runos_profiles, 'load_profile', return_value={'mediaBaseUrl': 'media.example.test:/images'}):
            runos_profiles.apply_profile(node)
        self.assertEqual(node.driver_info['external_http_url'], 'media.example.test:/images')
        self.assertEqual(node.driver_info['redfish_password'], 'placeholder')

    def test_unassigned_node_retains_its_settings(self):
        node = SimpleNamespace(driver_info={'external_http_url': 'https://existing.example.test'})
        with patch.object(runos_profiles, 'load_profile', return_value=None):
            runos_profiles.apply_profile(node)
            self.assertFalse(runos_profiles.boot_order_enabled(node, False))
        self.assertEqual(node.driver_info['external_http_url'], 'https://existing.example.test')
