"""Tests for portname CLI."""

import argparse
import sys
from unittest.mock import patch, MagicMock

import pytest

from portname import cli


SAMPLE_DEVICE = {
    "device_name": "alsa_card.pci-0000_2d_00.4",
    "device_description": "Starship/Matisse HD Audio Controller",
    "alsa_card": "1",
    "device_bus": "pci",
    "node_names": [],
    "routes": [
        {
            "name": "analog-output-lineout",
            "description": "Line Out",
            "direction": "Output",
            "available": "yes",
            "rename_method": "path_file",
        },
        {
            "name": "analog-input-rear-mic",
            "description": "Rear Microphone",
            "direction": "Input",
            "available": "no",
            "rename_method": "path_file",
        },
    ],
}

SAMPLE_USB_DEVICE = {
    "device_name": "alsa_card.usb-Generic_USB_Audio_1234-00",
    "device_description": "Generic USB Audio",
    "alsa_card": "2",
    "device_bus": "usb",
    "node_names": ["alsa_output.usb-Generic_USB_Audio_1234-00.analog-stereo"],
    "routes": [
        {
            "name": "analog-stereo",
            "description": "Analog Stereo",
            "direction": "Output",
            "available": "yes",
            "rename_method": "wireplumber",
        },
    ],
}


class TestCmdList:
    """Tests for the list command."""

    @patch("portname.cli.get_devices", return_value=[SAMPLE_DEVICE])
    @patch("portname.cli.is_renamed", return_value=False)
    def test_lists_devices(self, mock_is_renamed, mock_get_devices, capsys):
        cli.cmd_list(MagicMock())
        captured = capsys.readouterr()
        assert "Starship/Matisse HD Audio Controller" in captured.out
        assert "analog-output-lineout" in captured.out
        assert "Line Out" in captured.out

    @patch("portname.cli.get_devices", return_value=[])
    def test_no_devices_message(self, mock_get_devices, capsys):
        cli.cmd_list(MagicMock())
        assert "No audio devices found" in capsys.readouterr().out


class TestCmdRename:
    """Tests for the rename command."""

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.find_route", return_value=(SAMPLE_DEVICE, SAMPLE_DEVICE["routes"][0]))
    @patch("portname.cli.rename_port")
    def test_renames_path_file_route(self, mock_rename, mock_find, mock_root, capsys):
        args = argparse.Namespace(route="analog-output-lineout", name="JBL Headset")
        cli.cmd_rename(args)
        mock_rename.assert_called_once_with(
            "analog-output-lineout", "JBL Headset", method="path_file", node_name=None
        )
        assert "Renamed 'analog-output-lineout' to 'JBL Headset'" in capsys.readouterr().out

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.find_route", return_value=(SAMPLE_USB_DEVICE, SAMPLE_USB_DEVICE["routes"][0]))
    @patch("portname.cli.rename_port")
    def test_renames_wireplumber_route(self, mock_rename, mock_find, mock_root):
        args = argparse.Namespace(route="analog-stereo", name="USB Headset")
        cli.cmd_rename(args)
        mock_rename.assert_called_once_with(
            "analog-stereo", "USB Headset", method="wireplumber",
            node_name="alsa_output.usb-Generic_USB_Audio_1234-00.analog-stereo",
        )

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.find_route", return_value=(None, None))
    def test_exits_when_route_not_found(self, mock_find, mock_root, capsys):
        args = argparse.Namespace(route="missing-route", name="Name")
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_rename(args)
        assert exc_info.value.code == 1
        assert "not found" in capsys.readouterr().err

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.find_route", return_value=(SAMPLE_DEVICE, SAMPLE_DEVICE["routes"][0]))
    @patch("portname.cli.rename_port", side_effect=RuntimeError("write failed"))
    def test_prints_error_and_exits(self, mock_rename, mock_find, mock_root, capsys):
        args = argparse.Namespace(route="analog-output-lineout", name="Name")
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_rename(args)
        assert exc_info.value.code == 1
        assert "write failed" in capsys.readouterr().err


class TestCmdRevert:
    """Tests for the revert command."""

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.find_route", return_value=(SAMPLE_DEVICE, SAMPLE_DEVICE["routes"][0]))
    @patch("portname.cli.revert_port")
    def test_reverts_single_route(self, mock_revert, mock_find, mock_root, capsys):
        args = argparse.Namespace(route="analog-output-lineout", all=False)
        cli.cmd_revert(args)
        mock_revert.assert_called_once_with(
            "analog-output-lineout", method="path_file", node_name=None
        )
        assert "Reverted 'analog-output-lineout'" in capsys.readouterr().out

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.revert_all", return_value={"reverted": ["analog-output-lineout"], "failed": []})
    def test_reverts_all(self, mock_revert_all, mock_root, capsys):
        args = argparse.Namespace(route=None, all=True)
        cli.cmd_revert(args)
        assert "analog-output-lineout" in capsys.readouterr().out

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.find_route", return_value=(None, None))
    def test_exits_when_route_not_found(self, mock_find, mock_root, capsys):
        args = argparse.Namespace(route="missing", all=False)
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_revert(args)
        assert exc_info.value.code == 1


class TestCmdCheck:
    """Tests for the check command."""

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.check_renames", return_value={"repaired": ["analog-output-lineout"], "failed": []})
    def test_reports_repaired_renames(self, mock_check, mock_root, capsys):
        cli.cmd_check(MagicMock())
        out = capsys.readouterr().out
        assert "Re-applied" in out
        assert "analog-output-lineout" in out

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.check_renames", return_value={"repaired": [], "failed": []})
    def test_reports_no_clobbered_renames(self, mock_check, mock_root, capsys):
        cli.cmd_check(MagicMock())
        assert "No clobbered renames found" in capsys.readouterr().out

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.check_renames", return_value={"repaired": [], "failed": [("foo", "broken")]})
    def test_reports_failed_renames(self, mock_check, mock_root, capsys):
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_check(MagicMock())
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "could not be repaired" in err
        assert "foo" in err
        assert "broken" in err

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.check_renames", side_effect=FileNotFoundError("path missing"))
    def test_catches_file_not_found(self, mock_check, mock_root, capsys):
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_check(MagicMock())
        assert exc_info.value.code == 1
        assert "path missing" in capsys.readouterr().err

    @patch("portname.cli.ensure_root_or_exit")
    @patch("portname.cli.check_renames", side_effect=RuntimeError("pw-dump failed"))
    def test_prints_error_and_exits(self, mock_check, mock_root, capsys):
        with pytest.raises(SystemExit) as exc_info:
            cli.cmd_check(MagicMock())
        assert exc_info.value.code == 1
        assert "pw-dump failed" in capsys.readouterr().err


class TestMain:
    """Tests for CLI argument dispatch."""

    @patch("portname.cli.cmd_gui")
    def test_defaults_to_gui(self, mock_gui):
        with patch.object(sys, "argv", ["portname"]):
            cli.main()
        mock_gui.assert_called_once()

    @patch("portname.cli.cmd_list")
    def test_dispatches_list(self, mock_list):
        with patch.object(sys, "argv", ["portname", "list"]):
            cli.main()
        mock_list.assert_called_once()

    @patch("portname.cli.cmd_check")
    def test_dispatches_check(self, mock_check):
        with patch.object(sys, "argv", ["portname", "check"]):
            cli.main()
        mock_check.assert_called_once()

    @patch("portname.cli.cmd_check")
    def test_dispatches_repair(self, mock_check):
        with patch.object(sys, "argv", ["portname", "repair"]):
            cli.main()
        mock_check.assert_called_once()
