# Copyright 2026 the ironic-vmedia-relaxed authors
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
"""Support for BMCs that predate the parts of Redfish Ironic relies on.

This module is COPIED into Ironic's package rather than patched into an
existing file, so that upstream churn cannot conflict with it. The patch that
accompanies it only adds small call sites, which keeps rebasing onto a new
Ironic release cheap.

It owns two things:

* the two config options, registered here so the stock ``ironic/conf`` files
  need no patching at all;
* the OEM virtual media fallback used when a BMC implements no standard
  ``#VirtualMedia.InsertMedia`` action.
"""

from oslo_config import cfg
from oslo_log import log
import sushy

from ironic.common.i18n import _
from ironic.conf import CONF

LOG = log.getLogger(__name__)

OPTS = [
    cfg.BoolOpt(
        'skip_vendor_validation',
        default=False,
        help=_('Skip the vendor and firmware check that the '
               'redfish-virtual-media boot interface performs before it will '
               'operate on a node. By default that interface refuses any BMC '
               'reporting a Dell vendor string whose firmware major version '
               'is not 6 or 7. The check is a blanket policy rather than a '
               'capability probe, so it also rejects BMCs that implement the '
               'standard VirtualMedia.InsertMedia action correctly. Set this '
               'to true ONLY where standard Redfish virtual media has been '
               'verified on the hardware in question. It does not make an '
               'incapable BMC capable, and enabling it blindly turns a clear '
               'early failure into an obscure one part way through a '
               'deployment.')),
    cfg.BoolOpt(
        'enable_oem_vmedia_fallback',
        default=False,
        help=_('Allow virtual media to be inserted and ejected with a vendor '
               'OEM action when the BMC does not implement the standard '
               'VirtualMedia.InsertMedia action at all. Some older BMCs, '
               'notably HPE iLO 4, advertise no standard action and expose an '
               'OEM one instead, which the stock code skips, leaving no '
               'usable device. Only HPE iLO (Hp and Hpe) OEM actions are '
               'attempted. This has no effect on a BMC that implements the '
               'standard action, because the fallback is reached only when '
               'the standard action is missing.')),
]

# Registered on import. boot.py imports this module, and that happens while the
# driver loads, which is before any option is read.
CONF.register_opts(OPTS, group='redfish')

# HPE spells its OEM block Hp on iLO 4 and Hpe on iLO 5.
_OEM_VENDORS = ('Hp', 'Hpe')


def skip_vendor_validation():
    """Whether the vendor and firmware gate should be bypassed."""
    return CONF.redfish.skip_vendor_validation


def _oem_action(v_media, verb):
    """Find a vendor OEM virtual media action on a device.

    :param v_media: a sushy VirtualMedia resource.
    :param verb: either 'Insert' or 'Eject'.
    :returns: a (vendor, target_uri) tuple, or (None, None) if not found.
    """
    oem = v_media.json.get('Oem') or {}
    for vendor in _OEM_VENDORS:
        actions = (oem.get(vendor) or {}).get('Actions') or {}
        key = '#%siLOVirtualMedia.%sVirtualMedia' % (vendor, verb)
        target = (actions.get(key) or {}).get('target')
        if target:
            return vendor, target
    return None, None


def insert(task, v_media, boot_url):
    """Insert virtual media using a vendor OEM action.

    Reached only when the standard action is absent, so it cannot change
    behaviour on a BMC that implements it.

    :returns: True if an OEM action was found and accepted, else False.
    """
    if not CONF.redfish.enable_oem_vmedia_fallback:
        return False

    vendor, target = _oem_action(v_media, 'Insert')
    if not target:
        return False

    # NOTE: the action takes Image and NOTHING else. An Oem block in the POST
    # body is rejected with
    #   Base.0.10.ActionParameterUnknown: ['InsertVirtualMedia', 'Oem']
    try:
        v_media._conn.post(target, data={'Image': boot_url})
    except sushy.exceptions.SushyError as exc:
        LOG.warning('OEM virtual media insert via %(vendor)s failed for node '
                    '%(node)s: %(exc)s',
                    {'vendor': vendor, 'node': task.node.uuid, 'exc': exc})
        return False

    # BootOnNextServerReset is the iLO equivalent of a one-time boot override,
    # and it is a PROPERTY set by a separate PATCH, not a parameter of the
    # action above. It matters because a BMC lacking the standard InsertMedia
    # action is also unlikely to honour the standard Boot override.
    #
    # Best effort: the media is attached by now, so a failure here is worth a
    # warning but must not undo a successful insert.
    try:
        v_media._conn.patch(
            v_media.path,
            data={'Oem': {vendor: {'BootOnNextServerReset': True}}})
    except sushy.exceptions.SushyError as exc:
        LOG.warning('Could not set BootOnNextServerReset via %(vendor)s for '
                    'node %(node)s, the media is attached but the machine may '
                    'not boot it: %(exc)s',
                    {'vendor': vendor, 'node': task.node.uuid, 'exc': exc})

    LOG.info('Inserted boot media into %(slot)s on node %(node)s using the '
             '%(vendor)s OEM action, because this BMC does not implement the '
             'standard InsertMedia action.',
             {'slot': v_media.identity, 'node': task.node.uuid,
              'vendor': vendor})
    return True


def eject(task, v_media):
    """Eject virtual media using a vendor OEM action.

    :returns: True if an OEM action was found and accepted, else False.
    """
    if not CONF.redfish.enable_oem_vmedia_fallback:
        return False

    vendor, target = _oem_action(v_media, 'Eject')
    if not target:
        return False

    try:
        v_media._conn.post(target, data={})
    except sushy.exceptions.SushyError as exc:
        LOG.warning('OEM virtual media eject via %(vendor)s failed for node '
                    '%(node)s: %(exc)s',
                    {'vendor': vendor, 'node': task.node.uuid, 'exc': exc})
        return False

    LOG.info('Ejected boot media from %(slot)s on node %(node)s using the '
             '%(vendor)s OEM action.',
             {'slot': v_media.identity, 'node': task.node.uuid,
              'vendor': vendor})
    return True
