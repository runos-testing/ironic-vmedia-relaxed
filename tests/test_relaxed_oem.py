# Copyright 2026 the ironic-vmedia-relaxed authors
# Licensed under the Apache License, Version 2.0
"""Tests for the older-BMC support, run INSIDE the built image.

These run against the real Ironic and sushy in the image, not against mocks of
them, so they fail when an Ironic or sushy update breaks the specific things
this project depends on.

They are deliberately written around the mistakes that were actually made here,
because those are the ones that will be made again:

* hooking only the MissingActionError path, when an iLO 4 lands in
  BadRequestError;
* putting an Oem block in the InsertVirtualMedia body, which the BMC rejects;
* forgetting that BootOnNextServerReset is a separate PATCH.

Stdlib unittest only, so the image needs no test dependency installed.
"""

import json
import os
import types
import unittest

import sushy
from sushy import exceptions as sushy_exc

from ironic.common import exception
from ironic.conf import CONF
from ironic.drivers.modules.redfish import boot as rb
from ironic.drivers.modules.redfish import relaxed_oem

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def load(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return json.load(fh)


class FakeConn(object):
    """Records what would have been sent to the BMC."""

    def __init__(self, post_exc=None, patch_exc=None, gets=None):
        self.posts = []
        self.patches = []
        self.got = []
        self._post_exc = post_exc
        self._patch_exc = patch_exc
        self._gets = gets or {}

    def get(self, path):
        self.got.append(path)
        for key, payload in self._gets.items():
            if path.endswith(key):
                return types.SimpleNamespace(json=lambda p=payload: p)
        raise sushy_exc.ResourceNotFoundError('GET', path, types.SimpleNamespace(
            status_code=404, json=lambda: {}, content=b'', text='nf'))

    def post(self, path, data=None):
        self.posts.append((path, data))
        if self._post_exc:
            raise self._post_exc

    def patch(self, path, data=None):
        self.patches.append((path, data))
        if self._patch_exc:
            raise self._patch_exc


def fake_vmedia(payload, conn=None, media_types=None, inserted=False,
                insert_exc=None, eject_exc=None):
    """A stand-in for a sushy VirtualMedia resource."""
    vm = types.SimpleNamespace()
    vm.json = payload
    vm.path = payload.get('@odata.id', '/redfish/v1/Managers/1/VirtualMedia/2')
    vm.identity = payload.get('Id', '2')
    vm.name = payload.get('Name', 'VirtualMedia')
    vm.media_types = media_types or [sushy.VIRTUAL_MEDIA_CD]
    vm.inserted = inserted
    vm._conn = conn or FakeConn()

    def insert_media(*a, **kw):
        if insert_exc:
            raise insert_exc
    def eject_media(*a, **kw):
        if eject_exc:
            raise eject_exc

    vm.insert_media = insert_media
    vm.eject_media = eject_media
    return vm


def fake_resource(members):
    res = types.SimpleNamespace()
    res.virtual_media = types.SimpleNamespace(get_members=lambda: members)
    return res


def fake_task():
    node = types.SimpleNamespace(
        uuid='00000000-0000-0000-0000-000000000000',
        driver='redfish',
        driver_info={},
        instance_info={},
        properties={'vendor': 'Dell Inc.'})
    node.get_interface = lambda kind: 'redfish-virtual-media'
    # The insert path calls task.driver.boot, so give it the real interface
    # rather than a stub: that keeps these tests honest about upstream code.
    driver = types.SimpleNamespace(boot=rb.RedfishVirtualMediaBoot())
    return types.SimpleNamespace(node=node, driver=driver)


def boot_sources(names, optical_index=None):
    """A Dell BootSources payload. names is the boot order, top first."""
    return {'Attributes': {'UefiBootSeq': [
        {'Index': i, 'Enabled': True, 'Id': 'BIOS.Setup.1-1#UefiBootSeq#%s' % n,
         'Name': n}
        for i, n in enumerate(names)]}}


MANAGERS = {'Members': [{'@odata.id': '/redfish/v1/Managers/iDRAC.Embedded.1'}]}


def bad_request(msg='rejected'):
    return sushy_exc.BadRequestError('PATCH', '/x', types.SimpleNamespace(
        status_code=400, json=lambda: {}, content=b'', text=msg))


class OptionDefaults(unittest.TestCase):
    """The image must be inert until a deployment opts in."""

    def test_both_options_default_false(self):
        self.assertFalse(CONF.redfish.skip_vendor_validation)
        self.assertFalse(CONF.redfish.enable_oem_vmedia_fallback)


class VendorGate(unittest.TestCase):

    def setUp(self):
        self.task = fake_task()
        self.iface = rb.RedfishVirtualMediaBoot()
        self.mgr = types.SimpleNamespace(
            manager_type=sushy.MANAGER_TYPE_BMC,
            firmware_version='2.65.65.65')
        CONF.set_override('skip_vendor_validation', False, group='redfish')

    def test_stock_behaviour_preserved_when_disabled(self):
        # The whole point of the default: an unsupported Dell must still be
        # refused exactly as upstream refuses it.
        self.assertRaises(exception.InvalidParameterValue,
                          self.iface._validate_vendor,
                          self.task, [self.mgr])

    def test_gate_skipped_when_enabled(self):
        CONF.set_override('skip_vendor_validation', True, group='redfish')
        self.assertIsNone(self.iface._validate_vendor(self.task, [self.mgr]))

    def test_supported_firmware_still_passes_when_disabled(self):
        self.mgr.firmware_version = '6.10.00.00'
        self.assertIsNone(self.iface._validate_vendor(self.task, [self.mgr]))

    def test_upstream_signature_unchanged(self):
        # A signature change upstream would break the hook silently.
        import inspect
        sig = inspect.signature(rb.RedfishVirtualMediaBoot._validate_vendor)
        self.assertEqual(['self', 'task', 'managers'], list(sig.parameters))


class OemLookup(unittest.TestCase):

    def setUp(self):
        self.ilo4 = load('ilo4_virtualmedia.json')

    def test_real_ilo4_payload_has_no_standard_action(self):
        # If this ever stops being true the fallback is not needed for it.
        self.assertFalse(self.ilo4.get('Actions'))

    def test_finds_insert_and_eject_on_real_payload(self):
        vm = fake_vmedia(self.ilo4)
        vendor, target = relaxed_oem._oem_action(vm, 'Insert')
        self.assertEqual('Hp', vendor)
        self.assertIn('InsertVirtualMedia', target)
        vendor, target = relaxed_oem._oem_action(vm, 'Eject')
        self.assertEqual('Hp', vendor)
        self.assertIn('EjectVirtualMedia', target)

    def test_ignores_a_device_with_the_standard_action(self):
        vm = fake_vmedia({'Actions': {'#VirtualMedia.InsertMedia': {}}})
        self.assertEqual((None, None), relaxed_oem._oem_action(vm, 'Insert'))

    def test_handles_ilo5_spelling(self):
        payload = {'Oem': {'Hpe': {'Actions': {
            '#HpeiLOVirtualMedia.InsertVirtualMedia': {'target': '/t'}}}}}
        self.assertEqual(('Hpe', '/t'),
                         relaxed_oem._oem_action(fake_vmedia(payload), 'Insert'))


class OemInsertPayload(unittest.TestCase):
    """The exact wire format. This is where the real bug was."""

    def setUp(self):
        CONF.set_override('enable_oem_vmedia_fallback', True, group='redfish')
        self.task = fake_task()
        self.conn = FakeConn()
        self.vm = fake_vmedia(load('ilo4_virtualmedia.json'), conn=self.conn)

    def tearDown(self):
        CONF.set_override('enable_oem_vmedia_fallback', False, group='redfish')

    def test_post_body_carries_image_and_nothing_else(self):
        # An Oem block here is rejected by the BMC with
        # Base.0.10.ActionParameterUnknown: ['InsertVirtualMedia', 'Oem'].
        self.assertTrue(relaxed_oem.insert(self.task, self.vm, 'http://h/b.iso'))
        self.assertEqual(1, len(self.conn.posts))
        _, body = self.conn.posts[0]
        self.assertEqual({'Image': 'http://h/b.iso'}, body)

    def test_boot_on_next_reset_is_a_separate_patch(self):
        relaxed_oem.insert(self.task, self.vm, 'http://h/b.iso')
        self.assertEqual(1, len(self.conn.patches))
        path, body = self.conn.patches[0]
        self.assertEqual(self.vm.path, path)
        self.assertIs(True, body['Oem']['Hp']['BootOnNextServerReset'])

    def test_a_failed_patch_does_not_undo_a_good_insert(self):
        conn = FakeConn(patch_exc=bad_request())
        vm = fake_vmedia(load('ilo4_virtualmedia.json'), conn=conn)
        self.assertTrue(relaxed_oem.insert(self.task, vm, 'http://h/b.iso'))

    def test_a_failed_post_reports_failure(self):
        conn = FakeConn(post_exc=bad_request())
        vm = fake_vmedia(load('ilo4_virtualmedia.json'), conn=conn)
        self.assertFalse(relaxed_oem.insert(self.task, vm, 'http://h/b.iso'))

    def test_disabled_option_sends_nothing(self):
        CONF.set_override('enable_oem_vmedia_fallback', False, group='redfish')
        self.assertFalse(relaxed_oem.insert(self.task, self.vm, 'http://h/b.iso'))
        self.assertEqual([], self.conn.posts)


class InsertHookReachedFromBothPaths(unittest.TestCase):
    """The regression test for the bug that cost the most time.

    An iLO 4 does NOT raise MissingActionError. With no standard action sushy
    falls back to PATCHing the resource, the BMC rejects that, and it arrives
    as BadRequestError. Hooking only the first is not enough.
    """

    def setUp(self):
        CONF.set_override('enable_oem_vmedia_fallback', True, group='redfish')
        self.task = fake_task()

    def tearDown(self):
        CONF.set_override('enable_oem_vmedia_fallback', False, group='redfish')

    def _run(self, exc):
        conn = FakeConn()
        vm = fake_vmedia(load('ilo4_virtualmedia.json'), conn=conn,
                         insert_exc=exc)
        ok = rb._insert_vmedia_in_resource(
            self.task, fake_resource([vm]), 'http://h/b.iso',
            sushy.VIRTUAL_MEDIA_CD, [])
        return ok, conn

    def test_bad_request_falls_back(self):
        ok, conn = self._run(bad_request())
        self.assertTrue(ok, 'BadRequestError must reach the OEM fallback')
        self.assertEqual(1, len(conn.posts))

    def test_missing_action_falls_back(self):
        ok, conn = self._run(sushy_exc.MissingActionError(
            action='#VirtualMedia.InsertMedia', resource='/x'))
        self.assertTrue(ok, 'MissingActionError must reach the OEM fallback')
        self.assertEqual(1, len(conn.posts))

    def test_standard_device_never_reaches_the_fallback(self):
        conn = FakeConn()
        vm = fake_vmedia({'Actions': {'#VirtualMedia.InsertMedia': {}},
                          'Id': '1'}, conn=conn)
        ok = rb._insert_vmedia_in_resource(
            self.task, fake_resource([vm]), 'http://h/b.iso',
            sushy.VIRTUAL_MEDIA_CD, [])
        self.assertTrue(ok)
        self.assertEqual([], conn.posts, 'OEM path used on a standard BMC')


class EjectHook(unittest.TestCase):

    def setUp(self):
        CONF.set_override('enable_oem_vmedia_fallback', True, group='redfish')
        self.task = fake_task()

    def tearDown(self):
        CONF.set_override('enable_oem_vmedia_fallback', False, group='redfish')

    def test_bad_request_falls_back_to_oem_eject(self):
        conn = FakeConn()
        vm = fake_vmedia(load('ilo4_virtualmedia.json'), conn=conn,
                         inserted=True, eject_exc=bad_request())
        self.assertTrue(rb._eject_vmedia_from_resource(
            self.task, fake_resource([vm])))
        self.assertEqual(1, len(conn.posts))
        self.assertIn('EjectVirtualMedia', conn.posts[0][0])

    def test_raises_when_no_oem_action_exists(self):
        conn = FakeConn()
        vm = fake_vmedia({'Id': '1'}, conn=conn, inserted=True,
                         eject_exc=bad_request())
        self.assertRaises(sushy_exc.BadRequestError,
                          rb._eject_vmedia_from_resource,
                          self.task, fake_resource([vm]))


class UpstreamContract(unittest.TestCase):
    """Things upstream could change that would break this project.

    These fail loudly on an Ironic or sushy bump rather than at deploy time.
    """

    def test_ironic_still_exposes_the_hooked_functions(self):
        self.assertTrue(callable(rb._insert_vmedia_in_resource))
        self.assertTrue(callable(rb._eject_vmedia_from_resource))

    def test_hooked_insert_signature_unchanged(self):
        # These tests call it directly, so a new parameter must be noticed
        # here rather than as three confusing errors.
        import inspect
        sig = inspect.signature(rb._insert_vmedia_in_resource)
        self.assertEqual(
            ['task', 'resource', 'boot_url', 'boot_device', 'err_msgs',
             'username', 'password'],
            list(sig.parameters))

    def test_sushy_virtualmedia_still_exposes_json_and_path(self):
        from sushy.resources.manager.virtual_media import VirtualMedia
        self.assertTrue(hasattr(VirtualMedia, 'json'))
        self.assertTrue(hasattr(VirtualMedia, 'path'))

    def test_sushy_still_names_its_connector_conn(self):
        # Private API. A rename would otherwise surface as a failed deploy.
        import inspect
        from sushy.resources import base
        self.assertIn('self._conn',
                      inspect.getsource(base.ResourceBase.__init__))

    def test_both_insert_hooks_are_present_in_source(self):
        import inspect
        src = inspect.getsource(rb._insert_vmedia_in_resource)
        self.assertEqual(2, src.count('relaxed_oem.insert'),
                         'insert needs BOTH the missing-action and '
                         'bad-request hooks')


if __name__ == '__main__':
    unittest.main(verbosity=2)


class BootOrder(unittest.TestCase):
    """Some BMCs accept the boot override, report it, and ignore it.

    The UEFI boot sequence is what actually decides, and an OS install pushes
    the internal disk back on top, so this has to run on every attach.
    """

    DISK_FIRST = ['RAID.Integrated.1-1', 'NIC.Embedded.1-1-1',
                  'Optical.iDRACVirtual.1-1']
    CD_FIRST = ['Optical.iDRACVirtual.1-1', 'RAID.Integrated.1-1',
                'NIC.Embedded.1-1-1']
    # With no media attached the virtual optical entry does not exist at all.
    NO_OPTICAL = ['RAID.Integrated.1-1', 'NIC.Embedded.1-1-1']

    def setUp(self):
        CONF.set_override('enable_oem_boot_order', True, group='redfish')
        self.task = fake_task()
        self.task.node.driver_info = {
            'redfish_system_id': '/redfish/v1/Systems/System.Embedded.1'}

    def tearDown(self):
        CONF.set_override('enable_oem_boot_order', False, group='redfish')

    def _vm(self, names):
        conn = FakeConn(gets={'/BootSources': boot_sources(names),
                              '/redfish/v1/Managers': MANAGERS})
        return fake_vmedia({'Id': '1'}, conn=conn), conn

    def test_disabled_by_default(self):
        CONF.set_override('enable_oem_boot_order', False, group='redfish')
        vm, conn = self._vm(self.DISK_FIRST)
        self.assertFalse(relaxed_oem.ensure_vmedia_first(self.task, vm))
        self.assertEqual([], conn.patches)

    def test_reorders_when_the_disk_is_first(self):
        vm, conn = self._vm(self.DISK_FIRST)
        self.assertTrue(relaxed_oem.ensure_vmedia_first(self.task, vm))
        self.assertEqual(1, len(conn.patches))
        _, body = conn.patches[0]
        order = [e['Name'] for e in body['Attributes']['UefiBootSeq']]
        self.assertEqual('Optical.iDRACVirtual.1-1', order[0])
        # every entry must survive: the firmware rejects a partial list
        self.assertCountEqual(self.DISK_FIRST, order)
        # indices must be renumbered from zero
        self.assertEqual(list(range(len(order))),
                         [e['Index'] for e in body['Attributes']['UefiBootSeq']])

    def test_schedules_a_job_so_the_change_applies(self):
        vm, conn = self._vm(self.DISK_FIRST)
        relaxed_oem.ensure_vmedia_first(self.task, vm)
        self.assertEqual(1, len(conn.posts))
        path, body = conn.posts[0]
        self.assertTrue(path.endswith('/Jobs'))
        self.assertIn('BootSources/Settings', body['TargetSettingsURI'])

    def test_no_change_when_already_first(self):
        vm, conn = self._vm(self.CD_FIRST)
        self.assertTrue(relaxed_oem.ensure_vmedia_first(self.task, vm))
        self.assertEqual([], conn.patches, 'must not rewrite a correct order')

    def test_refuses_when_the_entry_is_absent(self):
        # THE TRAP: with no media attached the entry is gone, and a request
        # naming a missing entry returns 200 while doing nothing. Reporting
        # success there would hide a machine that boots its own disk.
        vm, conn = self._vm(self.NO_OPTICAL)
        self.assertFalse(relaxed_oem.ensure_vmedia_first(self.task, vm))
        self.assertEqual([], conn.patches)

    def test_silent_on_a_bmc_without_bootsources(self):
        conn = FakeConn(gets={'/redfish/v1/Managers': MANAGERS})
        vm = fake_vmedia({'Id': '1'}, conn=conn)
        self.assertFalse(relaxed_oem.ensure_vmedia_first(self.task, vm))

    def test_reports_failure_if_the_job_cannot_be_scheduled(self):
        vm, conn = self._vm(self.DISK_FIRST)
        conn._post_exc = bad_request()
        self.assertFalse(relaxed_oem.ensure_vmedia_first(self.task, vm),
                         'a reorder that cannot be applied is not a success')

    def test_restores_the_disk_when_media_is_ejected(self):
        # THE OTHER HALF OF THE FIX, and the reason it exists: a BMC that only
        # lists the optical device while it is permanently attached needs that
        # setting for the reorder to work at all, but a permanently attached
        # device pinned FIRST means the firmware is offered an empty optical
        # device on every later boot. Observed on real hardware: the machine
        # booted its freshly written image, then stopped booting once the
        # optical device was left pinned first.
        vm, conn = self._vm(self.CD_FIRST)
        self.assertTrue(relaxed_oem.restore_disk_first(self.task, vm))
        _, body = conn.patches[0]
        order = [e['Name'] for e in body['Attributes']['UefiBootSeq']]
        self.assertEqual('Optical.iDRACVirtual.1-1', order[-1])
        self.assertCountEqual(self.CD_FIRST, order)

    def test_restore_is_a_no_op_when_the_disk_is_already_first(self):
        vm, conn = self._vm(self.DISK_FIRST)
        self.assertTrue(relaxed_oem.restore_disk_first(self.task, vm))
        self.assertEqual([], conn.patches)

    def test_restore_respects_the_option(self):
        CONF.set_override('enable_oem_boot_order', False, group='redfish')
        vm, conn = self._vm(self.CD_FIRST)
        self.assertFalse(relaxed_oem.restore_disk_first(self.task, vm))
        self.assertEqual([], conn.patches)

    def test_eject_path_restores_the_order(self):
        import inspect
        self.assertIn('relaxed_oem.restore_disk_first',
                      inspect.getsource(rb._eject_vmedia_from_resource))

    def test_standard_insert_path_also_fixes_the_order(self):
        # The Dell uses the STANDARD insert, so the hook must be on that path
        # too, not only on the OEM one.
        import inspect
        self.assertIn('relaxed_oem.ensure_vmedia_first',
                      inspect.getsource(rb._insert_vmedia_in_resource))
