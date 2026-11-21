import asyncio

import pytest

from conftest import make_app, state

from scene_transition.app import SceneTransition, TransitionError
from scene_transition.lights import get_light_attributes, get_off_group_members
from scene_transition.scenes import expand_scene_configuration
from scene_transition.validation import validate_transition_state
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

async def test_group_entities_attribute_is_parsed():
    app = make_app({
        "light.group": state(attributes={
            "group_entities": [
                "light.vegard_taklampe",
                "light.ikea_e14_ambience_2",
                "light.ikea_e14_ambience",
            ],
        }),
    })

    attributes = await get_light_attributes(app, "light.group")

    assert attributes["member_entity_ids"] == [
        "light.vegard_taklampe",
        "light.ikea_e14_ambience_2",
        "light.ikea_e14_ambience",
    ]


async def test_comma_separated_group_entities_are_parsed():
    app = make_app({
        "light.group": state(attributes={
            "group_entities": (
                "light.vegard_taklampe, light.ikea_e14_ambience_2,"
                " light.ikea_e14_ambience"
            ),
        }),
    })

    attributes = await get_light_attributes(app, "light.group")

    assert attributes["member_entity_ids"] == [
        "light.vegard_taklampe",
        "light.ikea_e14_ambience_2",
        "light.ikea_e14_ambience",
    ]


async def test_other_member_attributes_are_not_accepted():
    app = make_app({
        "light.group": state(attributes={
            "entity_id": ["light.member_from_entity_id"],
            "group_members": ["light.member_from_group_members"],
            "members": ["light.member_from_members"],
        }),
    })

    attributes = await get_light_attributes(app, "light.group")

    assert attributes["member_entity_ids"] == []


async def test_off_group_member_is_detected():
    app = make_app({
        "light.group": state(attributes={
            "group_entities": [
                "light.member_on",
                "light.member_off",
            ],
        }),
        "light.member_on": state("on"),
        "light.member_off": state("off"),
    })

    off_members = await get_off_group_members(app, "light.group")

    assert off_members == ["light.member_off"]


async def test_unavailable_group_member_is_not_treated_as_off():
    app = make_app({
        "light.group": state(attributes={
            "group_entities": ["light.member_unavailable"],
        }),
        "light.member_unavailable": state("unavailable"),
    })

    off_members = await get_off_group_members(app, "light.group")

    assert off_members == []


async def test_validation_cancels_for_off_group_member():
    app = make_app({
        "light.group": state(attributes={
            "brightness": 100,
            "hs_color": [100, 50],
            "group_entities": ["light.member_off"],
        }),
        "light.member_off": state("off"),
    })

    with pytest.raises(ValueError, match="contains off member"):
        await validate_transition_state(app,
            "light.group",
            target_values={
                "brightness": 120,
                "hs_color": [120, 50],
                "color_temp_kelvin": None,
            },
            validation_key="brightness",
            error_message="brightness mismatch",
        )


async def test_individual_light_has_no_group_members():
    app = make_app({
        "light.individual": state(attributes={
            "brightness": 100,
            "hs_color": [200, 50],
        }),
    })

    attributes = await get_light_attributes(app, "light.individual")

    assert attributes["member_entity_ids"] == []


async def test_parse_group_entities_deduplicates_and_filters():
    attributes = {
        "group_entities": [
            "light.member_a",
            "sensor.not_a_light",
            "light.member_a",
            "light.member_b",
        ],
    }

    assert parse_group_entities(attributes) == [
        "light.member_a",
        "light.member_b",
    ]


async def test_expand_nested_group_deduplicates_and_skips_unavailable():
    app = make_app({
        "light.group": state(attributes={
            "group_entities": ["light.member_a", "light.nested"],
        }),
        "light.member_a": state("on"),
        "light.nested": state(attributes={
            "group_entities": ["light.member_b", "light.member_a"],
        }),
        "light.member_b": state("on"),
        "light.member_a": state("on"),
    })

    async def get_attributes(entity_id):
        return await get_light_attributes(app, entity_id)

    expanded = await expand_light_entities("light.group", get_attributes)

    assert expanded == ["light.member_a", "light.member_b"]


async def test_expand_group_skips_unknown_and_unavailable_members():
    app = make_app({
        "light.group": state(attributes={
            "group_entities": [
                "light.available",
                "light.unknown",
                "light.unavailable",
            ],
        }),
        "light.available": state("on"),
        "light.unknown": state("unknown"),
        "light.unavailable": state("unavailable"),
    })

    async def get_attributes(entity_id):
        return await get_light_attributes(app, entity_id)

    expanded = await expand_light_entities("light.group", get_attributes)

    assert expanded == ["light.available"]


async def test_expand_group_cycle_is_safe():
    app = make_app({
        "light.group_a": state(attributes={
            "group_entities": ["light.group_b"],
        }),
        "light.group_b": state(attributes={
            "group_entities": ["light.group_a", "light.member"],
        }),
        "light.member": state("on"),
    })

    async def get_attributes(entity_id):
        return await get_light_attributes(app, entity_id)

    expanded = await expand_light_entities("light.group_a", get_attributes)

    assert expanded == ["light.member"]


async def test_expand_scene_configuration_mixes_individual_and_group_entities():
    app = make_app({
        "light.individual": state("on", {
            "brightness": 100,
            "hs_color": [200, 50],
        }),
        "light.group": state("on", {
            "group_entities": [
                "light.member_a",
                "light.member_b",
                "light.member_unavailable",
            ],
        }),
        "light.member_a": state("on"),
        "light.member_b": state("off"),
        "light.member_unavailable": state("unavailable"),
    })
    scene_configuration = {
        "light.individual": {
            "brightness": 120,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        },
        "light.group": {
            "brightness": 130,
            "hs_color": [240, 70],
            "color_temp_kelvin": None,
        },
    }

    expanded = await expand_scene_configuration(app, scene_configuration)

    assert set(expanded) == {
        "light.individual",
        "light.member_a",
        "light.member_b",
    }
    assert expanded["light.member_a"] == scene_configuration["light.group"]
    assert expanded["light.member_b"] == scene_configuration["light.group"]


async def test_expand_scene_configuration_deduplicates_shared_member():
    app = make_app({
        "light.group_a": state("on", {
            "group_entities": ["light.member"],
        }),
        "light.group_b": state("on", {
            "group_entities": ["light.member"],
        }),
        "light.member": state("on"),
    })
    scene_configuration = {
        "light.group_a": {
            "brightness": 100,
            "hs_color": [200, 50],
            "color_temp_kelvin": None,
        },
        "light.group_b": {
            "brightness": 120,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        },
    }

    expanded = await expand_scene_configuration(app, scene_configuration)

    assert list(expanded) == ["light.member"]
    assert expanded["light.member"] == scene_configuration["light.group_a"]
