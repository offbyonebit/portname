"""Tests for portname core module."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock, mock_open

from portname.core import (
    _read_description,
    _modify_description,
    _has_dpkg_divert,
    _ensure_backup,
    _restore_backup,
    _store_custom_name,
    _get_stored_custom_name,
    _clear_custom_name,
    _custom_name_store_path,
    _load_custom_name_store,
    get_devices,
    is_renamed,
    validate_port_name,
    find_route,
    node_name_for_route,
    get_all_renamed_path_files,
    check_renames,
    revert_all,
)


SAMPLE_PATH_FILE = """# Comment
; Another comment

[General]
priority = 90
description-key = analog-output-lineout

[Jack Line Out]
required-any = any

[Element Master]
switch = mute
volume = merge

.include analog-output.conf.common
"""

SAMPLE_PATH_FILE_RENAMED = """# Comment
; Another comment

[General]
priority = 90
description = JBL Headset

[Jack Line Out]
required-any = any

[Element Master]
switch = mute
volume = merge

.include analog-output.conf.common
"""

SAMPLE_PATH_FILE_NO_DESCRIPTION = """# Broken file

[General]
priority = 90

[Jack Line Out]
required-any = any
"""

SAMPLE_PW_DUMP = [
    {
        "id": 47,
        "type": "PipeWire:Interface:Device",
        "info": {
            "props": {
                "device.api": "alsa",
                "device.name": "alsa_card.pci-0000_2d_00.4",
                "device.description": "Starship/Matisse HD Audio Controller",
                "alsa.card": 1,
                "alsa.card_name": "HD-Audio Generic",
            },
            "params": {
                "EnumRoute": [
                    {
                        "index": 0,
                        "direction": "Output",
                        "name": "analog-output-lineout",
                        "description": "Line Out",
                        "available": "yes",
                    },
                    {
                        "index": 1,
                        "direction": "Output",
                        "name": "analog-output-headphones",
                        "description": "Headphones",
                        "available": "yes",
                    },
                    {
                        "index": 2,
                        "direction": "Input",
                        "name": "analog-input-rear-mic",
                        "description": "Rear Microphone",
                        "available": "yes",
                    },
                ],
            },
        },
    }
]


class TestValidatePortName(unittest.TestCase):
    def test_valid_name(self):
        self.assertEqual(validate_port_name("JBL Headset"), "JBL Headset")

    def test_strips_whitespace(self):
        self.assertEqual(validate_port_name("  My Speaker  "), "My Speaker")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            validate_port_name("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            validate_port_name("   ")

    def test_control_chars_raises(self):
        with self.assertRaises(ValueError):
            validate_port_name("Bad\x00Name")

    def test_too_long_raises(self):
        with self.assertRaises(ValueError):
            validate_port_name("A" * 65)

    def test_max_length_ok(self):
        name = "A" * 64
        self.assertEqual(validate_port_name(name), name)

    def test_unicode_ok(self):
        self.assertEqual(validate_port_name("Haut-parleurs"), "Haut-parleurs")


class TestReadDescription(unittest.TestCase):
    def test_reads_description_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(SAMPLE_PATH_FILE)
            f.flush()
            result = _read_description(f.name)
        os.unlink(f.name)
        self.assertEqual(result, "analog-output-lineout")

    def test_reads_description_value(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(SAMPLE_PATH_FILE_RENAMED)
            f.flush()
            result = _read_description(f.name)
        os.unlink(f.name)
        self.assertEqual(result, "JBL Headset")

    def test_returns_none_for_missing_description(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(SAMPLE_PATH_FILE_NO_DESCRIPTION)
            f.flush()
            result = _read_description(f.name)
        os.unlink(f.name)
        self.assertIsNone(result)

    def test_returns_none_for_nonexistent_file(self):
        result = _read_description("/nonexistent/path.conf")
        self.assertIsNone(result)


class TestModifyDescription(unittest.TestCase):
    def test_replaces_description_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(SAMPLE_PATH_FILE)
            f.flush()
            result = _modify_description(f.name, "My Speakers")
        os.unlink(f.name)
        self.assertIn("description = My Speakers", result)
        self.assertNotIn("description-key", result)

    def test_replaces_existing_description(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(SAMPLE_PATH_FILE_RENAMED)
            f.flush()
            result = _modify_description(f.name, "New Name")
        os.unlink(f.name)
        self.assertIn("description = New Name", result)
        self.assertNotIn("JBL Headset", result)

    def test_preserves_other_sections(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(SAMPLE_PATH_FILE)
            f.flush()
            result = _modify_description(f.name, "Test")
        os.unlink(f.name)
        self.assertIn("[Jack Line Out]", result)
        self.assertIn("[Element Master]", result)
        self.assertIn("priority = 90", result)
        self.assertIn(".include analog-output.conf.common", result)

    def test_raises_on_missing_description(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
            f.write(SAMPLE_PATH_FILE_NO_DESCRIPTION)
            f.flush()
            with self.assertRaises(RuntimeError):
                _modify_description(f.name, "Test")
        os.unlink(f.name)

    def test_raises_on_nonexistent_file(self):
        with self.assertRaises(RuntimeError):
            _modify_description("/nonexistent/path.conf", "Test")


class TestGetDevices(unittest.TestCase):
    @patch("portname.core.os.path.exists")
    @patch("portname.core.shutil.which", return_value="/usr/bin/pw-dump")
    @patch("portname.core.subprocess.run")
    def test_parses_pw_dump(self, mock_run, mock_which, mock_exists):
        mock_run.return_value = MagicMock(
            stdout=json.dumps(SAMPLE_PW_DUMP),
            returncode=0,
        )
        mock_exists.return_value = True

        devices = get_devices()

        self.assertEqual(len(devices), 1)
        dev = devices[0]
        self.assertEqual(dev["device_description"], "Starship/Matisse HD Audio Controller")
        self.assertEqual(dev["alsa_card"], "1")
        self.assertEqual(len(dev["routes"]), 3)
        self.assertEqual(dev["routes"][0]["name"], "analog-output-lineout")
        self.assertEqual(dev["routes"][0]["direction"], "Output")

    @patch("portname.core.os.path.exists", return_value=False)
    @patch("portname.core.shutil.which", return_value="/usr/bin/pw-dump")
    @patch("portname.core.subprocess.run")
    def test_links_nodes_by_device_id(self, mock_run, mock_which, mock_exists):
        """Newer PipeWire nodes carry device.id (no device.name)."""
        dump = [
            {"id": 60, "type": "PipeWire:Interface:Device", "info": {
                "props": {"device.api": "alsa", "device.name": "alsa_card.usb-X",
                          "device.bus": "usb", "device.description": "USB Mic"},
                "params": {"EnumRoute": [{"name": "analog-input-mic",
                                          "description": "Mic", "direction": "Input"}]}}},
            {"id": 46, "type": "PipeWire:Interface:Node", "info": {
                "props": {"node.name": "alsa_input.usb-X.mono-fallback",
                          "device.id": 60, "media.class": "Audio/Source"}}},
        ]
        mock_run.return_value = MagicMock(stdout=json.dumps(dump), returncode=0)
        devices = get_devices()
        self.assertEqual(devices[0]["node_names"], ["alsa_input.usb-X.mono-fallback"])

    @patch("portname.core.shutil.which", return_value=None)
    def test_raises_when_pw_dump_missing(self, mock_which):
        with self.assertRaises(RuntimeError) as ctx:
            get_devices()
        self.assertIn("pw-dump not found", str(ctx.exception))

    @patch("portname.core.shutil.which", return_value="/usr/bin/pw-dump")
    @patch("portname.core.subprocess.run")
    def test_raises_on_invalid_json(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(stdout="not json", returncode=0)
        with self.assertRaises(RuntimeError) as ctx:
            get_devices()
        self.assertIn("invalid JSON", str(ctx.exception))


class TestIsRenamed(unittest.TestCase):
    @patch("portname.core._has_dpkg_divert", return_value=False)
    @patch("portname.core.os.path.exists")
    def test_returns_true_when_orig_exists(self, mock_exists, mock_divert):
        mock_exists.side_effect = lambda p: p.endswith(".orig") or not p.endswith(".orig")
        self.assertTrue(is_renamed("analog-output-lineout"))

    @patch("portname.core._has_dpkg_divert", return_value=False)
    @patch("portname.core.os.path.exists")
    def test_returns_false_when_no_orig(self, mock_exists, mock_divert):
        def exists_side_effect(path):
            if path.endswith(".orig"):
                return False
            return True
        mock_exists.side_effect = exists_side_effect
        self.assertFalse(is_renamed("analog-output-lineout"))

    @patch("portname.core._has_dpkg_divert", return_value=True)
    @patch("portname.core._get_diverted_paths", return_value={"/usr/share/alsa-card-profile/mixer/paths/foo.conf"})
    @patch("portname.core.os.path.exists")
    def test_uses_dpkg_divert_state_on_debian(self, mock_exists, mock_diverted, mock_divert):
        # Even if .orig exists, is_renamed should consult dpkg-divert's database.
        mock_exists.side_effect = lambda p: p.endswith(".orig")
        self.assertTrue(is_renamed("foo"))
        self.assertFalse(is_renamed("bar"))


class TestHasDpkgDivert(unittest.TestCase):
    @patch("portname.core.shutil.which", return_value="/usr/bin/dpkg-divert")
    def test_returns_true_when_available(self, mock_which):
        self.assertTrue(_has_dpkg_divert())

    @patch("portname.core.shutil.which", return_value=None)
    def test_returns_false_when_unavailable(self, mock_which):
        self.assertFalse(_has_dpkg_divert())


class TestEnsureBackup(unittest.TestCase):
    @patch("portname.core._has_dpkg_divert", return_value=False)
    @patch("portname.core.shutil.copy2")
    @patch("portname.core.os.path.exists", return_value=False)
    def test_manual_backup_on_non_debian(self, mock_exists, mock_copy, mock_divert):
        _ensure_backup("/path/file.conf")
        mock_copy.assert_called_once_with("/path/file.conf", "/path/file.conf.orig")

    @patch("portname.core._has_dpkg_divert", return_value=True)
    @patch("portname.core._get_diversions", return_value={})
    @patch("portname.core._get_diverted_paths", return_value=set())
    @patch("portname.core.subprocess.run")
    @patch("portname.core.os.path.exists", return_value=False)
    def test_dpkg_divert_on_debian(self, mock_exists, mock_run, mock_diverted, mock_targets, mock_divert):
        mock_run.return_value = MagicMock(returncode=0)
        _ensure_backup("/path/file.conf")
        mock_run.assert_called_once()
        args, _kwargs = mock_run.call_args
        self.assertEqual(args[0][0], "dpkg-divert")
        self.assertEqual(args[0][args[0].index("--divert") + 1], "/path/file.conf.orig")

    @patch("portname.core._has_dpkg_divert", return_value=True)
    @patch("portname.core._get_diverted_paths", return_value={"/path/file.conf"})
    @patch("portname.core.subprocess.run")
    @patch("portname.core.os.path.exists", return_value=False)
    def test_detects_inconsistent_divert_state(self, mock_exists, mock_run, mock_diverted, mock_divert):
        with self.assertRaises(RuntimeError):
            _ensure_backup("/path/file.conf")


class TestRestoreBackup(unittest.TestCase):
    @patch("portname.core._has_dpkg_divert", return_value=False)
    @patch("portname.core.os.remove")
    @patch("portname.core.shutil.copy2")
    @patch("portname.core.os.path.exists", return_value=True)
    def test_manual_restore_on_non_debian(self, mock_exists, mock_copy, mock_remove, mock_divert):
        _restore_backup("/path/file.conf")
        mock_copy.assert_called_once_with("/path/file.conf.orig", "/path/file.conf")
        mock_remove.assert_called_once_with("/path/file.conf.orig")


class TestFindRoute(unittest.TestCase):
    @patch("portname.core.get_devices")
    def test_finds_route_by_name(self, mock_get_devices):
        mock_get_devices.return_value = [SAMPLE_PW_DUMP[0]]
        # Convert sample dump to expected device shape by mocking get_devices return
        device = {
            "device_name": "alsa_card.pci-0000_2d_00.4",
            "device_description": "Starship/Matisse HD Audio Controller",
            "alsa_card": "1",
            "device_bus": "",
            "node_names": [],
            "routes": [
                {"name": "analog-output-lineout", "description": "Line Out",
                 "direction": "Output", "available": "yes", "rename_method": "path_file"},
            ],
        }
        mock_get_devices.return_value = [device]
        dev, route = find_route("analog-output-lineout")
        self.assertIsNotNone(route)
        self.assertEqual(route["name"], "analog-output-lineout")

    @patch("portname.core.get_devices", return_value=[])
    def test_returns_none_when_not_found(self, mock_get_devices):
        dev, route = find_route("missing")
        self.assertIsNone(dev)
        self.assertIsNone(route)


class TestGetAllRenamedPathFiles(unittest.TestCase):
    @patch("portname.core._has_dpkg_divert", return_value=True)
    @patch("portname.core.subprocess.run")
    def test_parses_dpkg_divert_output(self, mock_run, mock_divert):
        mock_run.return_value = MagicMock(
            stdout="local diversion of /usr/share/alsa-card-profile/mixer/paths/foo.conf to /usr/share/alsa-card-profile/mixer/paths/foo.conf.orig\n",
            returncode=0,
        )
        result = get_all_renamed_path_files()
        self.assertIn("foo", result)

    @patch("portname.core._has_dpkg_divert", return_value=False)
    @patch("portname.core.os.listdir")
    @patch("portname.core.os.path.isdir", return_value=True)
    def test_scans_orig_files_on_non_debian(self, mock_isdir, mock_listdir, mock_divert):
        mock_listdir.return_value = ["bar.conf.orig", "baz.conf"]
        result = get_all_renamed_path_files()
        self.assertEqual(result, ["bar"])


class TestNodeNameForRoute(unittest.TestCase):
    def test_picks_output_node_for_output_route(self):
        dev = {
            "node_names": [
                "alsa_output.usb-Generic_USB_Audio_1234-00.analog-stereo",
                "alsa_input.usb-Generic_USB_Audio_1234-00.analog-stereo",
            ]
        }
        route = {"direction": "Output"}
        self.assertEqual(
            node_name_for_route(dev, route),
            "alsa_output.usb-Generic_USB_Audio_1234-00.analog-stereo",
        )

    def test_picks_input_node_for_input_route(self):
        dev = {
            "node_names": [
                "alsa_output.usb-Generic_USB_Audio_1234-00.analog-stereo",
                "alsa_input.usb-Generic_USB_Audio_1234-00.analog-stereo",
            ]
        }
        route = {"direction": "Input"}
        self.assertEqual(
            node_name_for_route(dev, route),
            "alsa_input.usb-Generic_USB_Audio_1234-00.analog-stereo",
        )

    def test_returns_only_node_when_single(self):
        dev = {"node_names": ["alsa_output.usb-..."], "routes": []}
        route = {"direction": "Output"}
        self.assertEqual(node_name_for_route(dev, route), "alsa_output.usb-...")


class TestCheckRenames(unittest.TestCase):
    def setUp(self):
        # These unit tests must never inspect the host's audio files or database.
        patcher = patch("portname.core.PATHS_DIR", "/nonexistent-portname-test-paths")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("portname.core._get_diversions", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("portname.core.os.geteuid", return_value=0)
    @patch("portname.core._load_custom_name_store")
    @patch("portname.core.get_all_renamed_path_files", return_value=[])
    @patch("portname.core.get_path_file", return_value="/paths/analog-output-lineout.conf")
    @patch("portname.core._current_description", side_effect=["Old", "New"])
    @patch("portname.core._read_description", return_value="Original")
    @patch("portname.core._modify_description", return_value="description = New")
    @patch("portname.core._store_custom_name")
    @patch("portname.core.restart_pipewire")
    @patch("portname.core.open", new_callable=mock_open)
    def test_repairs_clobbered_path_file_rename(
        self, mock_open, mock_restart, mock_store, mock_modify,
        mock_read, mock_current, mock_get_path, mock_get_renamed, mock_load_store, mock_euid
    ):
        mock_load_store.return_value = {
            "path_file": {"analog-output-lineout": "New"},
            "wireplumber": {},
        }
        result = check_renames()
        self.assertEqual(result["repaired"], ["analog-output-lineout"])
        self.assertEqual(result["failed"], [])

    @patch("portname.core.os.geteuid", return_value=0)
    @patch("portname.core._load_custom_name_store")
    @patch("portname.core.get_all_renamed_path_files", return_value=["orphan-route"])
    @patch("portname.core.get_path_file", return_value="/paths/orphan-route.conf")
    @patch("portname.core._current_description", return_value="Clobbered Name")
    @patch("portname.core._read_description", return_value="Original")
    @patch("portname.core._modify_description", return_value="description = Clobbered Name")
    @patch("portname.core._store_custom_name")
    @patch("portname.core.restart_pipewire")
    def test_derives_custom_name_for_route_missing_from_sidecar(
        self, mock_restart, mock_store, mock_modify,
        mock_read, mock_current, mock_get_path, mock_get_renamed, mock_load_store, mock_euid
    ):
        # A route that is genuinely diverted/backed up on disk but has no
        # sidecar entry (e.g. sidecar was wiped, or rename predates the
        # sidecar) must still be checked, not silently skipped.
        mock_load_store.return_value = {"path_file": {}, "wireplumber": {}}
        result = check_renames()
        self.assertEqual(result["repaired"], [])
        mock_store.assert_called_once_with("path_file", "orphan-route", "Clobbered Name")

    @patch("portname.core.os.geteuid", return_value=0)
    @patch("portname.core._load_custom_name_store")
    @patch("portname.core.wireplumber.get_all_renamed", return_value=[])
    @patch("portname.core.wireplumber.rename_node")
    @patch("portname.core.restart_pipewire")
    def test_repairs_missing_wireplumber_rule(
        self, mock_restart, mock_rename_node, mock_wp_renamed,
        mock_load_store, mock_euid
    ):
        mock_load_store.return_value = {
            "path_file": {},
            "wireplumber": {"alsa_output.usb-...": "USB Headset"},
        }
        result = check_renames()
        self.assertEqual(result["repaired"], ["alsa_output.usb-..."])
        mock_rename_node.assert_called_once_with("alsa_output.usb-...", "USB Headset")

    @patch("portname.core.os.geteuid", return_value=0)
    @patch("portname.core._load_custom_name_store")
    @patch("portname.core.get_all_renamed_path_files", return_value=[])
    @patch("portname.core.get_path_file", side_effect=FileNotFoundError("missing"))
    @patch("portname.core.restart_pipewire")
    def test_reports_path_file_errors_without_crashing(
        self, mock_restart, mock_get_path, mock_get_renamed, mock_load_store, mock_euid
    ):
        mock_load_store.return_value = {
            "path_file": {"missing-route": "Name"},
            "wireplumber": {},
        }
        result = check_renames()
        self.assertEqual(result["repaired"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0][0], "missing-route")


class TestRevertAll(unittest.TestCase):
    @patch("portname.core.os.geteuid", return_value=0)
    @patch("portname.core.get_all_renamed_path_files", return_value=["foo"])
    @patch("portname.core.wireplumber.get_all_renamed", return_value=[])
    @patch("portname.core.revert_port")
    def test_reports_reverted_and_failed(
        self, mock_revert, mock_wp_renamed, mock_get_renamed, mock_euid
    ):
        mock_revert.side_effect = [None, RuntimeError("boom")]
        mock_get_renamed.return_value = ["foo", "bar"]
        with patch("portname.core.restart_pipewire") as restart, patch(
            "portname.core._load_custom_name_store", return_value={"path_file": {}, "wireplumber": {}}
        ):
            result = revert_all()
        restart.assert_called_once()
        self.assertEqual(result["reverted"], ["foo"])
        self.assertEqual(result["failed"], [("bar", "boom")])


class TestCustomNameStore(unittest.TestCase):
    @patch("portname.core._custom_name_store_path")
    def test_store_and_retrieve_custom_name(self, mock_path):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            mock_path.return_value = f.name
            _store_custom_name("path_file", "foo", "My Name")
            self.assertEqual(_get_stored_custom_name("path_file", "foo"), "My Name")
            _store_custom_name("wireplumber", "node1", "USB Name")
            self.assertEqual(_get_stored_custom_name("wireplumber", "node1"), "USB Name")
            _clear_custom_name("path_file", "foo")
            self.assertIsNone(_get_stored_custom_name("path_file", "foo"))
        os.unlink(f.name)

    @patch("portname.core._custom_name_store_path")
    def test_migrates_legacy_flat_format(self, mock_path):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write('{"legacy-route": "Legacy Name"}')
            f.flush()
            mock_path.return_value = f.name
            store = _load_custom_name_store()
            self.assertEqual(store["path_file"], {"legacy-route": "Legacy Name"})
            self.assertEqual(store["wireplumber"], {})
        os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
