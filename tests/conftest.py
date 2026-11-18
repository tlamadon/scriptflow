import pytest

from scriptflow.glob import set_main_controller


@pytest.fixture(autouse=True)
def isolated_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_main_controller(None)
    yield
    set_main_controller(None)


@pytest.fixture
def attempt(tmp_path):
    """Build a scheduler attempt as the runners record it, with its log under tmp_path."""
    def make(**fields):
        return dict(dict(state="queued", backend="slurm", job_id="123", submission_token="sf-token",
                         log=str(tmp_path / "attempt.out"), error=None), **fields)
    return make
