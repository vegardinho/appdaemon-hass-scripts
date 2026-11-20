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

async def test_service_call_helper_builds_brightness_kwargs():
    assert brightness_turn_on_kwargs(150, 15) == {
        "brightness": 150,
        "transition": 15,
    }


async def test_service_call_helper_builds_hs_kwargs():
    assert hs_turn_on_kwargs([220, 60], 15) == {
        "hs_color": (220, 60),
        "transition": 15,
    }


async def test_service_call_helper_builds_kelvin_kwargs():
    kwargs = kelvin_turn_on_kwargs(3521, 15)

    assert kwargs == {
        "color_temp_kelvin": 3521,
        "transition": 15,
    }
    assert "kelvin" not in kwargs
    assert assert_no_legacy_kelvin(kwargs) == kwargs


async def test_service_call_helper_rejects_legacy_kelvin():
    with pytest.raises(ValueError, match="Legacy 'kelvin'"):
        assert_no_legacy_kelvin({
            "kelvin": 3521,
            "transition": 15,
        })
