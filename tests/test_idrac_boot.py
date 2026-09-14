"""Verify immediate boot selection and staged controller authentication."""
import unittest
import inspect
from unittest.mock import Mock, patch

from ironic.drivers.modules.redfish import idrac_boot


class AuthenticationTest(unittest.TestCase):
    def test_partial_password_auth_completes_keyboard_interactive(self):
        transport = Mock()
        transport.auth_password.return_value = ['publickey', 'keyboard-interactive']
        transport.is_authenticated.side_effect = [False, True]
        idrac_boot.authenticate(transport, 'operator', 'placeholder')
        transport.auth_interactive.assert_called_once()
        handler = transport.auth_interactive.call_args.args[1]
        self.assertEqual(handler('', '', [('Password:', False)]), ['placeholder'])
        self.assertEqual(handler('', 'Authentication notice', []), [])

    def test_complete_password_auth_needs_no_second_stage(self):
        transport = Mock()
        transport.is_authenticated.return_value = True
        idrac_boot.authenticate(transport, 'operator', 'placeholder')
        transport.auth_interactive.assert_not_called()

    def test_partial_auth_without_completion_fails(self):
        transport = Mock()
        transport.auth_password.return_value = ['keyboard-interactive']
        transport.is_authenticated.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'authentication'):
            idrac_boot.authenticate(transport, 'operator', 'placeholder')

    def test_interactive_handler_refuses_unexpected_prompts(self):
        transport = Mock()
        transport.auth_password.return_value = ['keyboard-interactive']
        transport.is_authenticated.side_effect = [False, True]
        idrac_boot.authenticate(transport, 'operator', 'placeholder')
        handler = transport.auth_interactive.call_args.args[1]
        with self.assertRaisesRegex(RuntimeError, 'prompt'):
            handler('', '', [('OTP:', False)])


class BootSelectionTest(unittest.TestCase):
    def test_commands_arm_virtual_cd_and_verify_both_values(self):
        run = Mock(side_effect=['Object value modified successfully',
                                'Object value modified successfully',
                                'FirstBootDevice=VCD-DVD\nBootOnce=Enabled'])
        idrac_boot.arm(run)
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            'racadm set iDRAC.serverboot.FirstBootDevice VCD-DVD',
            'racadm set iDRAC.serverboot.BootOnce Enabled',
            'racadm get iDRAC.serverboot'])

    def test_successful_write_with_missing_effect_fails(self):
        run = Mock(side_effect=['success', 'success', 'FirstBootDevice=HDD\nBootOnce=Enabled'])
        with self.assertRaisesRegex(RuntimeError, 'readback'):
            idrac_boot.arm(run)

    def test_vendor_error_stops_before_next_command(self):
        run = Mock(return_value='ERROR: RAC1234: Cannot set property')
        with self.assertRaisesRegex(RuntimeError, 'rejected'):
            idrac_boot.arm(run)
        self.assertEqual(run.call_count, 1)


class HookTest(unittest.TestCase):
    def test_disabled_option_does_not_connect(self):
        from ironic.drivers.modules.redfish import relaxed_oem
        task = Mock()
        with patch.object(relaxed_oem.runos_profiles, 'load_profile', return_value=None), patch.object(
                idrac_boot, 'set_one_time_boot') as connect:
            relaxed_oem.arm_idrac_boot(task)
        connect.assert_not_called()

    def test_profile_enables_only_selected_machine(self):
        from ironic.drivers.modules.redfish import relaxed_oem
        task = Mock()
        with patch.object(relaxed_oem.runos_profiles, 'load_profile', return_value={'idracOneTimeBoot': True}), patch.object(
                idrac_boot, 'set_one_time_boot') as connect:
            relaxed_oem.arm_idrac_boot(task)
        connect.assert_called_once_with(task.node.driver_info)

    def test_failure_does_not_expose_credentials(self):
        from ironic.drivers.modules.redfish import relaxed_oem
        task = Mock()
        with patch.object(relaxed_oem.runos_profiles, 'load_profile', return_value={'idracOneTimeBoot': True}), patch.object(
                idrac_boot, 'set_one_time_boot', side_effect=RuntimeError('private credential')):
            with self.assertRaises(Exception) as raised:
                relaxed_oem.arm_idrac_boot(task)
        self.assertNotIn('private credential', str(raised.exception))

    def test_hook_follows_insert_and_standard_override(self):
        from ironic.drivers.modules.redfish import boot
        source = inspect.getsource(boot.RedfishVirtualMediaBoot.prepare_ramdisk)
        self.assertLess(source.index('_insert_vmedia(task'), source.index('relaxed_oem.arm_idrac_boot(task)'))
        self.assertLess(source.index('self._set_boot_device(task'), source.index('relaxed_oem.arm_idrac_boot(task)'))


if __name__ == '__main__':
    unittest.main()
