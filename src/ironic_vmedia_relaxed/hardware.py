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
"""A Redfish hardware type that accepts the relaxed virtual-media interface.

WHY THIS IS NEEDED AS WELL AS THE BOOT INTERFACE
------------------------------------------------
Enabling a boot interface in ``enabled_boot_interfaces`` is only half of what
Ironic requires. Each hardware type publishes a fixed list of interface classes
it will accept, and ``RedfishHardware.supported_boot_interfaces`` names the
stock classes explicitly. A node using hardware type ``redfish`` therefore
rejects any interface absent from that list, even one the conductor has loaded:

    boot interface implementation '<RelaxedRedfishVirtualMediaBoot object>'
    is not supported by hardware type RedfishHardware.

So the relaxed interface additionally needs a hardware type that lists it. This
class subclasses the stock one and appends exactly that, changing nothing else.

ORDERING
--------
The stock list is returned first and the relaxed interface is appended last.
Ironic picks the default interface as the first enabled entry of that list, so
appending keeps the default behaviour of a ``redfish-relaxed`` node identical
to a ``redfish`` node. Selecting the relaxed interface stays a deliberate,
explicit act by the operator.
"""

from ironic.drivers import redfish as redfish_hardware

from ironic_vmedia_relaxed import boot as relaxed_boot


class RelaxedRedfishHardware(redfish_hardware.RedfishHardware):
    """Redfish hardware type that also accepts the relaxed boot interface.

    Identical to the stock ``redfish`` hardware type in every respect except
    that ``redfish-virtual-media-relaxed`` may be selected on its nodes.
    """

    @property
    def supported_boot_interfaces(self):
        """Stock boot interfaces, plus the relaxed virtual media one."""
        return (super().supported_boot_interfaces
                + [relaxed_boot.RelaxedRedfishVirtualMediaBoot])
