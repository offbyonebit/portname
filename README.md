# Portname: Rename Audio Ports on Linux

Ever wished you could change "Line Out" to "JBL Headset" or "Headphones" to "Pebble Speakers" in your Sound Settings? Now you can.

**Portname** lets you rename the audio input/output ports that appear in your system's Sound Settings panel. Works with PipeWire and ALSA on any Linux distribution.

## Before & After

```
Before:                              After:
  Line Out                             JBL Headset
  Starship/Matisse HD Audio            Starship/Matisse HD Audio

  Headphones                           Pebble Speakers
  Starship/Matisse HD Audio            Starship/Matisse HD Audio
```

## Install

```bash
pip install portname
```

To update to the latest version:

```bash
pip install --upgrade portname
```

**Alternative, install from source:**

```bash
git clone https://github.com/offbyonebit/portname.git
cd portname
chmod +x install.sh
./install.sh
```

## Usage

### GUI (recommended for beginners)

Just run `portname` or find **Portname** in your application menu. Click **Rename** next to any audio port, type a new name, and enter your password when prompted.

### Command Line

```bash
# List all audio devices and ports
portname list

# Rename a port (requires sudo)
sudo portname rename analog-output-lineout "JBL Headset"
sudo portname rename analog-output-headphones "Pebble Speakers"
sudo portname rename analog-input-rear-mic "JBL Headset Mic"

# Revert a single port
sudo portname revert analog-output-lineout

# Revert all renamed ports
sudo portname revert --all

# Check for renames clobbered by a package upgrade and re-apply them (Arch/Fedora)
sudo portname check

# Toggle Auto-Mute Mode (fixes issue where front jack silences rear jacks)
portname auto-mute off
portname auto-mute on
portname auto-mute status
```

## How It Works

Audio port names on Linux come from ALSA card profile path files in `/usr/share/alsa-card-profile/mixer/paths/`. Portname:

1. Backs up the original file to `<name>.conf.orig`
2. Writes a modified copy with your custom name
3. Restarts PipeWire so the change appears immediately

Your custom names survive reboots. On **Debian/Ubuntu** the backup is registered with `dpkg-divert`, which also tells the package manager to skip the file on upgrades. On **other distros** (Arch, Fedora, etc.) the backup/restore logic is identical but there is no package-manager hook. An upgrade of `alsa-card-profile` can silently overwrite the modified file. Run `sudo portname check` after such an upgrade to detect and automatically re-apply any clobbered names. AUR/Copr package descriptions should note this and suggest adding a post-upgrade hook that calls `portname check`.

## Compatibility

| Distro | Status |
|---|---|
| Linux Mint 22 | Fully tested |
| Ubuntu 22.10+ | Fully tested |
| Arch Linux | Core features tested via CI, real-machine audio feedback not yet verified |
| Fedora | Core features tested via CI, real-machine audio feedback not yet verified |
| Other PipeWire distros | Should work, but untested |

**Arch / Fedora users:** portname works on your distro, but hasn't been tested by a real person on real hardware yet. If something doesn't behave as expected, names not showing up in Sound Settings, the audio system not restarting, anything, please [open an issue](https://github.com/offbyonebit/portname/issues) and describe what happened. That feedback is how we close the gap.

Does not work on distros still using PulseAudio without PipeWire.

## Requirements

- Linux with PipeWire (standard on most distros from 2022 onward)
- Python 3.8+
- GTK3 via PyGObject (pre-installed on most desktop Linux distros)

## Uninstall

```bash
cd portname
./install.sh --uninstall
```

This reverts all renamed ports to their original names and removes portname.

## Support

This project is free and I don't ask for anything. If it's useful to you,
a star on the repo is appreciated, and if you want to follow along with
other things I'm building, you can find them under
[@offbyonebit](https://github.com/offbyonebit).

If you'd like to support development, you can [sponsor me on GitHub](https://github.com/sponsors/offbyonebit).

## License

MIT
