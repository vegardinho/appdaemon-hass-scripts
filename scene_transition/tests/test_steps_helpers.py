import asyncio

import pytest

from conftest import make_app, state

from scene_transition.app import SceneTransition, TransitionError
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

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("transition_seconds", "expected_steps", "expected_remainder"),
    [
        (1, 1, 1),
        (60, 2, 0),
        (90, 3, 0),
        (91, 4, 1),
        (1800, 60, 0),
        (3600, 120, 0),
    ],
)
def test_step_count_helper(
    transition_seconds,
    expected_steps,
    expected_remainder,
):
    assert calculate_step_count(transition_seconds, 30) == (
        expected_steps,
        expected_remainder,
    )


def test_step_duration_helper():
    assert calculate_step_duration(1, 4, 1, 30) == 30
    assert calculate_step_duration(4, 4, 1, 30) == 1
    assert calculate_step_duration(2, 2, 0, 30) == 30


def test_interpolation_helpers():
    assert interpolate(50, 100, 1, 2) == 75
    assert interpolate(3000, 4000, 1, 2) == 3500
    assert interpolate_hs(300, 50, 30, 50, 1, 2) == (165, 50)


def test_attribute_duration_and_phase_helpers():
    assert calculate_phase_plan(4, False) == (0, 4)
    assert calculate_phase_plan(4, True) == (2, 2)
    assert calculate_phase_plan(3, True) == (1, 2)
