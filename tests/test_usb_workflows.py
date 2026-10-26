"""Regression coverage for device selection and complete USB rename workflows."""

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from portname import cli, core, wireplumber


@pytest.fixture
def usb_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)
    monkeypatch.setattr(core, "PATHS_DIR", str(tmp_path / "paths"))
    monkeypatch.setattr(core, "_has_dpkg_divert", lambda: False)
    monkeypatch.setattr(core, "_RENAME_STORE_PATH", str(tmp_path / "names.json"))
    monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path / "conf"))
    monkeypatch.setattr(wireplumber, "LUA_DIR", str(tmp_path / "lua"))
    restart = Mock()
    monkeypatch.setattr(core, "restart_pipewire", restart)
    return restart


@pytest.mark.parametrize("fmt", ["lua", "conf"])
@pytest.mark.parametrize("damage", ["overwritten", "missing", "malformed"])
def test_repair_and_revert_all_do_not_resurrect_names(usb_setup, monkeypatch, fmt, damage):
    monkeypatch.setattr(wireplumber, "_FORMAT", fmt)
    node = "alsa_input.usb-Brio.mono-fallback"
    core.rename_port("analog-input-mic", 'Desk "Mic"', method="wireplumber", node_name=node)
    path = Path(wireplumber._config_path(node))
    if damage == "overwritten":
        wireplumber.rename_node(node, "Wrong Name")
    elif damage == "missing":
        path.unlink()
    else:
        path.write_text("# broken rule")
    usb_setup.reset_mock()
    assert core.check_renames() == {"repaired": [node], "failed": []}
    assert wireplumber.get_current_description(node) == 'Desk "Mic"'
    usb_setup.assert_called_once()
    usb_setup.reset_mock()
    assert core.revert_all() == {"reverted": [node], "failed": []}
    assert not path.exists()
    assert core._get_stored_custom_name("wireplumber", node) is None
    usb_setup.assert_called_once()
    assert core.check_renames() == {"repaired": [], "failed": []}
    assert not path.exists()


def test_revert_all_clears_saved_name_when_rule_already_missing(usb_setup):
    node = "alsa_input.usb-Brio.mono-fallback"
    core._store_custom_name("wireplumber", node, "Desk Mic")
    assert core.revert_all() == {"reverted": [node], "failed": []}
    assert core.check_renames() == {"repaired": [], "failed": []}
    assert not wireplumber.is_node_renamed(node)


def test_revert_failure_preserves_saved_name_and_reports_failure(usb_setup, monkeypatch):
    node = "alsa_input.usb-Brio.mono-fallback"
    core.rename_port("analog-input-mic", "Desk Mic", method="wireplumber", node_name=node)
    def fail(_node):
        raise RuntimeError("cannot remove rule")
    monkeypatch.setattr(wireplumber, "revert_node", fail)
    usb_setup.reset_mock()
    assert core.revert_all() == {"reverted": [], "failed": [(node, "cannot remove rule")]}
    assert core._get_stored_custom_name("wireplumber", node) == "Desk Mic"
    usb_setup.assert_not_called()


def shared_devices():
    return [
        {"device_name": "alsa_card.usb-" + name, "node_names": ["alsa_output.usb-" + name],
         "routes": [{"name": "analog-stereo", "direction": "Output", "rename_method": "wireplumber"}]}
        for name in ("First", "Second")
    ]


def test_ambiguous_route_requires_device(monkeypatch):
    monkeypatch.setattr(core, "get_devices", shared_devices)
    with pytest.raises(ValueError, match="Specify --device"):
        core.find_route("analog-stereo")
    dev, _route = core.find_route("analog-stereo", device_name="alsa_card.usb-Second")
    assert dev["device_name"] == "alsa_card.usb-Second"
    assert core.find_route("analog-stereo", device_name="missing") == (None, None)


def test_cli_selects_second_device_and_leaves_first_alone(usb_setup, monkeypatch):
    monkeypatch.setattr(core, "get_devices", shared_devices)
    monkeypatch.setattr(cli, "ensure_root_or_exit", lambda: None)
    args = argparse.Namespace(route="analog-stereo", name="Second Headset",
                              device="alsa_card.usb-Second", all=False)
    cli.cmd_rename(args)
    assert wireplumber.get_current_description("alsa_output.usb-Second") == "Second Headset"
    assert not wireplumber.is_node_renamed("alsa_output.usb-First")
    cli.cmd_revert(args)
    assert not wireplumber.is_node_renamed("alsa_output.usb-Second")
    assert core.check_renames() == {"repaired": [], "failed": []}


def test_cli_ambiguity_fails_without_changing_either_device(usb_setup, monkeypatch, capsys):
    monkeypatch.setattr(core, "get_devices", shared_devices)
    monkeypatch.setattr(cli, "ensure_root_or_exit", lambda: None)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_rename(argparse.Namespace(route="analog-stereo", name="Headset", device=None))
    assert exc.value.code == 1
    assert "Specify --device" in capsys.readouterr().err
    assert wireplumber.get_all_renamed() == []
    usb_setup.assert_not_called()


def test_cli_reports_partial_revert_as_failure(monkeypatch):
    monkeypatch.setattr(cli, "ensure_root_or_exit", lambda: None)
    monkeypatch.setattr(cli, "revert_all", lambda: {"reverted": ["good"], "failed": [("bad", "error")]})
    with pytest.raises(SystemExit) as exc:
        cli.cmd_revert(argparse.Namespace(all=True))
    assert exc.value.code == 1


@pytest.mark.parametrize("action", ["rename", "revert"])
def test_gui_passes_selected_device_through_privilege_boundary(monkeypatch, action):
    gui = pytest.importorskip("portname.gui")
    result = SimpleNamespace(returncode=0, stdout="", stderr="")
    root = Mock(return_value=result)
    monkeypatch.setattr(gui, "run_as_root", root)
    monkeypatch.setattr(gui.GLib, "timeout_add", Mock())
    window = SimpleNamespace(_show_info=Mock(), _show_error=Mock(), _build_device_list=Mock())
    if action == "rename":
        gui.PortNameWindow._do_rename(window, "analog-stereo", "Second Headset", "alsa_card.usb-Second")
        expected = ["rename", "analog-stereo", "Second Headset", "--device", "alsa_card.usb-Second"]
    else:
        gui.PortNameWindow._do_revert(window, "analog-stereo", "alsa_card.usb-Second")
        expected = ["revert", "analog-stereo", "--device", "alsa_card.usb-Second"]
    root.assert_called_once_with(expected)
