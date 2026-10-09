"""Command-line interface for portname."""

import argparse
import subprocess
import sys

from portname import __version__
from portname.core import (
    get_devices, is_renamed, get_original_description,
    rename_port, revert_port, revert_all, find_route, check_renames,
    node_name_for_route,
)
from portname import wireplumber
from portname.automute import get_auto_mute_status, set_auto_mute, get_cards_with_auto_mute
from portname.privilege import ensure_root_or_exit


def _route_not_found(route_name):
    print(f"Error: Route '{route_name}' not found. Run 'portname list' to see available routes.",
          file=sys.stderr)
    sys.exit(1)


def _route_renamed_tag(dev, route):
    """Return True if this route is currently renamed via its method."""
    if route["rename_method"] == "wireplumber":
        node_name = node_name_for_route(dev, route)
        if node_name:
            return wireplumber.is_node_renamed(node_name)
        return False
    return is_renamed(route["name"])


def cmd_list(args):
    """List all audio devices and ports."""
    devices = get_devices()
    if not devices:
        print("No audio devices found.")
        return

    for dev in devices:
        print(f"\n{dev['device_description']} (card {dev['alsa_card']})")

        outputs = [r for r in dev["routes"] if r["direction"] == "Output"]
        inputs = [r for r in dev["routes"] if r["direction"] == "Input"]

        for label, routes in [("Output", outputs), ("Input", inputs)]:
            if not routes:
                continue
            print(f"  {label}:")
            for r in routes:
                tags = []
                if _route_renamed_tag(dev, r):
                    tags.append("renamed")
                if r["available"] == "yes":
                    tags.append("available")
                tag_str = f"  [{', '.join(tags)}]" if tags else ""
                print(f"    {r['name']:<40} {r['description']}{tag_str}")


def cmd_rename(args):
    """Rename an audio port."""
    ensure_root_or_exit()
    dev, route = find_route(args.route)
    if route is None:
        _route_not_found(args.route)

    method = route["rename_method"]
    node_name = node_name_for_route(dev, route)
    if method == "wireplumber" and not node_name:
        print(f"Error: No PipeWire node found for route '{args.route}'", file=sys.stderr)
        sys.exit(1)

    try:
        rename_port(args.route, args.name, method=method, node_name=node_name)
        print(f"Renamed '{args.route}' to '{args.name}'")
        print("Restarting PipeWire... done.")
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_revert(args):
    """Revert a renamed port."""
    ensure_root_or_exit()
    try:
        if args.all:
            result = revert_all()
            reverted = result["reverted"]
            failed = result["failed"]
            if reverted:
                for name in reverted:
                    print(f"Reverted '{name}'")
                print("Restarting PipeWire... done.")
            if failed:
                print("\nThe following could not be reverted:", file=sys.stderr)
                for name, err in failed:
                    print(f"  - {name}: {err}", file=sys.stderr)
            if not reverted and not failed:
                print("No renamed ports to revert.")
        elif args.route:
            dev, route = find_route(args.route)
            if route is None:
                _route_not_found(args.route)
            method = route["rename_method"]
            node_name = node_name_for_route(dev, route)
            if method == "wireplumber" and not node_name:
                print(f"Error: No PipeWire node found for route '{args.route}'", file=sys.stderr)
                sys.exit(1)
            revert_port(args.route, method=method, node_name=node_name)
            print(f"Reverted '{args.route}' to original name")
            print("Restarting PipeWire... done.")
        else:
            print("Error: specify a route name or use --all", file=sys.stderr)
            sys.exit(1)
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_check(args):
    """Check for renames clobbered by package upgrades and re-apply them."""
    ensure_root_or_exit()
    try:
        result = check_renames()
        repaired = result["repaired"]
        failed = result["failed"]
        if repaired:
            print("Re-applied the following clobbered renames:")
            for name in repaired:
                print(f"  - {name}")
            print("Restarting PipeWire... done.")
        if failed:
            print("\nThe following could not be repaired:", file=sys.stderr)
            for name, err in failed:
                print(f"  - {name}: {err}", file=sys.stderr)
        if not repaired and not failed:
            print("No clobbered renames found.")
    except (FileNotFoundError, ValueError, RuntimeError, PermissionError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_auto_mute(args):
    """Toggle Auto-Mute Mode."""
    devices = get_devices()

    if args.card:
        card = args.card
    else:
        cards = get_cards_with_auto_mute(devices)
        if not cards:
            print("No cards with Auto-Mute Mode found.")
            return
        if len(cards) > 1:
            print("Multiple cards found, specify one with --card:")
            for c, desc in cards:
                print(f"  --card {c}  ({desc})")
            return
        card = cards[0][0]

    if args.state == "status" or args.state is None:
        status = get_auto_mute_status(card)
        if status is None:
            print(f"Card {card} does not have Auto-Mute Mode.")
        else:
            print(f"Auto-Mute Mode on card {card}: {status}")
    elif args.state == "on":
        set_auto_mute(card, True)
        print(f"Auto-Mute Mode on card {card}: Enabled")
    elif args.state == "off":
        set_auto_mute(card, False)
        print(f"Auto-Mute Mode on card {card}: Disabled")


def cmd_gui(args):
    """Launch the GUI."""
    try:
        from portname.gui import run_gui
        run_gui()
    except ImportError as e:
        print(f"Error: GUI requires GTK3 (PyGObject): {e}", file=sys.stderr)
        print("Install with: sudo apt install python3-gi gir1.2-gtk-3.0", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="portname",
        description="Rename PipeWire/ALSA audio ports as they appear in Sound Settings",
    )
    parser.add_argument("--version", action="version", version=f"portname {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    # list
    subparsers.add_parser("list", help="List all audio devices and ports")

    # rename
    rename_p = subparsers.add_parser("rename", help="Rename an audio port (requires sudo)")
    rename_p.add_argument("route", help="Route name (e.g. analog-output-lineout)")
    rename_p.add_argument("name", help="New display name")

    # revert
    revert_p = subparsers.add_parser("revert", help="Revert a renamed port (requires sudo)")
    revert_p.add_argument("route", nargs="?", help="Route name to revert")
    revert_p.add_argument("--all", action="store_true", help="Revert all renamed ports")

    # check
    subparsers.add_parser("check", help="Re-apply renames clobbered by package upgrades")

    # auto-mute
    am_p = subparsers.add_parser("auto-mute", help="Toggle Auto-Mute Mode")
    am_p.add_argument("state", nargs="?", choices=["on", "off", "status"], default="status")
    am_p.add_argument("--card", "-c", help="ALSA card number")

    # gui
    subparsers.add_parser("gui", help="Launch graphical interface")

    args = parser.parse_args()

    if args.command is None:
        # Default to GUI if no command given
        cmd_gui(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "rename":
        cmd_rename(args)
    elif args.command == "revert":
        cmd_revert(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "auto-mute":
        cmd_auto_mute(args)
    elif args.command == "gui":
        cmd_gui(args)
