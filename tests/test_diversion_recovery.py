"""Exercise real dpkg-divert using only temporary files and a private database."""

import json
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from portname import core


ORIGINAL = "[General]\ndescription-key = hdmi-output\npriority = 90\n"


@pytest.fixture
def audio_paths(tmp_path, monkeypatch):
    if not shutil.which("dpkg-divert"):
        pytest.skip("dpkg-divert is not installed")
    paths = tmp_path / "mixer paths"
    paths.mkdir()
    admin = tmp_path / "dpkg"
    admin.mkdir()
    monkeypatch.setattr(core, "PATHS_DIR", str(paths))
    monkeypatch.setattr(core, "_RENAME_STORE_PATH", str(tmp_path / "renames.json"))
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)
    restart = Mock()
    monkeypatch.setattr(core, "restart_pipewire", restart)
    monkeypatch.setattr(core.wireplumber, "get_all_renamed", lambda: [])
    run = subprocess.run

    def isolated_run(cmd, **kwargs):
        assert cmd[0] == "dpkg-divert", "Unexpected external command in audio test"
        return run([cmd[0], "--admindir", str(admin)] + cmd[1:], **kwargs)

    monkeypatch.setattr(core.subprocess, "run", isolated_run)
    return paths, isolated_run, restart


def test_fresh_rename_rename_again_and_revert(audio_paths):
    paths, run, _restart = audio_paths
    path = paths / "hdmi-output-2.conf"
    path.write_text(ORIGINAL)
    core.rename_port("hdmi-output-2", "Monitor")
    assert path.with_suffix(".conf.orig").read_text() == ORIGINAL
    assert not path.with_suffix(".conf.distrib").exists()
    assert core.is_renamed("hdmi-output-2")
    core.rename_port("hdmi-output-2", "Desk Speakers")
    assert core._read_description(str(path)) == "Desk Speakers"
    assert core.get_original_description("hdmi-output-2") == "hdmi-output"
    core.revert_port("hdmi-output-2")
    assert path.read_text() == ORIGINAL
    assert not path.with_suffix(".conf.orig").exists()
    assert run(["dpkg-divert", "--list"], capture_output=True, text=True).stdout == ""


@pytest.mark.parametrize("suffix", [".orig", ".distrib"])
@pytest.mark.parametrize("registered", [True, False])
def test_repair_missing_port_without_sidecar(audio_paths, suffix, registered):
    paths, run, restart = audio_paths
    path = paths / "hdmi-output-2.conf"
    backup = str(path) + suffix
    path.write_text(ORIGINAL)
    if registered:
        run(["dpkg-divert", "--local", "--divert", backup, "--rename", "--add", str(path)],
            check=True, capture_output=True)
    else:
        path.rename(backup)
    assert core.get_path_file("hdmi-output-2") == str(path)
    assert core.check_renames() == {"repaired": ["hdmi-output-2"], "failed": []}
    assert path.read_text() == ORIGINAL
    assert core._read_description(backup) == "hdmi-output"
    restart.assert_called_once()
    assert core.check_renames() == {"repaired": [], "failed": []}


def test_legacy_distrib_rename_check_and_revert(audio_paths):
    paths, run, _restart = audio_paths
    path = paths / "hdmi-output-2.conf"
    path.write_text(ORIGINAL)
    # Exactly the command used by the broken release: dpkg chooses .distrib.
    run(["dpkg-divert", "--local", "--rename", "--add", str(path)],
        check=True, capture_output=True)
    core.rename_port("hdmi-output-2", "Monitor")
    assert core.get_original_description("hdmi-output-2") == "hdmi-output"
    path.unlink()
    assert core.check_renames() == {"repaired": ["hdmi-output-2"], "failed": []}
    assert core._read_description(str(path)) == "Monitor"
    core.revert_port("hdmi-output-2")
    assert path.read_text() == ORIGINAL
    assert not path.with_suffix(".conf.distrib").exists()
    assert not core.is_renamed("hdmi-output-2")


def test_legacy_backup_takes_precedence_over_unregistered_orig(audio_paths):
    paths, run, _restart = audio_paths
    path = paths / "hdmi-output-2.conf"
    path.write_text(ORIGINAL)
    run(["dpkg-divert", "--local", "--rename", "--add", str(path)],
        check=True, capture_output=True)
    path.with_suffix(".conf.orig").write_text("unrelated backup")
    core.rename_port("hdmi-output-2", "Monitor")
    assert core.get_original_description("hdmi-output-2") == "hdmi-output"
    core.revert_port("hdmi-output-2")
    assert path.read_text() == ORIGINAL
    assert path.with_suffix(".conf.orig").read_text() == "unrelated backup"


@pytest.mark.parametrize("owner", ["--local", "--package=another-package"])
def test_repair_refuses_unrelated_diversions(audio_paths, owner):
    paths, run, restart = audio_paths
    path = paths / "hdmi-output-2.conf"
    path.write_text(ORIGINAL)
    backup = str(path) + (".other" if owner == "--local" else ".distrib")
    run(["dpkg-divert", owner, "--divert", backup, "--rename", "--add", str(path)],
        check=True, capture_output=True)
    core._store_custom_name("path_file", "hdmi-output-2", "Monitor")
    result = core.check_renames()
    assert result["repaired"] == []
    assert len(result["failed"]) == 1
    assert not path.exists()
    assert core._read_description(backup) == "hdmi-output"
    restart.assert_not_called()


def test_missing_registered_backup_preserves_active_file(audio_paths):
    paths, run, _restart = audio_paths
    path = paths / "hdmi-output-2.conf"
    path.write_text(ORIGINAL)
    run(["dpkg-divert", "--local", "--rename", "--add", str(path)],
        check=True, capture_output=True)
    path.with_suffix(".conf.distrib").unlink()
    path.write_text("[General]\ndescription = Existing Name\n")
    with pytest.raises(RuntimeError, match="registered backup.*missing"):
        core.rename_port("hdmi-output-2", "New Name")
    assert core._read_description(str(path)) == "Existing Name"


def test_legacy_ports_remain_discoverable(audio_paths, monkeypatch):
    paths, _run, _restart = audio_paths
    (paths / "hdmi-output-2.conf.distrib").write_text(ORIGINAL)
    data = [{"info": {"props": {"device.api": "alsa", "device.name": "test"},
                      "params": {"EnumRoute": [{"name": "hdmi-output-2"}]}}}]
    monkeypatch.setattr(core.shutil, "which", lambda _cmd: "/usr/bin/pw-dump")
    monkeypatch.setattr(core.subprocess, "run", lambda *_args, **_kwargs:
                        subprocess.CompletedProcess([], 0, stdout=json.dumps(data)))
    assert core.get_devices()[0]["routes"][0]["rename_method"] == "path_file"


def test_non_debian_rename_and_revert(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "PATHS_DIR", str(tmp_path))
    monkeypatch.setattr(core, "_has_dpkg_divert", lambda: False)
    monkeypatch.setattr(core, "_RENAME_STORE_PATH", str(tmp_path / "renames.json"))
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)
    monkeypatch.setattr(core, "restart_pipewire", Mock())
    path = tmp_path / "hdmi-output-2.conf"
    path.write_text(ORIGINAL)
    core.rename_port("hdmi-output-2", "Monitor")
    assert path.with_suffix(".conf.orig").read_text() == ORIGINAL
    core.revert_port("hdmi-output-2")
    assert path.read_text() == ORIGINAL


def test_diversion_query_forces_stable_locale(audio_paths, monkeypatch):
    _paths, run, _restart = audio_paths
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    def check_locale(cmd, **kwargs):
        assert kwargs["env"]["LC_ALL"] == "C"
        return run(cmd, **kwargs)
    monkeypatch.setattr(core.subprocess, "run", check_locale)
    assert core._get_diversions() == {}


def test_diversion_database_failure_aborts_rename(audio_paths, monkeypatch):
    paths, _run, restart = audio_paths
    path = paths / "hdmi-output-2.conf"
    path.write_text(ORIGINAL)
    def fail(cmd, **kwargs):
        raise subprocess.CalledProcessError(2, cmd, stderr="database unavailable")
    monkeypatch.setattr(core.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="Cannot read diversions"):
        core.rename_port("hdmi-output-2", "Monitor")
    assert path.read_text() == ORIGINAL
    restart.assert_not_called()
