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
"""A Redfish virtual-media boot interface without the vendor firmware gate.

WHY THIS EXISTS
---------------
Ironic's stock ``redfish-virtual-media`` boot interface refuses to operate on
any BMC reporting a Dell vendor string unless its firmware major version is 6
or 7 (iDRAC9). The check lives in
``ironic.drivers.modules.redfish.boot.RedfishVirtualMediaBoot._validate_vendor``
and raises ``InvalidParameterValue`` before anything is attempted.

That gate is a blanket policy, not a capability probe. It is a reasonable
default: older BMCs frequently implement the standard Redfish VirtualMedia
actions partially or not at all, and failing early with a clear message beats
failing deep inside a deploy.

It is nonetheless wrong for *some* individual machines. A BMC outside the
supported window may still implement the standard
``#VirtualMedia.InsertMedia`` action correctly, including an HTTP image URI.
Where that has been verified on the specific hardware in question, this
interface allows the operator to proceed.

WHAT IT CHANGES
---------------
Exactly one thing: ``_validate_vendor`` becomes a no-op. Every other behaviour,
including boot ISO generation, media insertion and ejection, boot device
selection and cleanup, is inherited unmodified from the stock interface.

WHEN NOT TO USE IT
------------------
Do not reach for this to work around a BMC that genuinely cannot do virtual
media. Verify first, against the actual machine, that the standard action works:

    GET  /redfish/v1/Managers/<id>/VirtualMedia/<device>
         -> Actions must list #VirtualMedia.InsertMedia

    POST /redfish/v1/Managers/<id>/VirtualMedia/<device>/Actions/
         VirtualMedia.InsertMedia   {"Image": "http://..."}
         -> expect 204, then confirm Inserted == true

If the action is absent, or returns ActionNotSupported, this interface will not
help and the hardware needs a different boot method.

Note also that the BMC must support HTTP byte ranges on the image URL. A server
that answers a HEAD without ``Accept-Ranges: bytes`` will cause some BMCs to
abandon the transfer with a generic, misleading error and never issue a GET.
"""

from oslo_log import log

from ironic.drivers.modules.redfish import boot as redfish_boot

LOG = log.getLogger(__name__)


class RelaxedRedfishVirtualMediaBoot(redfish_boot.RedfishVirtualMediaBoot):
    """Redfish virtual media boot with the vendor firmware gate removed.

    Identical to the stock ``redfish-virtual-media`` interface in every respect
    except that it does not refuse BMCs on the basis of vendor and firmware
    version. Intended for hardware where standard Redfish virtual media has
    been verified to work despite falling outside the stock support window.
    """

    def _validate_vendor(self, task, managers):
        """Skip the vendor and firmware gate.

        The stock implementation raises InvalidParameterValue for Dell BMCs
        whose firmware major version is not 6 or 7. Operators selecting this
        interface are asserting they have verified standard virtual media on
        the target hardware, so the gate is intentionally not applied.
        """
        LOG.debug(
            "Vendor firmware gate skipped for node %s: the relaxed virtual "
            "media boot interface is in use.", task.node.uuid)
