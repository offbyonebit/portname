# Portname 1.1.1 validation

Validated on 2026-10-26 on Linux Mint 22.3 Cinnamon with PipeWire and
WirePlumber 0.4.17, using physical PCI audio devices and a Logitech Brio 101
USB microphone.

## Live installation and hardware

Installed a fresh source copy using `./install.sh` and tested the installed
`/usr/bin/portname` launcher and `/usr/lib/portname` package. The complete final
run passed 19 assertions, including restoration checks.

- Recovered an actual missing microphone profile whose original was registered
  as a `.conf.distrib` diversion, without losing existing custom port names.
- Renamed an HDMI route through the installed CLI with `--device`, observed
  the new route description in live PipeWire, and reverted the profile with
  byte-for-byte restoration.
- Applied the installed WirePlumber Lua backend to the physical Brio node,
  observed its new description in PipeWire, deliberately overwrote the rule,
  and restored the intended description through the installed `repair` command.
- Ran the installed `revert --all`, verified the USB name disappeared from the
  live node and saved-name store, and verified `repair` did not recreate it.
- Opened a capture stream from the physical microphone to `/dev/null`, and a
  silent playback stream through the real output device. Verified active
  PipeWire links for both; no microphone recording was retained.
- Opened the installed GTK3 window on the desktop and verified device rows,
  Rename controls, and the Repair Missing Ports button were present.
- Restored pre-existing customized profiles, saved names, diversion records,
  default devices, and the previous application installation. Verified all
  original hardware nodes and all three audio services were active afterward.
  The recovered microphone profile was retained.

Rapid live operations also exposed systemd's five-starts-per-ten-seconds limit.
The fix batches revert-all into one audio restart and, on an actual
`start-limit-hit`, resets only the affected audio units and retries once.
Configuration failures still produce errors. The final live run passed with
this recovery behavior included.

## Automated coverage

`python3 -m pytest -q` passes all 134 tests. Added coverage includes real
`dpkg-divert` operations with a private temporary database, both WirePlumber
rule formats, missing and overwritten rules, revert-all persistence, ambiguous
device selection, GUI-to-CLI device forwarding, and bounded restart recovery.

The wheel also builds with Mint's system setuptools using
`pip wheel --no-deps --no-build-isolation --no-index .`. The license metadata
uses the format supported by the declared setuptools minimum; the previous
SPDX string failed with the system builder. The resulting wheel was installed
into a separate directory and its launcher and package imports were checked.

Two USB devices sharing a route were covered by regression tests; only one
physical USB audio device was available. GTK startup was tested on the real
desktop; the authentication dialog was not automated. These checks reproduce
the reported backup failure on the same Mint release, but do not constitute a
test on the Reddit reporter's own machine.
