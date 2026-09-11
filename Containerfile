# Ironic with the relaxed virtual-media boot interface added.
#
# Adds one out-of-tree boot interface via setuptools entry points. No Ironic
# source is patched and no upstream driver is reinstated; the stock interfaces
# remain exactly as shipped.
ARG IRONIC_IMAGE=quay.io/metal3-io/ironic:release-38.0
FROM ${IRONIC_IMAGE}

USER root
COPY . /tmp/ironic-vmedia-relaxed
RUN pip3 install --no-cache-dir /tmp/ironic-vmedia-relaxed \
    && rm -rf /tmp/ironic-vmedia-relaxed

# Enabling the interface is deployment configuration, not a build concern.
# Add "redfish-virtual-media-relaxed" to enabled_boot_interfaces in ironic.conf,
# for example through the Ironic CR's extraConfig when running under Metal3.
