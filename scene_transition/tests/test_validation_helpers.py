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

async def test_validation_helper_matches_with_tolerances():
    hs_match = is_at_target(
        {"brightness": 101, "hs_color": [201, 51]},
        {"brightness": 100, "hs_color": [200, 50], "color_temp_kelvin": None},
    )
    kelvin_match = is_at_target(
        {"brightness": 100, "color_temp_kelvin": 3400},
        {"brightness": 100, "hs_color": [0, 0], "color_temp_kelvin": 3450},
    )

    assert hs_match["overall"]
    assert kelvin_match["overall"]


async def test_validation_helper_detects_missing_or_mismatched_values():
    missing_brightness = is_at_target(
        {"brightness": None, "hs_color": [200, 50]},
        {"brightness": 100, "hs_color": [200, 50], "color_temp_kelvin": None},
    )
    mismatch_hs = is_at_target(
        {"brightness": 100, "hs_color": [300, 80]},
        {"brightness": 100, "hs_color": [200, 50], "color_temp_kelvin": None},
    )

    assert not missing_brightness["overall"]
    assert not mismatch_hs["color"]


async def test_validation_helper_required_values_and_mismatch_text():
    hs_target = {
        "brightness": 100,
        "hs_color": [200, 50],
        "color_temp_kelvin": None,
    }
    kelvin_target = {
        "brightness": 100,
        "hs_color": [0, 0],
        "color_temp_kelvin": 3500,
    }

    assert get_required_validation_value(
        {"brightness": 99, "hs_color": [200, 50]},
        hs_target,
        "brightness",
    ) == 99
    assert get_required_validation_value(
        {"color_temp_kelvin": 3400},
        kelvin_target,
        "color",
    ) == 3400
    assert "Brightness mismatch" in describe_target_mismatch(
        {"brightness": 40},
        hs_target,
        "brightness",
        "fallback",
    )
    assert "3500K" in describe_target_mismatch(
        {"color_temp_kelvin": 3000},
        kelvin_target,
        "color",
        "fallback",
    )


async def test_correction_helper_lists_mismatches_once():
    assert get_corrections_needed(
        {"brightness": 50, "hs_color": [200, 50]},
        {"brightness": 100, "hs_color": [220, 60], "color_temp_kelvin": None},
    ) == ["brightness", "hs_color"]
    assert get_corrections_needed(
        {"brightness": 100, "color_temp_kelvin": 3000},
        {"brightness": 100, "hs_color": [0, 0], "color_temp_kelvin": 3500},
    ) == ["color_temp"]
