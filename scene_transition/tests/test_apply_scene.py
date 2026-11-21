import asyncio

import pytest

from conftest import make_app, state

from scene_transition.app import SceneTransition, TransitionError
import scene_transition.scenes as scenes_module
from scene_transition.scenes import apply_scene
from scene_transition.color import (
    adapt_target_to_light,
    convert_hs_to_kelvin,
    get_kelvin_range,
    hs_to_kelvin,
    supports_hs_or_xy,
    supports_kelvin,
)
from scene_transition.lights import (
    classify_light_state,
    expand_light_entities,
    normalize_light_state,
    parse_group_entities,
)
from scene_transition.validation import (
    describe_target_mismatch,
    get_corrections_needed,
    get_required_validation_value,
    is_at_target,
)
from scene_transition.steps import (
    calculate_phase_plan,
    calculate_step_count,
    calculate_step_duration,
    interpolate,
    interpolate_hs,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.unit,
]

async def test_apply_scene_fans_out_to_individual_group_members():
    app = make_app({
        "light.group": state("on", {
            "group_entities": [
                "light.member_a",
                "light.member_b",
            ],
        }),
        "light.member_a": state("on"),
        "light.member_b": state("on"),
    })
    target = {
        "brightness": 150,
        "hs_color": [220, 60],
        "color_temp_kelvin": None,
    }

    async def load_scene_configuration(app, scene_entity_id):
        return {"light.group": target}

    transitioned = []

    async def transition_to_scene(app, entity_id, target_values, transition_seconds, scene_entity_id):
        transitioned.append(entity_id)

    scenes_module.load_scene_configuration = load_scene_configuration
    scenes_module.transition_to_light = transition_to_scene

    await apply_scene(app, "scene.test", 60)

    assert transitioned == ["light.member_a", "light.member_b"]


async def test_apply_scene_cancels_other_scene_with_shared_member():
    app = make_app({
        "light.member": state("on"),
    })

    async def load_scene_configuration(app, scene_entity_id):
        return {"light.member": {
            "brightness": 150,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        }}

    scenes_module.load_scene_configuration = load_scene_configuration

    old_task = asyncio.create_task(asyncio.sleep(3600))
    app._active_scenes["scene.old"] = {
        "tasks": {"light.member": old_task},
    }

    async def transition_to_scene(app, entity_id, target_values, transition_seconds, scene_entity_id):
        return None

    scenes_module.transition_to_light = transition_to_scene

    await apply_scene(app, "scene.new", 60)

    assert old_task.cancelled()
    assert "scene.old" not in app._active_scenes


async def test_apply_scene_failure_cancels_remaining_member_tasks():
    app = make_app({
        "light.member_a": state("on"),
        "light.member_b": state("on"),
    })

    async def load_scene_configuration(app, scene_entity_id):
        return {
            "light.member_a": {
                "brightness": 150,
                "hs_color": [220, 60],
                "color_temp_kelvin": None,
            },
            "light.member_b": {
                "brightness": 150,
                "hs_color": [220, 60],
                "color_temp_kelvin": None,
            },
        }

    scenes_module.load_scene_configuration = load_scene_configuration
    sibling_states = {}

    async def transition_to_scene(app, entity_id, target_values, transition_seconds, scene_entity_id):
        if entity_id == "light.member_a":
            await asyncio.sleep(0)
            raise ValueError("expected test failure")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            sibling_states[entity_id] = "cancelled"
            raise

    scenes_module.transition_to_light = transition_to_scene

    await apply_scene(app, "scene.test", 60)

    assert sibling_states == {"light.member_b": "cancelled"}
