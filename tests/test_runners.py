"""
    Implementing tests using pytest and fixtures

"""

import omegaconf
import pytest
from unittest.mock import MagicMock, Mock
import scriptflow as sf
import asyncio
import pytest_asyncio
import subprocess

# we create a fixture that replaces the subprocess call


# custom class to be the mock return value
# will override the requests.Response returned from requests.get
class MockResponse:

    # mock json() method always returns a specific testing dictionary
    @staticmethod
    def decode():
        return {"mock_key": "mock_response"}

def test_runner(monkeypatch):

    # Any arguments may be passed and mock_get() will always return our
    # mocked object, which only has the .json() method.
    def mock_get(*args, **kwargs):
        return MockResponse()

    # apply the monkeypatch for requests.get to mock_get
    monkeypatch.setattr(subprocess, "check_output", mock_get)

    runner = sf.HpcRunner({'maxsize':5})
    t1 = sf.Task(cmd="test")
    runner.add(t1)

    result = subprocess.check_output("basdasd").decode()
    assert result["mock_key"] == "mock_response"
