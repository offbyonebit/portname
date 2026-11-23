import pytest

from portname import core, wireplumber


@pytest.fixture(autouse=True)
def _default_wireplumber_format(monkeypatch):
    """Don't depend on the WirePlumber version installed on the test machine."""
    monkeypatch.setattr(wireplumber, "_FORMAT", "conf")


@pytest.fixture(autouse=True)
def _no_live_pipewire_check(monkeypatch):
    """Tests have no PipeWire to confirm a USB rename against."""
    monkeypatch.setattr(core, "node_has_description", lambda *_a, **_k: True)
