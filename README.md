# ironic-vmedia-relaxed

An out-of-tree [Ironic](https://docs.openstack.org/ironic/) boot interface that
provides Redfish virtual-media booting **without the vendor firmware gate**.

## The problem

Ironic's stock `redfish-virtual-media` boot interface refuses to operate on any
BMC reporting a Dell vendor string whose firmware major version is not 6 or 7:

```
The redfish-virtual-media boot interface is not suitable for node <uuid>
with vendor Dell Inc. and BMC version <x>, the interface only supports
iDRAC9 versions between 6.0.0.0 and 7.x.x.x.
```

The check is a blanket policy rather than a capability probe. It raises before
anything is attempted, even when the BMC in question implements the standard
Redfish virtual-media actions correctly.

That default is sensible. Older BMCs often implement `VirtualMedia` partially or
not at all, and failing early with a clear message is far better than failing
deep inside a deploy. But it is wrong for *individual* machines that fall outside
the window and nonetheless work.

## What this provides

One additional boot interface, `redfish-virtual-media-relaxed`, which subclasses
the stock one and overrides exactly one method: `_validate_vendor` becomes a
no-op. Boot ISO generation, media insertion and ejection, boot device selection
and cleanup are all inherited unmodified.

No Ironic source is patched. Registration is via setuptools entry points, which
is Ironic's supported out-of-tree extension mechanism.

## Verify before you use it

This interface does not make an incapable BMC capable. Confirm on the actual
hardware that the standard action exists and works:

```bash
# 1. The standard action must be advertised
curl -sk -u "$USER:$PASS" \
  "https://$BMC/redfish/v1/Managers/$MGR/VirtualMedia/CD" \
  | jq '.Actions | keys'
# expect: ["#VirtualMedia.EjectMedia", "#VirtualMedia.InsertMedia"]

# 2. It must actually accept an image
curl -sk -u "$USER:$PASS" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"Image":"http://your-server/boot.iso"}' \
  "https://$BMC/redfish/v1/Managers/$MGR/VirtualMedia/CD/Actions/VirtualMedia.InsertMedia"
# expect: 204, then confirm Inserted == true
```

If the action is absent, or returns `ActionNotSupported`, this interface will not
help. Some vendors expose only an OEM-specific action under a different name, in
which case standard virtual media is genuinely unavailable and the hardware needs
a different boot method.

### The image server must support byte ranges

A BMC mounting an ISO needs random access. It typically issues a `HEAD` first and
looks for `Accept-Ranges: bytes`. A server that cannot serve a `206 Partial
Content` will cause the BMC to abandon the transfer **without ever issuing a
GET**, usually reporting a generic error that looks like an authentication,
licensing or path problem.

Python's `http.server` from the standard library does not support ranges and will
fail this way. Apache, nginx and most object stores are fine.

## Using it

Install the package into an Ironic image, or use the published one:

```
ghcr.io/runos-testing/ironic-vmedia-relaxed:latest
```

Then add the interface to `enabled_boot_interfaces` in `ironic.conf`:

```ini
enabled_boot_interfaces = redfish-virtual-media,redfish-virtual-media-relaxed,ipxe,pxe
```

Under Metal3, set it through the `Ironic` custom resource's `extraConfig`, and
select it per node with `driver_info` / the `BareMetalHost` spec.

## Compatibility

Built against Ironic 38.0 (`quay.io/metal3-io/ironic:release-38.0`). Because it
subclasses a stock interface, it is sensitive to changes in that class. Pin the
base image and re-test on Ironic upgrades.

## Licence

Apache 2.0, matching Ironic. Portions of the behaviour documented here describe
Ironic's own interfaces; see [openstack/ironic](https://opendev.org/openstack/ironic).
