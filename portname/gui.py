"""GTK3 GUI for portname."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

from portname.core import (
    get_devices, is_renamed, get_original_description, validate_port_name,
    node_name_for_route,
)
from portname.automute import get_auto_mute_status, set_auto_mute
from portname.privilege import run_as_root
from portname import wireplumber


class PortNameWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Portname — Audio Port Renamer")
        self.set_default_size(700, 500)
        self.set_border_width(12)

        # Main scrollable container
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.add(scrolled)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scrolled.add(self.main_box)

        header = Gtk.HeaderBar(title="Portname — Audio Port Renamer")
        header.set_show_close_button(True)
        repair_btn = Gtk.Button(label="Repair Missing Ports")
        repair_btn.connect("clicked", self._on_repair_clicked)
        header.pack_end(repair_btn)
        self.set_titlebar(header)

        self._build_device_list()

    def _build_device_list(self):
        # Clear existing children
        for child in self.main_box.get_children():
            self.main_box.remove(child)

        try:
            devices = get_devices()
        except Exception as e:
            error_label = Gtk.Label(label=f"Error reading audio devices: {e}")
            error_label.set_line_wrap(True)
            self.main_box.pack_start(error_label, False, False, 0)
            self.main_box.show_all()
            return

        if not devices:
            label = Gtk.Label(label="No audio devices found.")
            self.main_box.pack_start(label, False, False, 0)
            self.main_box.show_all()
            return

        for dev in devices:
            self._add_device_section(dev)

        self.main_box.show_all()

    def _add_device_section(self, dev):
        # Device header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.set_margin_top(8)

        label = Gtk.Label()
        label.set_markup(f"<b>{GLib.markup_escape_text(dev['device_description'])}</b>  "
                         f"<small>(card {dev['alsa_card']})</small>")
        label.set_halign(Gtk.Align.START)
        header_box.pack_start(label, True, True, 0)

        # Auto-Mute button if applicable
        if dev["alsa_card"]:
            status = get_auto_mute_status(dev["alsa_card"])
            if status is not None:
                am_btn = Gtk.Button(label=f"Auto-Mute: {status}")
                am_btn.connect("clicked", self._on_auto_mute_toggle, dev["alsa_card"])
                if status == "Enabled":
                    am_btn.get_style_context().add_class("suggested-action")
                header_box.pack_end(am_btn, False, False, 0)

        self.main_box.pack_start(header_box, False, False, 0)

        # Routes list
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        frame.add(listbox)

        outputs = [r for r in dev["routes"] if r["direction"] == "Output"]
        inputs = [r for r in dev["routes"] if r["direction"] == "Input"]

        for direction_label, routes in [("Output", outputs), ("Input", inputs)]:
            for route in routes:
                row = self._create_route_row(dev, route, direction_label)
                listbox.add(row)

        self.main_box.pack_start(frame, False, False, 0)

    def _is_route_renamed(self, dev, route):
        """Return True if this route has been renamed by either method."""
        if route["rename_method"] == "wireplumber":
            node_name = node_name_for_route(dev, route)
            if node_name:
                return wireplumber.is_node_renamed(node_name)
            return False
        return is_renamed(route["name"])

    def _create_route_row(self, dev, route, direction_label):
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_margin_top(6)
        hbox.set_margin_bottom(6)
        hbox.set_margin_start(10)
        hbox.set_margin_end(10)

        # Direction indicator
        if direction_label == "Output":
            icon_text = "OUT"
        else:
            icon_text = "IN"
        dir_label = Gtk.Label()
        dir_label.set_markup(f"<small><b>{icon_text}</b></small>")
        dir_label.set_width_chars(4)
        hbox.pack_start(dir_label, False, False, 0)

        # Port name (main label)
        name_label = Gtk.Label(label=route["description"])
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_max_width_chars(30)
        hbox.pack_start(name_label, True, True, 0)

        # Status badges
        renamed = self._is_route_renamed(dev, route)
        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        if renamed:
            renamed_badge = Gtk.Label()
            renamed_badge.set_markup("<small>Renamed</small>")
            renamed_badge.get_style_context().add_class("dim-label")
            badge_box.pack_start(renamed_badge, False, False, 0)

        if route["available"] == "yes":
            avail_badge = Gtk.Label()
            avail_badge.set_markup("<small>Plugged in</small>")
            avail_badge.get_style_context().add_class("dim-label")
            badge_box.pack_start(avail_badge, False, False, 0)

        hbox.pack_start(badge_box, False, False, 0)

        # Action buttons
        rename_btn = Gtk.Button(label="Rename")
        rename_btn.connect("clicked", self._on_rename_clicked, dev, route)
        hbox.pack_end(rename_btn, False, False, 0)

        if renamed:
            revert_btn = Gtk.Button(label="Revert")
            revert_btn.connect("clicked", self._on_revert_clicked, dev, route)
            hbox.pack_end(revert_btn, False, False, 0)

        row.add(hbox)
        return row

    def _on_rename_clicked(self, button, dev, route):
        dialog = Gtk.Dialog(
            title="Rename Audio Port",
            parent=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Rename", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)

        label = Gtk.Label()
        label.set_markup(f"Rename <b>{GLib.markup_escape_text(route['name'])}</b>:")
        label.set_halign(Gtk.Align.START)
        content.add(label)

        entry = Gtk.Entry()
        entry.set_text(route["description"])
        entry.set_activates_default(True)
        content.add(entry)

        dialog.show_all()
        response = dialog.run()
        new_name = entry.get_text().strip()
        dialog.destroy()

        if response == Gtk.ResponseType.OK and new_name and new_name != route["description"]:
            try:
                validate_port_name(new_name)
            except ValueError as e:
                self._show_error(str(e))
                return
            self._do_rename(route["name"], new_name, dev["device_name"])

    def _on_revert_clicked(self, button, dev, route):
        if route["rename_method"] == "wireplumber":
            orig = "original"
        else:
            orig = get_original_description(route["name"]) or "original"
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            message_format=f"Revert '{route['description']}' to its original name ({orig})?",
        )
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            self._do_revert(route["name"], dev["device_name"])

    def _on_auto_mute_toggle(self, button, card):
        current = get_auto_mute_status(card)
        new_state = current != "Enabled"
        try:
            set_auto_mute(card, new_state)
            button.set_label(f"Auto-Mute: {'Enabled' if new_state else 'Disabled'}")
            if new_state:
                button.get_style_context().add_class("suggested-action")
            else:
                button.get_style_context().remove_class("suggested-action")
        except Exception as e:
            self._show_error(f"Failed to toggle Auto-Mute: {e}")

    def _extract_error(self, stderr):
        """Extract a clean error message from stderr, ignoring tracebacks."""
        if not stderr or not stderr.strip():
            return "Authentication cancelled or failed"
        lines = stderr.strip().splitlines()
        # Walk backwards to find the last meaningful error line
        for line in reversed(lines):
            line = line.strip()
            if line and not line.startswith(("File ", "Traceback", "During handling")):
                # Strip exception class prefix if present (e.g. "RuntimeError: ...")
                if ": " in line and line[0].isupper():
                    return line.split(": ", 1)[1]
                return line
        return lines[-1].strip()

    def _do_rename(self, route_name, new_name, device_name):
        result = run_as_root(["rename", route_name, new_name, "--device", device_name])
        if result.returncode == 0:
            self._show_info(f"Renamed to '{new_name}'. Refreshing...")
            GLib.timeout_add(2000, self._build_device_list)
        else:
            self._show_error(f"Rename failed: {self._extract_error(result.stderr)}")

    def _do_revert(self, route_name, device_name):
        result = run_as_root(["revert", route_name, "--device", device_name])
        if result.returncode == 0:
            self._show_info("Reverted to original name. Refreshing...")
            GLib.timeout_add(2000, self._build_device_list)
        else:
            self._show_error(f"Revert failed: {self._extract_error(result.stderr)}")

    def _on_repair_clicked(self, button):
        result = run_as_root(["repair"])
        if result.returncode == 0:
            self._show_info(result.stdout.strip() or "Repair complete. Refreshing...")
        else:
            self._show_error(f"Repair failed: {self._extract_error(result.stderr)}")
        GLib.timeout_add(2000, self._build_device_list)

    def _show_error(self, message):
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            message_format=message,
        )
        dialog.run()
        dialog.destroy()

    def _show_info(self, message):
        dialog = Gtk.MessageDialog(
            parent=self,
            flags=Gtk.DialogFlags.MODAL,
            type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            message_format=message,
        )
        dialog.run()
        dialog.destroy()


def run_gui():
    win = PortNameWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
