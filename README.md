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

Four config options, all `false` by default. While they are all false this
image behaves exactly like the stock one.

### `[redfish]skip_vendor_validation`

Set it to true and `_validate_vendor` logs a warning and returns instead of
raising. That is the whole change: the gate described above stops firing.

### `[redfish]enable_oem_vmedia_fallback`

For the harder case, a BMC that does not implement the standard
`#VirtualMedia.InsertMedia` action **at all**. HPE iLO 4 is the example: it
reports `"Actions": {}` on every virtual media device and exposes an OEM action
under `Oem/Hp` instead.

The stock code treats a missing standard action as "this slot cannot do remote
media" and moves to the next device. On such a BMC every device is skipped, so
nothing is ever attached and the failure looks like a machine with no virtual
media at all.

With this option on, the insert and eject paths try the vendor OEM action
before giving up. Only HPE iLO actions are attempted (`Oem/Hp` and `Oem/Hpe`).
The insert also sets `BootOnNextServerReset`, which is the iLO equivalent of a
one-time boot override, because a BMC missing the standard action is unlikely
to honour the standard `Boot` override either.

**This fallback is reached only when the standard action is missing**, so it
cannot change behaviour on a BMC that implements it.

### `[redfish]force_persistent_boot_on_vmedia`

For a BMC that accepts the one-time boot override, reports it back, and then
clears it before the machine boots. The machine boots its internal disk, the
agent never runs, and the failure is a timeout saying the inspection ramdisk is
not running, which is true and tells nobody why.

With this option on, the code writes `force_persistent_boot_device = Always`
into the node's `driver_info` while media is attached, and removes it again on
eject. Ironic already reads that key in `conductor/utils.py` and honours exactly
`Always`, so this asks through the documented mechanism rather than overriding
the decision. Ironic still chooses; the option only supplies the input it looks
for.

Two things about it are load-bearing:

- **It is requested wherever the insert succeeds, not only in the OEM
  fallback.** A BMC that implements the standard `InsertMedia` action never
  reaches the fallback, and one such BMC is exactly the hardware that needs
  this. MEASURED on an iDRAC 7: with the request made only on the fallback path
  the override read back `Once/Cd` and then `Once/None`, never `Continuous`.
- **The key is withdrawn on eject, on both paths.** Left behind it would make
  every later boot device change persistent too, including the one that points a
  finished machine at its disk. That is a behaviour change nobody asked for and
  nobody would think to look for.

Failing to write the key is a warning, never a failure: the media is attached by
then, and a node that cannot be saved is not a reason to undo an insert that
worked.

### `[redfish]enable_oem_boot_order`

For a third failure, which is separate from both of the above and bites even a
BMC whose virtual media works perfectly.

Ironic sets the standard Redfish one-time boot override to `Cd`. Some BMCs
accept that, report it back, consume it on the next boot, and boot the internal
disk anyway. **There is no error.** The symptom is a deploy or inspection that
hangs forever while the ramdisk never calls home, and the machine quietly
running whatever was already installed on it.

On those machines the vendor UEFI boot sequence is what actually decides. With
this option on, the code moves the virtual optical device to the top of that
sequence after attaching media, and schedules the configuration job that
applies it on the boot Ironic is about to perform anyway.

Two things make this subtler than it looks, and both are enforced in code and
covered by tests:

- **It is not a one-time fix.** Installing an OS makes the firmware re-enumerate
  and push the internal disk back to the top, so the order must be re-asserted
  before *every* deployment. Measured on real hardware: the first deploy worked,
  the second booted the freshly installed disk instead.
- **The virtual optical entry exists only while media is attached.** With
  nothing attached the entry is absent, and a request naming a missing entry
  returns `200` and changes nothing. The code therefore runs after the insert,
  and refuses to report success when the entry is not there, rather than
  silently leaving a machine that boots its own disk.

The top of the boot order is held ONLY while the media is attached: the eject
path puts the virtual optical device back at the bottom. That pairing is not
tidiness, it is the other half of the fix, and skipping it produces a machine
that deploys perfectly and then never boots again. See the prerequisite below.

Currently implements the Dell `BootSources` scheme, and does nothing on a BMC
that does not expose it.

#### Prerequisite: the virtual device must be permanently attached

This option cannot help if the BMC never presents the virtual device to the
host, because then it is not a boot option to reorder. On Dell, check:

```sh
racadm get idrac.virtualmedia
# Attached=AutoAttach   <- the virtual CD is presented only transiently
```

In that mode the symptoms are quietly misleading:

- Redfish reports `Inserted: True`, so the media looks attached;
- the UEFI boot sequence contains **no** virtual optical entry at all;
- a reorder naming that entry returns `200` and changes nothing.

Set it once per machine, and it survives OS installs:

```sh
racadm set idrac.virtualmedia.Attached Attached
```

This is deliberately not done by the driver: it is a persistent BMC setting
rather than per-deployment state, it is vendor-CLI only on this generation, and
silently changing a BMC's configuration is not something a boot interface
should do behind an operator's back.

**And it is why the eject path puts the device back at the bottom.** Permanently
attaching the device is what makes the entry exist to reorder, but it also means
the firmware is offered that device on every boot forever. Leave it pinned first
and the machine is offered an EMPTY optical device ahead of its disk on every
later boot, and some firmware stops there rather than falling through.

Observed on real hardware: a machine booted its freshly written image perfectly,
then stopped booting entirely once the optical device was left pinned first. It
deploys, reports success, and never comes back, which looks exactly like a
failed install and is not one.

So the two halves have to ship together. If you implement this scheme for
another vendor, restore the order on eject as well.

#### Prefer the static configuration to this option

MEASURED, and the reason this section exists: on the hardware this was built
for, two BMC settings do the whole job and this option is not needed.

    pin the virtual optical device FIRST in the boot order, once
    set the BMC to AutoAttach

With no media attached the device is not presented at all, so it vanishes from
the boot order and the disk boots. With media attached it reappears at the top
and the machine boots the media. Nothing has to be reordered at run time.

Configured that way, with this option OFF, a full deploy ran clean:
inspection completed, the image was written, the node reached `active`, and the
machine booted its new image 45 seconds later.

With the option ON, the same machine stalled. The restore half puts the disk
back on top at the end of every deploy, so the NEXT deploy powers on with the
disk first, boots the old image instead of the agent, and the node sits in
`wait call-back` until it times out. That is a worse failure than the one the
option solves, because the machine looks healthy while the record of it is wrong.

There is also a reason to distrust the mechanism itself: on the same machine,
seconds apart, the Redfish `BootSources` view and the vendor CLI DISAGREED about
the current order. This code reads the Redfish view, so it can be deciding on a
stale picture of the thing it is changing.

So: try the static configuration first. Reach for this option only on a BMC
where that is not possible, and expect the cost described below.

#### The first attempt after a reorder still boots the old order

A vendor BIOS configuration job applies during the next POST, but **that same
boot still uses the previous order**. The corrected order governs the boot after
it. So the first deployment following a reorder can still boot the internal
disk, and the retry succeeds.

Nothing is wrong when that happens, it costs one cycle. Because the order is
re-asserted on every attach, it self-heals without operator involvement.

Two details here are not what you would guess from reading the code, and both
cost real time to find:

**The BMC does not land in `MissingActionError`.** That is the obvious place to
hook a fallback, and it is wrong. When the standard action is absent sushy does
not give up: it falls back to `PATCH`ing the VirtualMedia resource directly. An
iLO 4 rejects that PATCH:

```
HTTP PATCH .../redfish/v1/Managers/1/VirtualMedia/2 returned code 400
Base.0.10.PropertyUnknown: ['Inserted']
```

which surfaces as `BadRequestError`. So the fallback has to hang off the
`BadRequestError` handler as well, and the eject path needs the same pair.

**The OEM action takes `Image` and nothing else.** Passing an `Oem` block in the
POST body is rejected:

```
Base.0.10.ActionParameterUnknown: ['InsertVirtualMedia', 'Oem']
```

`BootOnNextServerReset` is a *property* of the device, not a parameter of the
action, so it needs a separate `PATCH` after the insert. That PATCH is best
effort: the media is already attached by then, so failing it warrants a warning
but must not undo a successful insert.

Nodes keep the stock `redfish` driver and the stock `redfish-virtual-media`
boot interface in both cases, so anything that drives Ironic keeps working
unchanged, the Bare Metal Operator included.

The complete change is in [`patches/`](patches/).

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

## Keeping up with upstream

The layout is chosen to make rebasing onto a new Ironic release cheap, because
a patch against a moving target is a maintenance liability.

All the logic lives in `module/ironic_relaxed_oem.py`, which is **copied** into
the image, never patched. It also registers its own config options, so
`ironic/conf/redfish.py` is not touched at all. The patch in `patches/` adds
only call sites: an import, the vendor gate, two insert hooks and one eject
hook. That is under 60 lines against a single file.

To move to a new Ironic release, bump `IRONIC_IMAGE` and build. Then:

- if the patch still applies, the build succeeds and the assertions confirm the
  hooks are live;
- if upstream moved the code, `patch` fails and the **build fails**. It cannot
  quietly produce an unpatched image.

### Tests

`tests/` runs **inside the built image**, against the real Ironic and sushy in
it, as a build step. An upstream change that breaks what this project depends
on therefore fails the build rather than a deployment, and the tests are removed
in the same layer so they are not carried in the published image.

Run them by hand against an image with:

```bash
docker run --rm -v "$PWD/tests:/tmp/tests:ro" -w /tmp/tests \
  --entrypoint python3.12 <image> -m unittest discover -s . -t .
```

They cover the use cases this exists for, and are deliberately shaped around
the mistakes actually made while writing it, because those are the ones that
will be made again:

- the vendor gate still raises exactly as upstream does while the option is off;
- the OEM insert body carries `Image` **and nothing else**, asserted on the
  recorded request, because an `Oem` block there is rejected by the BMC;
- `BootOnNextServerReset` goes out as a **separate PATCH**, and a failure of
  that PATCH does not undo an insert that already succeeded;
- the fallback is reached from **both** the missing-action and the bad-request
  paths, since an iLO 4 lands in the second one and hooking only the first was
  the original bug;
- a device carrying the standard action never reaches the OEM path at all;
- both options default to false and send nothing while off.

`tests/fixtures/ilo4_virtualmedia.json` is a real, unmodified payload captured
from an HPE iLO 4, so the lookup is tested against what the hardware actually
sends rather than against an idea of it.

The build also asserts things a successful patch does not guarantee:

- both options are registered and still default to `false`;
- all three hooks are present, including that the insert path has **two**
  (missing-action and bad-request), since fixing only one is a mistake already
  made once here;
- sushy still exposes the `path` and `json` attributes, and still names its
  connector `_conn`. That last one is private API, so it is checked at build
  time rather than discovered during a deploy.

The hooked insert loop is upstream's busiest spot in this file: it already
carries special cases for several vendors and will keep changing. Expect to
re-seat those two hooks occasionally. The vendor gate and the eject hook are in
much quieter code.

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
# For a BMC that HAS standard virtual media but is refused by the vendor gate
skip_vendor_validation = true
# For a BMC that has NO standard InsertMedia action at all, e.g. HPE iLO 4
enable_oem_vmedia_fallback = true
# For a BMC that accepts the one-time boot override and then clears it
force_persistent_boot_on_vmedia = true
# LAST RESORT for the same symptom, and MEASURED HARMFUL where the two BMC
# settings above it in the boot-order section are available. Read that section
# before turning this on.
# enable_oem_boot_order = true
```

Enable only what the hardware in front of you actually needs. They are
independent: the first is for a BMC that can do standard virtual media and is
being refused, the second is for one that genuinely cannot, and the third is for
one that attaches media fine but will not boot it.

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
