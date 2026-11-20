import asyncio
import sys
import types
from pathlib import Path

import pytest


hassapi = types.ModuleType("hassapi")
hassapi.Hass = object
sys.modules.setdefault("hassapi", hassapi)

yaml = types.ModuleType("yaml")
yaml.safe_load = lambda *args, **kwargs: None
sys.modules.setdefault("yaml", yaml)

APPS_DIR = Path(__file__).parents[2]
sys.path.insert(0, str(APPS_DIR))

from scene_transition.scene_transition import SceneTransition


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker("asyncio")
        item.add_marker("unit")


def make_app(states):
    app = object.__new__(SceneTransition)
    app.transition_step_seconds = 30
    app.hue_difference_threshold = 120
    app._active_scenes = {}
    app._lights_started_off = set()
    app._entity_scene_ids = {}
    app.log = lambda *args, **kwargs: None
    app.create_task = lambda coro: asyncio.create_task(coro)

    async def get_state(entity_id, attribute=None):
        return states.get(entity_id)

    app.get_state = get_state
    return app


def state(entity_state="on", attributes=None):
    return {
        "state": entity_state,
        "attributes": attributes or {},
    }
