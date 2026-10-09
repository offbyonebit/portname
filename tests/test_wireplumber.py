"""Tests for wireplumber module."""

import os
import tempfile

import pytest

from portname import wireplumber


class TestGenerateRule:
    """Tests for generate_rule function."""

    def test_generates_valid_spa_json(self):
        """Test that generate_rule produces valid SPA-JSON config."""
        content = wireplumber.generate_rule(
            "alsa_output.usb-Generic_USB_Audio_1234-00.analog-stereo",
            "My USB Headset"
        )
        # Should contain the node name
        assert 'node.name = "alsa_output.usb-Generic_USB_Audio_1234-00.analog-stereo"' in content
        # Should contain the description (allowing for different spacing)
        assert 'node.description = "My USB Headset"' in content
        assert 'node.nick' in content and '"My USB Headset"' in content
        # Should be valid SPA-JSON structure
        assert "monitor.alsa.rules = [" in content

    def test_escapes_special_chars_in_description(self):
        """Test that special characters in description are escaped."""
        content = wireplumber.generate_rule(
            "test-node",
            'Name with "quotes" and \\ backslash'
        )
        # Quotes should be escaped
        assert 'node.description = "Name with \\"quotes\\" and \\\\ backslash"' in content

    def test_escapes_special_chars_in_node_name(self):
        """Test that special characters in node name are escaped."""
        content = wireplumber.generate_rule(
            'node-with-"quotes"',
            "Test Name"
        )
        # Node name quotes should be escaped
        assert 'node.name = "node-with-\\"quotes\\""' in content


class TestSafeFilename:
    """Tests for _safe_filename function."""

    def test_leaves_alphanumeric_unchanged(self):
        """Test that alphanumeric characters are preserved."""
        assert wireplumber._safe_filename("test123") == "test123"
        assert wireplumber._safe_filename("Test-Name_123") == "Test-Name_123"

    def test_replaces_special_chars_with_underscore(self):
        """Test that special characters become underscores."""
        assert wireplumber._safe_filename("name with spaces") == "name_with_spaces"
        assert wireplumber._safe_filename("name.with.dots") == "name_with_dots"
        assert wireplumber._safe_filename("name/with/slashes") == "name_with_slashes"

    def test_handles_usb_node_names(self):
        """Test that typical USB node names are sanitized."""
        node_name = "alsa_output.usb-Generic_USB_Audio_1234-00.analog-stereo"
        safe = wireplumber._safe_filename(node_name)
        assert "." not in safe
        assert "-" in safe  # Dashes should be preserved
        assert safe == "alsa_output_usb-Generic_USB_Audio_1234-00_analog-stereo"


class TestConfigPath:
    """Tests for _config_path function."""

    def test_returns_correct_path(self, monkeypatch):
        """Test that _config_path returns the correct file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", tmpdir)
            path = wireplumber._config_path("test-node")
            assert path.startswith(tmpdir)
            assert path.endswith(".conf")
            assert "90-portname-test-node" in path

    def test_sanitizes_node_name_in_filename(self, monkeypatch):
        """Test that node names are sanitized in the filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", tmpdir)
            path = wireplumber._config_path("node.with.dots")
            assert "node_with_dots" in path
            assert "." not in os.path.basename(path).replace(".conf", "")


class TestRenameNode:
    """Tests for rename_node function."""

    def test_raises_without_root(self, monkeypatch):
        """Test that rename_node requires root privileges."""
        monkeypatch.setattr(os, "geteuid", lambda: 1000)  # Non-root
        with pytest.raises(PermissionError, match="Must be run as root"):
            wireplumber.rename_node("test-node", "Test Name")

    def test_creates_config_file(self, monkeypatch, tmp_path):
        """Test that rename_node creates the config file."""
        monkeypatch.setattr(os, "geteuid", lambda: 0)  # Root
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        wireplumber.rename_node("test-node", "Test Name")

        config_file = tmp_path / "90-portname-test-node.conf"
        assert config_file.exists()
        content = config_file.read_text()
        assert "test-node" in content
        assert "Test Name" in content

    def test_creates_directory_if_needed(self, monkeypatch, tmp_path):
        """Test that rename_node creates the config directory if it doesn't exist."""
        monkeypatch.setattr(os, "geteuid", lambda: 0)  # Root
        config_dir = tmp_path / "wireplumber.conf.d"
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(config_dir))

        wireplumber.rename_node("test-node", "Test Name")

        assert config_dir.exists()


class TestRevertNode:
    """Tests for revert_node function."""

    def test_raises_without_root(self, monkeypatch):
        """Test that revert_node requires root privileges."""
        monkeypatch.setattr(os, "geteuid", lambda: 1000)  # Non-root
        with pytest.raises(PermissionError, match="Must be run as root"):
            wireplumber.revert_node("test-node")

    def test_removes_config_file(self, monkeypatch, tmp_path):
        """Test that revert_node removes the config file."""
        monkeypatch.setattr(os, "geteuid", lambda: 0)  # Root
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        # Create a config file first
        config_file = tmp_path / "90-portname-test-node.conf"
        config_file.write_text("# test config")

        wireplumber.revert_node("test-node")

        assert not config_file.exists()

    def test_raises_when_no_config_exists(self, monkeypatch, tmp_path):
        """Test that revert_node raises error when no config exists."""
        monkeypatch.setattr(os, "geteuid", lambda: 0)  # Root
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        with pytest.raises(ValueError, match="No rename rule found"):
            wireplumber.revert_node("nonexistent-node")


class TestIsNodeRenamed:
    """Tests for is_node_renamed function."""

    def test_returns_true_when_config_exists(self, monkeypatch, tmp_path):
        """Test that is_node_renamed returns True when config exists."""
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        config_file = tmp_path / "90-portname-test-node.conf"
        config_file.write_text("# test config")

        assert wireplumber.is_node_renamed("test-node") is True

    def test_returns_false_when_no_config(self, monkeypatch, tmp_path):
        """Test that is_node_renamed returns False when no config exists."""
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        assert wireplumber.is_node_renamed("nonexistent-node") is False


class TestGetCurrentDescription:
    """Tests for get_current_description function."""

    def test_returns_description_from_config(self, monkeypatch, tmp_path):
        """Test that get_current_description extracts the description."""
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        config_file = tmp_path / "90-portname-test-node.conf"
        config_file.write_text('''
monitor.alsa.rules = [
  {
    matches = [ { node.name = "test-node" } ]
    actions = {
      update-props = {
        node.description = "My Custom Name"
        node.nick        = "My Custom Name"
      }
    }
  }
]
''')

        desc = wireplumber.get_current_description("test-node")
        assert desc == "My Custom Name"

    def test_returns_none_when_no_config(self, monkeypatch, tmp_path):
        """Test that get_current_description returns None when no config."""
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        desc = wireplumber.get_current_description("nonexistent-node")
        assert desc is None


class TestGetAllRenamed:
    """Tests for get_all_renamed function."""

    def test_returns_list_of_renamed_nodes(self, monkeypatch, tmp_path):
        """Test that get_all_renamed finds all portname configs."""
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        # Create two config files
        (tmp_path / "90-portname-node1.conf").write_text('''
matches = [ { node.name = "node1" } ]
node.description = "Node One"
''')
        (tmp_path / "90-portname-node2.conf").write_text('''
matches = [ { node.name = "node2" } ]
node.description = "Node Two"
''')
        # Create a non-portname file that should be ignored
        (tmp_path / "other-file.conf").write_text("# not a portname file")

        renamed = wireplumber.get_all_renamed()
        assert len(renamed) == 2
        node_names = [name for name, _desc in renamed]
        assert "node1" in node_names
        assert "node2" in node_names

    def test_returns_empty_list_when_no_configs(self, monkeypatch, tmp_path):
        """Test that get_all_renamed returns empty list when no configs."""
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        renamed = wireplumber.get_all_renamed()
        assert renamed == []

    def test_returns_empty_list_when_directory_missing(self, monkeypatch):
        """Test that get_all_renamed handles missing directory."""
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", "/nonexistent/path")

        renamed = wireplumber.get_all_renamed()
        assert renamed == []


class TestRevertAll:
    """Tests for revert_all function."""

    def test_raises_without_root(self, monkeypatch):
        """Test that revert_all requires root privileges."""
        monkeypatch.setattr(os, "geteuid", lambda: 1000)  # Non-root
        with pytest.raises(PermissionError, match="Must be run as root"):
            wireplumber.revert_all()

    def test_removes_all_configs(self, monkeypatch, tmp_path):
        """Test that revert_all removes all portname configs."""
        monkeypatch.setattr(os, "geteuid", lambda: 0)  # Root
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        # Create config files
        (tmp_path / "90-portname-node1.conf").write_text('''
matches = [ { node.name = "node1" } ]
node.description = "Node One"
''')
        (tmp_path / "90-portname-node2.conf").write_text('''
matches = [ { node.name = "node2" } ]
node.description = "Node Two"
''')

        reverted = wireplumber.revert_all()

        assert len(reverted) == 2
        assert "node1" in reverted
        assert "node2" in reverted
        assert not (tmp_path / "90-portname-node1.conf").exists()
        assert not (tmp_path / "90-portname-node2.conf").exists()

    def test_returns_empty_list_when_no_configs(self, monkeypatch, tmp_path):
        """Test that revert_all returns empty list when no configs exist."""
        monkeypatch.setattr(os, "geteuid", lambda: 0)  # Root
        monkeypatch.setattr(wireplumber, "WIREPLUMBER_DIR", str(tmp_path))

        reverted = wireplumber.revert_all()

        assert reverted == []


class TestLuaFormat:
    """WirePlumber 0.4 reads Lua rules from main.lua.d."""

    NODE = "alsa_input.usb-046d_Brio_101-02.mono-fallback"

    def _use_lua(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wireplumber, "_FORMAT", "lua")
        monkeypatch.setattr(wireplumber, "LUA_DIR", str(tmp_path))
        monkeypatch.setattr(wireplumber.os, "geteuid", lambda: 0)

    def test_generate_lua_rule(self, monkeypatch):
        monkeypatch.setattr(wireplumber, "_FORMAT", "lua")
        out = wireplumber.generate_rule(self.NODE, "Desk Mic")
        assert "alsa_monitor.rules" in out
        assert f'{{ "node.name", "equals", "{self.NODE}" }}' in out
        assert '["node.description"] = "Desk Mic"' in out
        assert '["node.nick"] = "Desk Mic"' in out

    def test_escapes_quotes_and_backslashes(self, monkeypatch):
        monkeypatch.setattr(wireplumber, "_FORMAT", "lua")
        out = wireplumber.generate_rule(self.NODE, 'a"b\\c')
        assert '= "a\\"b\\\\c"' in out

    def test_rename_revert_roundtrip(self, monkeypatch, tmp_path):
        self._use_lua(monkeypatch, tmp_path)
        wireplumber.rename_node(self.NODE, 'My "Mic"')
        files = list(tmp_path.iterdir())
        assert len(files) == 1 and files[0].suffix == ".lua"
        assert wireplumber.is_node_renamed(self.NODE)
        assert wireplumber.get_current_description(self.NODE) == 'My "Mic"'
        assert wireplumber.get_all_renamed() == [(self.NODE, 'My "Mic"')]
        wireplumber.revert_node(self.NODE)
        assert not list(tmp_path.iterdir())

    def test_detects_version(self, monkeypatch):
        class R:
            stdout = "wireplumber\nCompiled with libwireplumber 0.4.17\n"
        monkeypatch.setattr(wireplumber, "_FORMAT", None)
        monkeypatch.setattr(wireplumber.subprocess, "run", lambda *a, **k: R())
        assert wireplumber._detect_format() == "lua"
        R.stdout = "libwireplumber 0.5.6"
        monkeypatch.setattr(wireplumber, "_FORMAT", None)
        assert wireplumber._detect_format() == "conf"
