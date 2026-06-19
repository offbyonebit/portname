"""Tests for portname automute module."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from portname import automute


SAMPLE_AMIXER_STATUS_ENABLED = """\
Simple mixer control 'Auto-Mute Mode',0
  Capabilities: enum
  Items: 'Disabled' 'Enabled'
  Item0: 'Enabled'
"""

SAMPLE_AMIXER_STATUS_DISABLED = """\
Simple mixer control 'Auto-Mute Mode',0
  Capabilities: enum
  Items: 'Disabled' 'Enabled'
  Item0: 'Disabled'
"""

SAMPLE_AMIXER_NO_CONTROL = """\
amixer: Unable to find simple control 'Auto-Mute Mode',0
"""


class TestGetAutoMuteStatus:
    """Tests for get_auto_mute_status."""

    @patch("portname.automute.subprocess.run")
    def test_returns_enabled(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=SAMPLE_AMIXER_STATUS_ENABLED,
            returncode=0,
        )
        assert automute.get_auto_mute_status(0) == "Enabled"
        mock_run.assert_called_once_with(
            ["amixer", "-c", "0", "sget", "Auto-Mute Mode"],
            capture_output=True, text=True, check=True,
        )

    @patch("portname.automute.subprocess.run")
    def test_returns_disabled(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout=SAMPLE_AMIXER_STATUS_DISABLED,
            returncode=0,
        )
        assert automute.get_auto_mute_status(1) == "Disabled"

    @patch("portname.automute.subprocess.run")
    def test_returns_none_when_control_missing(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "amixer")
        assert automute.get_auto_mute_status(0) is None


class TestSetAutoMute:
    """Tests for set_auto_mute."""

    @patch("portname.automute.subprocess.run")
    def test_enables(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        automute.set_auto_mute(0, True)
        mock_run.assert_called_once_with(
            ["amixer", "-c", "0", "sset", "Auto-Mute Mode", "Enabled"],
            capture_output=True, text=True, check=True,
        )

    @patch("portname.automute.subprocess.run")
    def test_disables(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        automute.set_auto_mute(2, False)
        mock_run.assert_called_once_with(
            ["amixer", "-c", "2", "sset", "Auto-Mute Mode", "Disabled"],
            capture_output=True, text=True, check=True,
        )


class TestGetCardsWithAutoMute:
    """Tests for get_cards_with_auto_mute."""

    @patch("portname.automute.get_auto_mute_status")
    def test_returns_cards_with_auto_mute(self, mock_status):
        mock_status.side_effect = lambda card: "Enabled" if card == "1" else None
        devices = [
            {"alsa_card": "1", "device_description": "Card One"},
            {"alsa_card": "2", "device_description": "Card Two"},
            {"alsa_card": "1", "device_description": "Card One Again"},
        ]
        result = automute.get_cards_with_auto_mute(devices)
        assert result == [("1", "Card One")]

    @patch("portname.automute.get_auto_mute_status")
    def test_returns_empty_list_when_none_have_auto_mute(self, mock_status):
        mock_status.return_value = None
        devices = [
            {"alsa_card": "1", "device_description": "Card One"},
        ]
        assert automute.get_cards_with_auto_mute(devices) == []
