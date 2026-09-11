# ironic-vmedia-relaxed

An [Ironic](https://docs.openstack.org/ironic/) image that makes the Redfish
virtual-media vendor gate optional, behind a config option that defaults to off.

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

## What this changes

One config option, `[redfish]skip_vendor_validation`, default `false`. While it
is false this image behaves exactly like the stock one. Set it to true and
`_validate_vendor` logs a warning and returns instead of raising.

Nothing else is touched. There is no new boot interface, no new hardware type,
and no change to boot ISO generation, media insertion or ejection, boot device
selection or cleanup. Nodes keep the stock `redfish` driver and the stock
`redfish-virtual-media` boot interface, so anything that drives Ironic keeps
working unchanged, the Bare Metal Operator included.

The complete change is in [`patches/`](patches/), about 25 lines.

## Why this is a patch and not a plugin

Ironic has a supported out-of-tree extension mechanism: setuptools entry points
for hardware types and interfaces. That route was tried first and it does not
work for this, for two independent reasons.

**A subclassed boot interface is rejected by the stock hardware type.** Ironic
compares by exact type identity, not `isinstance`:

```python
supported_impls = getattr(hw_type, 'supported_%s_interfaces' % interface_type)
if type(impl_instance) not in supported_impls:
    raise exception.IncompatibleInterface(...)
```

So `RedfishHardware` rejects a subclass of `RedfishVirtualMediaBoot`, and using
the interface requires shipping a hardware type as well.

**But an out-of-tree hardware type cannot publish images.** Virtual media boot
must publish its boot ISO, and the publisher is selected from a map keyed on the
driver *name*, built as a local variable inside the function:

```python
def update_driver_config(self, driver):
    _SWIFT_MAP = {"redfish": {...}, "idrac": {...}}
    if driver not in _SWIFT_MAP:
        raise exception.UnsupportedDriverExtension(
            _("Publishing images is not supported for driver %s") % driver)
```

The map is rebuilt on every call, so it cannot be extended from outside, and any
hardware type not named `redfish` or `idrac` fails at deploy time with
`Publishing images is not supported for driver <name>`.

Together these mean upstream Ironic cannot support an out-of-tree virtual-media
hardware type at all. Patching one method is the smaller and more honest change,
and it has the practical advantage of leaving nodes on the stock driver.

## Verify before you enable it

This option does not make an incapable BMC capable. It converts a clear early
failure into an obscure one part way through a deploy. Confirm on the actual
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

If the action is absent, or returns `ActionNotSupported`, this image will not
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

### The BMC may accept a boot override and ignore it

Passing the vendor check only gets the media attached. A separate problem on
older hardware is whether the machine actually boots it.

Ironic sets the standard Redfish one-time boot override to `Cd`. Some BMCs
accept that request, report it back, consume it on the next boot, and boot the
internal disk anyway. There is no error. The symptom is an inspection or deploy
that hangs forever while the ramdisk never calls home, and the machine quietly
running whatever was already installed on it.

Two settings can be responsible, and they are not the same one:

- A first-boot-device setting (on Dell, `iDRAC.serverboot.FirstBootDevice`)
  applies to **legacy BIOS** boot. Setting it on a UEFI machine changes nothing,
  and it reports success while doing so.
- In UEFI mode the machine obeys its **UEFI boot sequence**
  (`BIOS.BiosBootSettings.UefiBootSeq` on Dell). After an OS is installed, the
  internal disk sits at the top of that list and the virtual optical device at
  the bottom, so the disk always wins.

To check what the machine will really do:

```bash
racadm get BIOS.BiosBootSettings
# BootMode=Uefi                       <- decides which list below matters
# UefiBootSeq=RAID.Integrated.1-1,...,Optical.iDRACVirtual.1-1
#             ^ disk first                  ^ virtual CD last
```

The fix is to reorder that list so the virtual optical device is first, then
commit it as a BIOS job and let the machine reboot:

```bash
racadm set BIOS.BiosBootSettings.UefiBootSeq Optical.iDRACVirtual.1-1,<the rest>
racadm jobqueue create BIOS.Setup.1-1 -r pwrcycle -s TIME_NOW
```

Leaving the virtual device permanently first is usually what you want on a
machine managed this way: with no media attached the firmware falls through to
the next entry, and with media attached the provisioner wins.

A quick way to tell which of the two happened, without a console: if the host's
existing OS answers on its usual port, it booted the disk. The agent ramdisk
serves its API on TCP 9999, so that port opening is positive proof the ramdisk
booted rather than an absence of evidence.

## Using it

```
ghcr.io/runos-testing/ironic-vmedia-relaxed:latest
```

Pin to a `sha-<commit>` tag in anything you care about. Then enable the option:

```ini
[redfish]
skip_vendor_validation = true
```

Under Metal3, set it through the `Ironic` custom resource:

```yaml
spec:
  images:
    ironic: ghcr.io/runos-testing/ironic-vmedia-relaxed:sha-<commit>
  extraConfig:
    - group: redfish
      name: skip_vendor_validation
      value: "true"
```

Note that the rendered `ironic.conf` is not where this ends up. The Metal3 Ironic
image also honours oslo.config's environment variable support, so the operator
passes the value as `OS_REDFISH__SKIP_VENDOR_VALIDATION` and the config file
still shows the stock content. Check the running service, not the file.

## Compatibility

Built against Ironic 38.0 (`quay.io/metal3-io/ironic:release-38.0`). The patch is
anchored on surrounding source lines, so a base image whose source has moved
fails the build rather than silently producing an unpatched image. Pin the base
image and re-test on Ironic upgrades.

## Licence

Apache 2.0, matching Ironic. The patch modifies Ironic source; see
[openstack/ironic](https://opendev.org/openstack/ironic).
