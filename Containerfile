# Ironic with the Redfish virtual-media vendor gate made optional.
#
# The stock redfish-virtual-media boot interface refuses any BMC reporting a
# Dell vendor string whose firmware major version is not 6 or 7. This image
# adds a config option, [redfish]skip_vendor_validation, which defaults to
# false and therefore changes NOTHING until a deployment opts in.
#
# The change is applied to the installed package rather than registered as an
# out-of-tree plugin. That is not a shortcut, it is forced: see README.md,
# "Why this is a patch and not a plugin".
ARG IRONIC_IMAGE=quay.io/metal3-io/ironic:release-38.0

# ---- builder: apply the patch -----------------------------------------------
# The base image ships no patch(1), and installing one into the image we ship
# would add a package for no runtime reason. So patch in a throwaway stage and
# carry only the resulting .py files across.
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

# ---- final: stock image plus the two patched files --------------------------
FROM ${IRONIC_IMAGE}
USER root
ARG SITE_PACKAGES=/usr/lib/python3.12/site-packages

COPY --from=patcher ${SITE_PACKAGES}/ironic/conf/redfish.py \
                    ${SITE_PACKAGES}/ironic/conf/redfish.py
COPY --from=patcher ${SITE_PACKAGES}/ironic/drivers/modules/redfish/boot.py \
                    ${SITE_PACKAGES}/ironic/drivers/modules/redfish/boot.py

# Drop the stale bytecode the base image built from the unpatched source, then
# rebuild it, so the running service cannot load a cached unpatched module.
RUN find "${SITE_PACKAGES}/ironic" -name '__pycache__' -prune -exec rm -rf {} + \
 && python3.12 -m compileall -q \
        "${SITE_PACKAGES}/ironic/conf/redfish.py" \
        "${SITE_PACKAGES}/ironic/drivers/modules/redfish/boot.py"

# Prove the option is registered, defaults to false, and that the gate actually
# reads it. A build that applied the patch but produced an inert option is
# worse than no build at all.
RUN python3.12 -c "\
import inspect; \
from ironic.conf import CONF; \
from ironic.drivers.modules.redfish import boot; \
assert CONF.redfish.skip_vendor_validation is False, 'skip_vendor_validation missing or wrong default'; \
assert CONF.redfish.enable_oem_vmedia_fallback is False, 'enable_oem_vmedia_fallback missing or wrong default'; \
src = inspect.getsource(boot.RedfishVirtualMediaBoot._validate_vendor); \
assert 'skip_vendor_validation' in src, 'vendor gate does not read its option'; \
assert hasattr(boot, '_oem_insert_vmedia'), 'OEM insert helper missing'; \
assert hasattr(boot, '_oem_eject_vmedia'), 'OEM eject helper missing'; \
assert '_oem_insert_vmedia' in inspect.getsource(boot._insert_vmedia_in_resource), 'insert path does not call the OEM fallback'; \
assert '_oem_eject_vmedia' in inspect.getsource(boot._eject_vmedia_from_resource), 'eject path does not call the OEM fallback'; \
print('OK: both options registered and defaulting False, both fallbacks wired in')"
