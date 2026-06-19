"""Tests for portname privilege module."""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

from portname import privilege


class TestEnsureRootOrExit:
    """Tests for ensure_root_or_exit."""

    @patch("portname.privilege.os.geteuid", return_value=0)
    @patch("portname.privilege.sys.exit")
    def test_does_not_exit_when_root(self, mock_exit, mock_euid):
        privilege.ensure_root_or_exit()
        mock_exit.assert_not_called()

    @patch("portname.privilege.os.geteuid", return_value=1000)
    @patch("portname.privilege.sys.exit")
    def test_exits_when_not_root(self, mock_exit, mock_euid):
        privilege.ensure_root_or_exit()
        mock_exit.assert_called_once_with(1)


class TestRunAsRoot:
    """Tests for run_as_root."""

    @patch("portname.privilege.shutil.which", return_value="/usr/bin/portname")
    @patch("portname.privilege.subprocess.run")
    def test_uses_pkexec_with_installed_binary(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=0)
        privilege.run_as_root(["rename", "route", "Name"])
        mock_run.assert_called_once_with(
            ["pkexec", "/usr/bin/portname", "rename", "route", "Name"],
            capture_output=True, text=True,
        )

    @patch("portname.privilege.shutil.which", return_value=None)
    @patch("portname.privilege.subprocess.run")
    def test_uses_python_module_fallback(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(returncode=0)
        privilege.run_as_root(["revert", "route"])
        args, kwargs = mock_run.call_args
        assert args[0][0] == "pkexec"
        assert args[0][1] == "env"
        assert args[0][2].startswith("PYTHONPATH=")
        assert args[0][3] == sys.executable
        assert args[0][4] == "-m"
        assert args[0][5] == "portname"
        assert args[0][6:] == ["revert", "route"]
