import scriptflow as sf
from omegaconf import OmegaConf
from scriptflow.core import Controller
import pytest
import importlib.util
from unittest.mock import MagicMock, Mock, AsyncMock

from scriptflow.cli import main as sf_main

import asyncio

async def simple_flow():
    await asyncio.sleep(0.1)

@pytest.mark.asyncio
async def test_main(monkeypatch):

    monkeypatch.setattr(
        importlib.util,"spec_from_file_location",
        Mock())

    controller = Mock(**{'start_loops':AsyncMock()})

    await sf_main(simple_flow, controller)

