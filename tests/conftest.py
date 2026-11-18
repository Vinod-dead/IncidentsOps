import pytest

from incidentops.app import IncidentOps
from incidentops.config import Settings
from incidentops.llm.fake import FakeLLM


@pytest.fixture
def settings(tmp_path):
    return Settings(home=tmp_path / "home", backoff_base_s=0.001, tool_timeout_s=2.0)


@pytest.fixture
def make_ops(settings):
    made = []

    def _make(**kw):
        kw.setdefault("llm", FakeLLM())
        ops = IncidentOps(settings, **kw)
        made.append(ops)
        return ops

    yield _make
    for o in made:
        try:
            o.close()
        except Exception:  # noqa: BLE001
            pass
