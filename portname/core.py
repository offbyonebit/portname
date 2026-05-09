"""Core business logic for portname: reading devices, renaming ports, reverting."""

import json
import os
import pwd
import re
import shutil
import subprocess

from portname import wireplumber

PATHS_DIR = "/usr/share/alsa-card-profile/mixer/paths"

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


def get_path_file(route_name):
    """Return the full path to the ALSA path config file for a route."""
    if ".." in route_name or "/" in route_name or "\\" in route_name:
        raise ValueError(f"Invalid route name: {route_name}")
    path = os.path.join(PATHS_DIR, f"{route_name}.conf")
    if not os.path.exists(path) and not os.path.exists(path + ".orig"):
        raise FileNotFoundError(f"No path file found for route '{route_name}'")
    return path


def is_renamed(route_name):
    """Check if a port has been renamed (dpkg-divert is active)."""
    path = get_path_file(route_name)
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
        restart_pipewire()
        return

    # Default: ALSA path file method
    path = get_path_file(route_name)

    # Set up dpkg-divert if not already done
    if not is_renamed(route_name):
        try:
            subprocess.run(
                ["dpkg-divert", "--local", "--rename", "--add", path],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"dpkg-divert failed: {e.stderr.strip() or 'unknown error'}")

    # Read from .orig, write modified version to the main path
    content = _modify_description(path + ".orig", new_name)
    try:
        with open(path, "w") as f:
            f.write(content)
    except OSError as e:
        raise RuntimeError(f"Cannot write to {path}: {e}")

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
        restart_pipewire()
        return

    # Default: ALSA path file method
    path = get_path_file(route_name)

    if not is_renamed(route_name):
        raise ValueError(f"Port '{route_name}' has not been renamed")

    # Remove our modified copy
    if os.path.exists(path):
        os.remove(path)

    # Remove the divert (restores .orig to original location)
    try:
        subprocess.run(
            ["dpkg-divert", "--local", "--rename", "--remove", path],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"dpkg-divert revert failed: {e.stderr.strip() or 'unknown error'}")

    restart_pipewire()


def revert_all():
    """Revert all renamed ports. Must be run as root."""
    if os.geteuid() != 0:
        raise PermissionError("Must be run as root")

    result = subprocess.run(
        ["dpkg-divert", "--list"], capture_output=True, text=True
    )
    reverted = []
    for line in result.stdout.splitlines():
        if PATHS_DIR in line and "local diversion" in line:
            # Extract the original path from: "local diversion of /path/to/file.conf to /path/to/file.conf.orig"
            parts = line.split()
            try:
                idx = parts.index("of")
                path = parts[idx + 1]
                route_name = os.path.basename(path).replace(".conf", "")
                revert_port(route_name)
                reverted.append(route_name)
            except (ValueError, IndexError):
                continue

    return reverted


def get_all_renamed():
    """Return a list of all currently renamed route names."""
    result = subprocess.run(
        ["dpkg-divert", "--list"], capture_output=True, text=True
    )
    renamed = []
    for line in result.stdout.splitlines():
        if PATHS_DIR in line and "local diversion" in line:
            parts = line.split()
            try:
                idx = parts.index("of")
                path = parts[idx + 1]
                route_name = os.path.basename(path).replace(".conf", "")
                renamed.append(route_name)
            except (ValueError, IndexError):
                continue
    return renamed


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
