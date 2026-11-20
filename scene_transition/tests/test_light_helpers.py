import asyncio

import pytest

from conftest import make_app, state

from scene_transition.scene_transition import SceneTransition, TransitionError
from scene_transition.scene_transition_color import (
    adapt_target_to_light,
    convert_hs_to_kelvin,
    get_kelvin_range,
    hs_to_kelvin,
    supports_hs_or_xy,
    supports_kelvin,
)
from scene_transition.scene_transition_lights import (
    classify_light_state,
    expand_light_entities,
    normalize_light_state,
    parse_group_entities,
)
from scene_transition.scene_transition_validation import (
    describe_target_mismatch,
    get_corrections_needed,
    get_required_validation_value,
    is_at_target,
)
from scene_transition.scene_transition_steps import (
    calculate_phase_plan,
    calculate_step_count,
    calculate_step_duration,
    interpolate,
    interpolate_hs,
    split_attribute_durations,
)
from scene_transition.scene_transition_service_calls import (
    assert_no_legacy_kelvin,
    brightness_turn_on_kwargs,
    hs_turn_on_kwargs,
    kelvin_turn_on_kwargs,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.unit,
]


@pytest.mark.parametrize(
    ("entity_state", "expected"),
    [
        ("on", "on"),
        ("off", "off"),
        ("unknown", "unavailable"),
        ("unavailable", "unavailable"),
        (None, "unavailable"),
    ],
)
async def test_classify_light_state(entity_state, expected):
    assert classify_light_state(entity_state) == expected


async def test_normalize_light_state_handles_missing_state_dict():
    assert normalize_light_state("light.missing", None) == {
        "state": "unavailable",
        "available": False,
        "hs_color": None,
        "brightness": None,
        "color_temp_kelvin": None,
        "member_entity_ids": [],
        "supported_color_modes": [],
        "min_color_temp_kelvin": None,
        "max_color_temp_kelvin": None,
    }
