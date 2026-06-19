"""Core business logic for portname: reading devices, renaming ports, reverting."""

import fcntl
import json
import os
import pwd
import re
import shutil
import subprocess

from portname import wireplumber

PATHS_DIR = "/usr/share/alsa-card-profile/mixer/paths"
_RENAME_STORE_PATH = "/etc/portname/renames.json"

# Port names must be reasonable: 1-64 chars, no control characters
_VALID_NAME_RE = re.compile(r"^[^\x00-\x1f]{1,64}$")


def validate_port_name(name):
    """Validate a port display name. Raises ValueError if invalid."""
    if not name or not name.strip():
        raise ValueError("Port name cannot be empty")
    name = name.strip()
    if not _VALID_NAME_RE.match(name):
        raise ValueError("Port name must be 1-64 characters with no control characters")
    return name


def _has_dpkg_divert():
    """Return True if dpkg-divert is available (Debian/Ubuntu)."""
    return shutil.which("dpkg-divert") is not None


def _get_diverted_paths():
    """Return a set of paths currently diverted by dpkg-divert in PATHS_DIR."""
    diverted = set()
    if not _has_dpkg_divert():
        return diverted
    result = subprocess.run(
        ["dpkg-divert", "--list"], capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if PATHS_DIR in line and "local diversion" in line:
            parts = line.split()
            try:
                idx = parts.index("of")
                diverted.add(parts[idx + 1])
            except (ValueError, IndexError):
                continue
    return diverted


def get_devices():
    """Get all ALSA audio devices and their routes from PipeWire.

    Returns a list of dicts:
        [{"device_name": str, "device_description": str, "alsa_card": str,
          "device_bus": str, "node_names": [str],
          "routes": [{"name": str, "description": str, "direction": str,
                       "available": str, "rename_method": str}]}]

    rename_method is "path_file" for routes with ALSA path config files,
    or "wireplumber" for USB routes without path files (renamed via WirePlumber rules).
    """
    if not shutil.which("pw-dump"):
        raise RuntimeError(
            "pw-dump not found. Is PipeWire installed?\n"
            "Install with: sudo apt install pipewire"
        )

    try:
        result = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, check=True, timeout=10
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("pw-dump timed out — PipeWire may not be running")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"pw-dump failed: {e.stderr.strip() or 'unknown error'}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("pw-dump returned invalid JSON — PipeWire may be misconfigured")

    # First pass: collect node names keyed by device.name so we can associate
    # PipeWire nodes with their parent devices.
    device_nodes = {}  # device.name -> list of node.name
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        node_name = props.get("node.name", "")
        dev_name = props.get("device.name", "")
        media_class = props.get("media.class", "")
        if node_name and dev_name and media_class.startswith("Audio/"):
            device_nodes.setdefault(dev_name, []).append(node_name)

    # Second pass: build device list
    devices = []
    for obj in data:
        props = obj.get("info", {}).get("props", {})
        if props.get("device.api") != "alsa":
            continue
        if "device.name" not in props:
            continue

        params = obj.get("info", {}).get("params", {})
        enum_routes = params.get("EnumRoute", [])
        if not enum_routes:
            continue

        device_bus = props.get("device.bus", "")
        dev_name = props.get("device.name", "")
        is_usb = device_bus == "usb"

        routes = []
        for route in enum_routes:
            route_name = route.get("name", "")
            # Check if a corresponding ALSA path file exists
            path_file = os.path.join(PATHS_DIR, f"{route_name}.conf")
            orig_file = path_file + ".orig"
            has_path_file = os.path.exists(path_file) or os.path.exists(orig_file)

            if has_path_file:
                rename_method = "path_file"
            elif is_usb:
                rename_method = "wireplumber"
            else:
                # Non-USB device without a path file -- skip as before
                continue

            routes.append({
                "name": route_name,
                "description": route.get("description", route_name),
                "direction": route.get("direction", "Unknown"),
                "available": route.get("available", "unknown"),
                "rename_method": rename_method,
            })

        if routes:
            devices.append({
                "device_name": dev_name,
                "device_description": props.get("device.description", props.get("alsa.card_name", "Unknown")),
                "alsa_card": str(props.get("alsa.card", "")),
                "device_bus": device_bus,
                "node_names": device_nodes.get(dev_name, []),
                "routes": routes,
            })

    return devices


def node_name_for_route(dev, route):
    """Pick the PipeWire node name that matches the route's direction."""
    node_names = dev.get("node_names", [])
    if not node_names:
        return None
    if len(node_names) == 1:
        return node_names[0]
    direction = route.get("direction", "")
    if direction == "Output":
        for name in node_names:
            if ".output" in name or "_output" in name:
                return name
    elif direction == "Input":
        for name in node_names:
            if ".input" in name or "_input" in name:
                return name
    return node_names[0]


def find_route(route_name):
    """Find a route by name across all devices.

    Returns (device, route) or (None, None) if not found.
    """
    for dev in get_devices():
        for route in dev["routes"]:
            if route["name"] == route_name:
                return dev, route
    return None, None


def get_path_file(route_name):
    """Return the full path to the ALSA path config file for a route."""
    if ".." in route_name or "/" in route_name or "\\" in route_name:
        raise ValueError(f"Invalid route name: {route_name}")
    path = os.path.join(PATHS_DIR, f"{route_name}.conf")
    if not os.path.exists(path) and not os.path.exists(path + ".orig"):
        raise FileNotFoundError(f"No path file found for route '{route_name}'")
    return path


def is_renamed(route_name):
    """Check if a path-file port has been renamed.

    On Debian/Ubuntu this consults dpkg-divert's actual database; on other
    distros it checks for a manual .orig backup.
    """
    try:
        path = get_path_file(route_name)
    except FileNotFoundError:
        return False
    if _has_dpkg_divert():
        return path in _get_diverted_paths()
    return os.path.exists(path + ".orig")


def get_original_description(route_name):
    """Get the original description key/value from the .orig file."""
    path = get_path_file(route_name)
    orig = path + ".orig"
    if not os.path.exists(orig):
        return None
    return _read_description(orig)


def _read_description(filepath):
    """Read the description or description-key from a path file's [General] section."""
    in_general = False
    try:
        with open(filepath) as f:
            for line in f:
                stripped = line.strip()
                if stripped == "[General]":
                    in_general = True
                    continue
                if stripped.startswith("[") and stripped.endswith("]"):
                    if in_general:
                        break
                    continue
                if in_general:
                    if stripped.startswith("description ="):
                        return stripped.split("=", 1)[1].strip()
                    if stripped.startswith("description-key ="):
                        return stripped.split("=", 1)[1].strip()
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _modify_description(orig_path, new_description):
    """Read a path file and return modified content with a new description."""
    try:
        with open(orig_path) as f:
            lines = f.readlines()
    except OSError as e:
        raise RuntimeError(f"Cannot read path file {orig_path}: {e}")

    in_general = False
    found = False
    result = []

    for line in lines:
        stripped = line.strip()
        if stripped == "[General]":
            in_general = True
            result.append(line)
        elif stripped.startswith("[") and stripped.endswith("]"):
            in_general = False
            result.append(line)
        elif in_general and not found and (
            stripped.startswith("description-key") or stripped.startswith("description")
        ):
            result.append(f"description = {new_description}\n")
            found = True
        else:
            result.append(line)

    if not found:
        raise RuntimeError(
            f"Could not find description field in [General] section of {orig_path}. "
            "The file may be corrupted or in an unexpected format."
        )

    return "".join(result)


def _ensure_backup(path):
    """Create a .orig backup of path if it does not already exist.

    On Debian/Ubuntu this uses dpkg-divert. On other distros it copies the file
    manually. Returns True if a new backup was created.

    Raises RuntimeError if the path is already diverted but the .orig backup is
    missing (an inconsistent state that requires manual cleanup).
    """
    orig = path + ".orig"
    if os.path.exists(orig):
        return False

    if _has_dpkg_divert():
        if path in _get_diverted_paths():
            raise RuntimeError(
                f"{path} is already diverted by dpkg-divert but the .orig backup is missing. "
                "Run 'sudo dpkg-divert --local --rename --remove <path>' to repair."
            )
        try:
            subprocess.run(
                ["dpkg-divert", "--local", "--rename", "--add", path],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"dpkg-divert failed: {e.stderr.strip() or 'unknown error'}")
    else:
        try:
            shutil.copy2(path, orig)
        except OSError as e:
            raise RuntimeError(f"Cannot create backup {orig}: {e}")
    return True


def _restore_backup(path):
    """Restore the original path file from .orig and remove the backup.

    On Debian/Ubuntu this removes the dpkg-divert. On other distros it copies
    .orig back to path and deletes .orig.
    """
    orig = path + ".orig"
    if not os.path.exists(orig):
        raise ValueError(f"No backup found for {path}")

    if _has_dpkg_divert():
        try:
            subprocess.run(
                ["dpkg-divert", "--local", "--rename", "--remove", path],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"dpkg-divert revert failed: {e.stderr.strip() or 'unknown error'}")
    else:
        try:
            shutil.copy2(orig, path)
            os.remove(orig)
        except OSError as e:
            raise RuntimeError(f"Cannot restore backup for {path}: {e}")


def _current_description(path):
    """Read the active description from a path file (not the backup)."""
    return _read_description(path)


def rename_port(route_name, new_name, method="path_file", node_name=None):
    """Rename an audio port. Must be run as root.

    Args:
        route_name: The route/port name (e.g. "analog-output-lineout").
        new_name: The new display name.
        method: "path_file" (ALSA path config) or "wireplumber" (WirePlumber rule).
        node_name: Required when method is "wireplumber". The PipeWire node.name
                   to target (e.g. "alsa_output.usb-...").
    """
    if os.geteuid() != 0:
        raise PermissionError("Must be run as root")

    new_name = validate_port_name(new_name)

    if method == "wireplumber":
        if not node_name:
            raise ValueError("node_name is required for WirePlumber rename")
        wireplumber.rename_node(node_name, new_name)
        _store_custom_name("wireplumber", node_name, new_name)
        restart_pipewire()
        return

    # Default: ALSA path file method
    path = get_path_file(route_name)

    # Create backup if not already present
    _ensure_backup(path)

    # Read from .orig, write modified version to the main path
    content = _modify_description(path + ".orig", new_name)
    try:
        with open(path, "w") as f:
            f.write(content)
    except OSError as e:
        raise RuntimeError(f"Cannot write to {path}: {e}")

    _store_custom_name("path_file", route_name, new_name)
    restart_pipewire()


def revert_port(route_name, method="path_file", node_name=None):
    """Revert a renamed port to its original name. Must be run as root.

    Args:
        route_name: The route/port name (e.g. "analog-output-lineout").
        method: "path_file" (ALSA path config) or "wireplumber" (WirePlumber rule).
        node_name: Required when method is "wireplumber".
    """
    if os.geteuid() != 0:
        raise PermissionError("Must be run as root")

    if method == "wireplumber":
        if not node_name:
            raise ValueError("node_name is required for WirePlumber revert")
        wireplumber.revert_node(node_name)
        _clear_custom_name("wireplumber", node_name)
        restart_pipewire()
        return

    # Default: ALSA path file method
    path = get_path_file(route_name)

    if not is_renamed(route_name):
        raise ValueError(f"Port '{route_name}' has not been renamed")

    _restore_backup(path)
    _clear_custom_name("path_file", route_name)
    restart_pipewire()


def get_all_renamed_path_files():
    """Return a list of route names that have path-file renames."""
    renamed = []

    if _has_dpkg_divert():
        for path in _get_diverted_paths():
            if path.startswith(PATHS_DIR) and path.endswith(".conf"):
                renamed.append(os.path.basename(path).replace(".conf", ""))
    else:
        # Manual backup mode: scan for .orig files
        if os.path.isdir(PATHS_DIR):
            for filename in os.listdir(PATHS_DIR):
                if filename.endswith(".conf.orig"):
                    route_name = filename[:-len(".conf.orig")]
                    renamed.append(route_name)

    return renamed


def get_all_renamed():
    """Return a list of all currently renamed path-file route names."""
    return sorted(get_all_renamed_path_files())


def revert_all():
    """Revert all renamed ports. Must be run as root.

    Returns {"reverted": [...], "failed": [(name, error), ...]}.
    """
    if os.geteuid() != 0:
        raise PermissionError("Must be run as root")

    reverted = []
    failed = []

    # Revert path-file renames
    for route_name in get_all_renamed_path_files():
        try:
            revert_port(route_name)
            reverted.append(route_name)
        except (ValueError, RuntimeError) as e:
            failed.append((route_name, str(e)))

    # Revert WirePlumber rules
    for node_name, _desc in wireplumber.get_all_renamed():
        try:
            wireplumber.revert_node(node_name)
            reverted.append(node_name)
        except (ValueError, RuntimeError) as e:
            failed.append((node_name, str(e)))

    return {"reverted": reverted, "failed": failed}


def check_renames():
    """Detect renames clobbered by package upgrades or external changes and re-apply them.

    Returns {"repaired": [...], "failed": [(name, error), ...]}.
    """
    if os.geteuid() != 0:
        raise PermissionError("Must be run as root")

    repaired = []
    failed = []
    store = _load_custom_name_store()

    # Path-file renames. Check every route that is either recorded in the
    # sidecar or actually diverted/backed-up on disk -- the sidecar alone can
    # miss renames made outside portname or lost to a corrupted sidecar file.
    path_file_entries = store.get("path_file", {})
    all_route_names = sorted(set(path_file_entries) | set(get_all_renamed_path_files()))
    for route_name in all_route_names:
        custom_name = path_file_entries.get(route_name)
        try:
            path = get_path_file(route_name)
        except (FileNotFoundError, ValueError) as e:
            failed.append((route_name, str(e)))
            continue

        orig_path = path + ".orig"

        # If we don't have a sidecar entry for this route, try to derive one from
        # the current active file before deciding nothing needs repair.
        current = _current_description(path)
        if custom_name is None:
            original = _read_description(orig_path)
            if current is None or original is None:
                # Can't determine state; skip to avoid destructive changes.
                continue
            if current != original:
                custom_name = current
                _store_custom_name("path_file", route_name, custom_name)
            else:
                continue

        # If the active file is missing the description field or differs from
        # the intended custom name, re-apply.
        if current != custom_name:
            try:
                content = _modify_description(orig_path, custom_name)
                with open(path, "w") as f:
                    f.write(content)
                repaired.append(route_name)
            except (OSError, RuntimeError) as e:
                failed.append((route_name, str(e)))

    # WirePlumber renames
    wp_entries = store.get("wireplumber", {})
    existing_rules = {name for name, _desc in wireplumber.get_all_renamed()}
    for node_name, custom_name in wp_entries.items():
        if node_name in existing_rules:
            continue
        try:
            wireplumber.rename_node(node_name, custom_name)
            repaired.append(node_name)
        except (OSError, RuntimeError, ValueError) as e:
            failed.append((node_name, str(e)))

    if repaired:
        restart_pipewire()

    return {"repaired": repaired, "failed": failed}


def _custom_name_store_path():
    """Return the path to the JSON sidecar that records custom names."""
    return _RENAME_STORE_PATH


def _load_custom_name_store():
    """Load the custom-name sidecar as a dict.

    Migrates the legacy flat format (used before WirePlumber support) to the
    current nested format.
    """
    path = _custom_name_store_path()
    if not os.path.exists(path):
        return {"path_file": {}, "wireplumber": {}}
    try:
        with open(path) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        if isinstance(data, dict):
            if "path_file" not in data and "wireplumber" not in data:
                # Legacy flat format: only path-file route names.
                return {"path_file": data, "wireplumber": {}}
            return {
                "path_file": data.get("path_file", {}),
                "wireplumber": data.get("wireplumber", {}),
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {"path_file": {}, "wireplumber": {}}


def _save_custom_name_store(store):
    """Save the custom-name sidecar with advisory locking."""
    path = _custom_name_store_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(store, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _store_custom_name(kind, key, custom_name):
    """Record the custom name for a path-file route or WirePlumber node."""
    store = _load_custom_name_store()
    store.setdefault(kind, {})[key] = custom_name
    _save_custom_name_store(store)


def _clear_custom_name(kind, key):
    """Remove the recorded custom name after it is reverted."""
    store = _load_custom_name_store()
    if kind in store and key in store[kind]:
        del store[kind][key]
        _save_custom_name_store(store)


def _get_stored_custom_name(kind, key):
    """Return the recorded custom name, or None."""
    return _load_custom_name_store().get(kind, {}).get(key)


def _get_real_user():
    """Get the real logged-in user when running under sudo/pkexec."""
    # Try SUDO_USER first
    user = os.environ.get("SUDO_USER")
    if user:
        return user, str(pwd.getpwnam(user).pw_uid)

    # Try PKEXEC_UID
    uid = os.environ.get("PKEXEC_UID")
    if uid:
        return pwd.getpwuid(int(uid)).pw_name, uid

    # Fallback
    uid = os.getuid()
    return pwd.getpwuid(uid).pw_name, str(uid)


def restart_pipewire():
    """Restart PipeWire services. Runs as the real user if we're root via sudo/pkexec."""
    try:
        if os.geteuid() == 0:
            user, uid = _get_real_user()
            subprocess.run(
                ["sudo", "-u", user,
                 f"XDG_RUNTIME_DIR=/run/user/{uid}",
                 "systemctl", "--user", "restart",
                 "pipewire", "pipewire-pulse", "wireplumber"],
                check=True, capture_output=True, text=True, timeout=15,
            )
        else:
            subprocess.run(
                ["systemctl", "--user", "restart",
                 "pipewire", "pipewire-pulse", "wireplumber"],
                check=True, capture_output=True, text=True, timeout=15,
            )
    except subprocess.TimeoutExpired:
        raise RuntimeError("PipeWire restart timed out")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"PipeWire restart failed: {e.stderr.strip() or 'unknown error'}")
