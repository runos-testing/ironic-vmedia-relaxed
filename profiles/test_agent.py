import unittest
from unittest.mock import patch
from agent import export_clients, ensure_rpcbind


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


class RpcbindTests(unittest.TestCase):
    @patch('agent.subprocess.Popen')
    @patch('agent.socket.create_connection')
    def test_reuses_the_host_portmapper(self, connect, spawn):
        ensure_rpcbind()
        spawn.assert_not_called()
        connect.assert_called_once_with(('127.0.0.1', 111), timeout=2)

    @patch('agent.subprocess.Popen')
    @patch('agent.socket.create_connection', side_effect=OSError)
    def test_starts_a_portmapper_when_the_host_has_none(self, connect, spawn):
        ensure_rpcbind()
        spawn.assert_called_once_with(['rpcbind', '-f'])
