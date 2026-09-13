import unittest
from agent import export_clients


class ExportTests(unittest.TestCase):
    def test_only_nfs_profile_clients_can_read_media(self):
        self.assertEqual(export_clients({'machines': {
            'bmc-a.example.test/redfish/v1/Systems/1': {'mediaBaseUrl': 'media.example.test:/images'},
            'bmc-b.example.test/redfish/v1/Systems/1': {'mediaBaseUrl': 'https://media.example.test/images'},
        }}), ['bmc-a.example.test'])

    def test_empty_profiles_export_nothing(self):
        self.assertEqual(export_clients({'machines': {}}), [])

    def test_export_syntax_cannot_be_injected(self):
        with self.assertRaises(ValueError):
            export_clients({'machines': {'*(rw)/system': {'mediaBaseUrl': 'media:/images'}}})
