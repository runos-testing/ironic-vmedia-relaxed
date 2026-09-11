# Ironic with support for BMCs that predate the parts of Redfish it relies on.
#
# Two config options, both default false, so this image behaves exactly like
# the stock one until a deployment opts in:
#
#   [redfish]skip_vendor_validation      the Dell firmware gate stops firing
#   [redfish]enable_oem_vmedia_fallback  use an HPE OEM virtual media action
#                                        when there is no standard one
#
# Structured for cheap rebasing onto a new Ironic release: all the logic lives
# in a module that is COPIED in, and the patch only adds call sites.
ARG IRONIC_IMAGE=quay.io/metal3-io/ironic:release-38.0

# ---- builder: apply the patch -----------------------------------------------
# The base image ships no patch(1) and adding one to the image we ship would be
# a package carried for no runtime reason, so patch in a throwaway stage.
FROM ${IRONIC_IMAGE} AS patcher
USER root
ARG SITE_PACKAGES=/usr/lib/python3.12/site-packages
COPY patches/ /tmp/patches/

# --forward makes re-application a no-op rather than an interactive prompt.
# The build still FAILS on a patch that does not apply, which is the point: a
# base image whose source has moved must not silently produce an unpatched
# build.
RUN microdnf install -y patch \
 && patch -p1 --forward --batch -d "${SITE_PACKAGES}" \
        < /tmp/patches/0001-redfish-older-bmc-support.patch

# ---- final: stock image, our module, and the patched call sites -------------
FROM ${IRONIC_IMAGE}
USER root
ARG SITE_PACKAGES=/usr/lib/python3.12/site-packages

# All the logic. Copied, not patched, so upstream churn cannot conflict with it.
COPY module/ironic_relaxed_oem.py \
     ${SITE_PACKAGES}/ironic/drivers/modules/redfish/relaxed_oem.py

# Only the call sites.
COPY --from=patcher ${SITE_PACKAGES}/ironic/drivers/modules/redfish/boot.py \
                    ${SITE_PACKAGES}/ironic/drivers/modules/redfish/boot.py

# Drop the stale bytecode the base image built from the unpatched source, then
# rebuild it, so the running service cannot load a cached unpatched module.
RUN find "${SITE_PACKAGES}/ironic" -name '__pycache__' -prune -exec rm -rf {} + \
 && python3.12 -m compileall -q \
        "${SITE_PACKAGES}/ironic/drivers/modules/redfish/boot.py" \
        "${SITE_PACKAGES}/ironic/drivers/modules/redfish/relaxed_oem.py"

# Run the test suite against the assembled image. These tests exercise the real
# Ironic and sushy in this image, so an upstream change that breaks what this
# project depends on fails the BUILD rather than a deployment. The tests are
# removed in the same layer, so they are not carried in the published image.
COPY tests/ /tmp/tests/
RUN cd /tmp/tests && python3.12 -m unittest discover -s . -t . -v \
 && rm -rf /tmp/tests

# A last belt-and-braces check on the assembled image. The suite above covers
# behaviour; this covers "did the patch actually land".
RUN python3.12 -c "\
import inspect; \
from ironic.conf import CONF; \
from ironic.drivers.modules.redfish import boot, relaxed_oem; \
from sushy.resources.manager.virtual_media import VirtualMedia as VM; \
assert CONF.redfish.skip_vendor_validation is False, 'skip_vendor_validation missing or wrong default'; \
assert CONF.redfish.enable_oem_vmedia_fallback is False, 'enable_oem_vmedia_fallback missing or wrong default'; \
assert CONF.redfish.enable_oem_boot_order is False, 'enable_oem_boot_order missing or wrong default'; \
assert 'relaxed_oem' in inspect.getsource(boot.RedfishVirtualMediaBoot._validate_vendor), 'vendor gate not hooked'; \
assert inspect.getsource(boot._insert_vmedia_in_resource).count('relaxed_oem.insert') == 2, 'insert needs BOTH the MissingAction and BadRequest hooks'; \
assert 'relaxed_oem.eject' in inspect.getsource(boot._eject_vmedia_from_resource), 'eject not hooked'; \
assert 'relaxed_oem.ensure_vmedia_first' in inspect.getsource(boot._insert_vmedia_in_resource), 'boot order not hooked on the standard insert path'; \
assert hasattr(VM, 'path') and hasattr(VM, 'json'), 'sushy VirtualMedia lost path/json'; \
assert '_conn' in inspect.getsource(__import__('sushy.resources.base', fromlist=['x']).ResourceBase.__init__), 'sushy renamed the private connector attribute this module uses'; \
print('OK: options registered, all three hooks in place, sushy API as expected')"
