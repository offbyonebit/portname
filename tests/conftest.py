import pytest

from portname import wireplumber


@pytest.fixture(autouse=True)
def _default_wireplumber_format(monkeypatch):
    """Don't depend on the WirePlumber version installed on the test machine."""
    monkeypatch.setattr(wireplumber, "_FORMAT", "conf")
