# Fixtures

`ilo4_virtualmedia.json` is a real, unmodified `VirtualMedia` resource captured
from an HPE iLO 4. It is the payload the OEM fallback exists for: note that
`Actions` is absent entirely and the only actions live under `Oem.Hp`.

It carries no addresses, serial numbers or identifiers. `Image` was empty at
capture time because nothing was attached.

Do not tidy this file. Its value is that it is exactly what the hardware sends,
including the parts that look redundant.
