import asyncio

import pytest

from conftest import make_app, state

from scene_transition.app import SceneTransition, TransitionError
from scene_transition.color import (
    adapt_target_to_light,
    clamp_kelvin,
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


@pytest.mark.parametrize(
    ("hue", "saturation", "expected"),
    [
        (10, 50, 2000),
        (20, 50, 2000),
        (160, 50, 4250),
        (300, 50, 6500),
        (350, 50, 6500),
        (200, 5, 4000),
    ],
)
async def test_hs_to_kelvin_boundaries(hue, saturation, expected):
    assert hs_to_kelvin(hue, saturation) == expected


async def test_convert_hs_to_kelvin():
    assert convert_hs_to_kelvin([160, 50]) == 4250
    assert convert_hs_to_kelvin(None) is None
    assert convert_hs_to_kelvin([200]) is None


async def test_kelvin_and_hs_capability_helpers():
    kelvin_only = {"supported_color_modes": ["color_temp"]}
    hs_only = {"supported_color_modes": ["hs"]}
    xy_kelvin = {"supported_color_modes": ["color_temp", "xy"]}

    assert supports_kelvin(kelvin_only)
    assert not supports_hs_or_xy(kelvin_only)
    assert supports_hs_or_xy(hs_only)
    assert supports_kelvin(xy_kelvin)
    assert supports_hs_or_xy(xy_kelvin)


async def test_kelvin_range_helper():
    attributes = {
        "min_color_temp_kelvin": 2202,
        "max_color_temp_kelvin": 4000,
    }

    assert get_kelvin_range(attributes) == (2202, 4000)


async def test_clamp_kelvin_to_bulb_range():
    attributes = {
        "min_color_temp_kelvin": 2202,
        "max_color_temp_kelvin": 4000,
    }

    assert clamp_kelvin(5198, attributes) == 4000
    assert clamp_kelvin(1800, attributes) == 2202
    assert clamp_kelvin(3500, attributes) == 3500


async def test_adapt_target_for_kelvin_only_light():
    light = {
        "supported_color_modes": ["color_temp"],
        "min_color_temp_kelvin": 2202,
        "max_color_temp_kelvin": 4000,
    }

    adapted = adapt_target_to_light(light, {
        "brightness": 150,
        "hs_color": [160, 50],
        "color_temp_kelvin": None,
    })

    assert adapted == {
        "brightness": 150,
        "hs_color": [0, 0],
        "color_temp_kelvin": 4000,
    }


async def test_adapt_target_clamps_scene_kelvin_to_bulb_maximum():
    light = {
        "supported_color_modes": ["color_temp"],
        "min_color_temp_kelvin": 2202,
        "max_color_temp_kelvin": 4000,
    }

    adapted = adapt_target_to_light(light, {
        "brightness": 255,
        "hs_color": [219, 49],
        "color_temp_kelvin": 5198,
    })

    assert adapted["color_temp_kelvin"] == 4000
    assert adapted["hs_color"] == [0, 0]


async def test_adapt_hs_conversion_clamps_to_bulb_maximum():
    light = {
        "supported_color_modes": ["color_temp"],
        "min_color_temp_kelvin": 2202,
        "max_color_temp_kelvin": 4000,
    }

    adapted = adapt_target_to_light(light, {
        "brightness": 255,
        "hs_color": [300, 50],
        "color_temp_kelvin": None,
    })

    assert adapted["color_temp_kelvin"] == 4000


async def test_adapt_target_preserves_hs_for_xy_light():
    light = {
        "supported_color_modes": ["color_temp", "xy"],
    }

    adapted = adapt_target_to_light(light, {
        "brightness": 150,
        "hs_color": [220, 60],
        "color_temp_kelvin": None,
    })

    assert adapted == {
        "brightness": 150,
        "hs_color": [220, 60],
        "color_temp_kelvin": None,
    }


async def test_adapt_target_uses_kelvin_when_scene_target_has_kelvin():
    light = {
        "supported_color_modes": ["color_temp", "xy"],
    }

    adapted = adapt_target_to_light(light, {
        "brightness": 150,
        "hs_color": [27, 44],
        "color_temp_kelvin": 3521,
    })

    assert adapted == {
        "brightness": 150,
        "hs_color": [0, 0],
        "color_temp_kelvin": 3521,
    }


async def test_adapt_target_for_brightness_only_light():
    adapted = adapt_target_to_light(
        {"supported_color_modes": ["brightness"]},
        {
            "brightness": 150,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        },
    )

    assert adapted == {
        "brightness": 150,
        "hs_color": None,
        "color_temp_kelvin": None,
    }
