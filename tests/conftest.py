import pytest

from scriptflow.glob import set_main_controller


@pytest.fixture(autouse=True)
def isolated_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    set_main_controller(None)
    yield
    set_main_controller(None)
