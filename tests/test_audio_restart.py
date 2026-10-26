"""Handle the systemd start limit found during live Mint hardware testing."""

import subprocess
from unittest.mock import Mock

import pytest

from portname import core


@pytest.mark.parametrize("root", [False, True])
def test_restart_recovers_only_rate_limit(monkeypatch, root):
    monkeypatch.setattr(core.os, "geteuid", lambda: 0 if root else 1000)
    monkeypatch.setattr(core, "_get_real_user", lambda: ("testuser", "1000"))
    run = Mock(side_effect=[
        subprocess.CalledProcessError(1, [], stderr="Job failed"),
        subprocess.CompletedProcess([], 0, stdout="start-limit-hit\nstart-limit-hit\nsuccess\n"),
        subprocess.CompletedProcess([], 0),
        subprocess.CompletedProcess([], 0),
    ])
    monkeypatch.setattr(core.subprocess, "run", run)
    core.restart_pipewire()
    commands = [call.args[0] for call in run.call_args_list]
    assert commands[0] == commands[3]
    assert "reset-failed" in commands[2]
    if root:
        assert all(cmd[:4] == ["sudo", "-u", "testuser", "XDG_RUNTIME_DIR=/run/user/1000"] for cmd in commands)
    else:
        assert all(cmd[:2] == ["systemctl", "--user"] for cmd in commands)


def test_real_configuration_failure_is_not_reset_or_hidden(monkeypatch):
    monkeypatch.setattr(core.os, "geteuid", lambda: 1000)
    run = Mock(side_effect=[
        subprocess.CalledProcessError(1, [], stderr="invalid config"),
        subprocess.CompletedProcess([], 0, stdout="exit-code\nsuccess\nsuccess\n"),
    ])
    monkeypatch.setattr(core.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="invalid config"):
        core.restart_pipewire()
    assert run.call_count == 2


def test_rate_limit_retry_is_bounded(monkeypatch):
    monkeypatch.setattr(core.os, "geteuid", lambda: 1000)
    run = Mock(side_effect=[
        subprocess.CalledProcessError(1, [], stderr="Job failed"),
        subprocess.CompletedProcess([], 0, stdout="start-limit-hit\n"),
        subprocess.CompletedProcess([], 0),
        subprocess.CalledProcessError(1, [], stderr="still failed"),
    ])
    monkeypatch.setattr(core.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="still failed"):
        core.restart_pipewire()
    assert run.call_count == 4


def test_restart_timeout_reports_error(monkeypatch):
    monkeypatch.setattr(core.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(core.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired([], 15)))
    with pytest.raises(RuntimeError, match="timed out"):
        core.restart_pipewire()
