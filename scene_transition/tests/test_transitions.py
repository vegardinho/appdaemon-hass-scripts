import asyncio

import pytest

from conftest import make_app, state

from scene_transition.app import SceneTransition, TransitionError
from scene_transition.transitions import transition_to_light
from scene_transition.validation import validate_transition_state, verify_and_correct_target
import scene_transition.steps as steps_module
import scene_transition.validation as validation_module
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

async def test_brightness_transition_calls_expected_steps():
    app = make_app({
        "light.group": state(attributes={
            "brightness": 50,
            "hs_color": [200, 50],
            "group_entities": [],
        }),
    })
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.group",
        {
            "brightness": 100,
            "hs_color": [200, 50],
            "color_temp_kelvin": None,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )

    assert [call[1]["brightness"] for call in calls] == [75, 100]


async def test_group_member_turning_off_aborts_transition():
    app = make_app({
        "light.group": state(attributes={
            "brightness": 50,
            "hs_color": [200, 50],
            "group_entities": ["light.member_off"],
        }),
        "light.member_off": state("off"),
    })

    async def turn_on(entity_id, **kwargs):
        return None

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op
    steps_module.validate_transition_state = validation_module.validate_transition_state

    with pytest.raises(TransitionError, match="contains off member"):
        await transition_to_light(app,
            "light.group",
            {
                "brightness": 100,
                "hs_color": [200, 50],
                "color_temp_kelvin": None,
            },
            transition_seconds=30,
            scene_entity_id="scene.test",
        )


async def test_transition_skips_light_already_at_target():
    app = make_app({
        "light.group": state(attributes={
            "brightness": 100,
            "hs_color": [200, 50],
            "group_entities": [],
        }),
    })
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))

    app.turn_on = turn_on

    await transition_to_light(app,
        "light.group",
        {
            "brightness": 100,
            "hs_color": [200, 50],
            "color_temp_kelvin": None,
        },
        transition_seconds=30,
        scene_entity_id="scene.test",
    )

    assert calls == []


async def test_transition_pipeline_does_not_verify_twice():
    app = make_app({
        "light.test": state(attributes={
            "brightness": 100,
            "hs_color": [20, 60],
        }),
    })
    calls = []

    async def turn_on(*args, **kwargs):
        return None

    async def sleep(*args, **kwargs):
        return None

    async def verify(entity_id, target_values, started_off=False):
        calls.append((entity_id, target_values, started_off))

    async def validate(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = sleep

    await transition_to_light(app,
        "light.test",
        {
            "brightness": 255,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )

    assert len(calls) <= 1



async def test_entity_logs_include_scene_and_entity_prefix():
    app = make_app({})
    app._entity_scene_ids["light.test"] = "scene.vegard_kveld"
    messages = []
    app.log = lambda message, **kwargs: messages.append(message)

    app.log_entity("light.test", "Step 1/1")

    assert messages == [
        "[scene.vegard_kveld][light.test] Step 1/1",
    ]


async def test_off_hs_light_starts_from_first_reported_color():
    states = {
        "light.off": state("off", {
            "brightness": None,
            "hs_color": None,
            "supported_color_modes": ["xy"],
        }),
    }
    app = make_app(states)
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))
        states["light.off"] = state("on", {
            "brightness": kwargs.get("brightness", 100),
            "hs_color": [200, 50],
            "supported_color_modes": ["xy"],
        })

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.off",
        {
            "brightness": 100,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )

    brightness_calls = [
        kwargs["brightness"]
        for _entity_id, kwargs in calls
        if "brightness" in kwargs
    ]
    hs_calls = [
        kwargs["hs_color"]
        for _entity_id, kwargs in calls
        if "hs_color" in kwargs
    ]

    assert brightness_calls == [50, 100]
    assert hs_calls == [(210, 55), (220, 60)]


async def test_off_hs_light_never_fabricates_startup_color():
    states = {
        "light.off": state("off", {
            "brightness": None,
            "hs_color": None,
            "supported_color_modes": ["xy"],
        }),
    }
    app = make_app(states)
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))
        states["light.off"] = state("on", {
            "brightness": kwargs.get("brightness", 100),
            "hs_color": None,
            "supported_color_modes": ["xy"],
        })

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.off",
        {
            "brightness": 100,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )

    hs_calls = [
        kwargs["hs_color"]
        for _entity_id, kwargs in calls
        if "hs_color" in kwargs
    ]
    assert hs_calls == [(220, 60), (220, 60)]


async def test_off_hs_light_preserves_reported_color_baseline():
    states = {
        "light.off": state("off", {
            "brightness": 0,
            "hs_color": [200, 50],
            "supported_color_modes": ["xy"],
        }),
    }
    app = make_app(states)
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))
        states["light.off"] = state("on", {
            "brightness": kwargs.get("brightness", 100),
            "hs_color": [200, 50],
            "supported_color_modes": ["xy"],
        })

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.off",
        {
            "brightness": 100,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )

    hs_calls = [
        kwargs["hs_color"]
        for _entity_id, kwargs in calls
        if "hs_color" in kwargs
    ]
    assert hs_calls == [(210, 55), (220, 60)]


async def test_off_kelvin_light_clamps_scene_target_to_bulb_maximum():
    states = {
        "light.ikea": state("off", {
            "brightness": 0,
            "hs_color": None,
            "color_temp_kelvin": None,
            "supported_color_modes": ["color_temp"],
            "min_color_temp_kelvin": 2202,
            "max_color_temp_kelvin": 4000,
        }),
    }
    app = make_app(states)
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))
        states["light.ikea"] = state("on", {
            "brightness": kwargs.get("brightness", 255),
            "hs_color": None,
            "color_temp_kelvin": 2700,
            "supported_color_modes": ["color_temp"],
            "min_color_temp_kelvin": 2202,
            "max_color_temp_kelvin": 4000,
        })

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.ikea",
        {
            "brightness": 255,
            "hs_color": [219, 49],
            "color_temp_kelvin": 5198,
        },
        transition_seconds=1,
        scene_entity_id="scene.test",
    )

    kelvin_calls = [
        kwargs["color_temp_kelvin"]
        for _entity_id, kwargs in calls
        if "color_temp_kelvin" in kwargs
    ]
    assert kelvin_calls == [4000]


async def test_off_brightness_only_light_needs_no_hs_color():
    app = make_app({
        "light.hue_plug": state("off", {
            "brightness": 0,
            "hs_color": None,
            "supported_color_modes": ["brightness"],
        }),
    })
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.hue_plug",
        {
            "brightness": 255,
            "hs_color": [0, 0],
            "color_temp_kelvin": None,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )

    assert [kwargs["brightness"] for _entity_id, kwargs in calls] == [128, 255]
    assert all("hs_color" not in kwargs for _entity_id, kwargs in calls)
    assert all("color_temp_kelvin" not in kwargs for _entity_id, kwargs in calls)


async def test_off_light_state_is_allowed_to_propagate_once():
    states = {
        "light.starting_off": state("off", {
            "brightness": 0,
            "hs_color": None,
            "supported_color_modes": ["brightness"],
        }),
    }
    app = make_app(states)
    app._lights_started_off = {"light.starting_off"}
    warnings = []
    app.log_entity = lambda entity_id, message, level="INFO", **kwargs: warnings.append(
        (entity_id, message, level)
    )

    await validate_transition_state(app,
        "light.starting_off",
        target_values={
            "brightness": 128,
            "hs_color": None,
            "color_temp_kelvin": None,
        },
        validation_key="brightness",
        error_message="mismatch",
    )

    assert ("light.starting_off", "Light did not respond", "WARNING") in warnings


async def test_off_light_is_cancelled_after_it_was_on_then_manually_turned_off():
    states = {
        "light.starting_off": state("on", {
            "brightness": 128,
            "hs_color": None,
            "supported_color_modes": ["brightness"],
        }),
    }
    app = make_app(states)
    app._lights_started_off = {"light.starting_off"}

    await validate_transition_state(app,
        "light.starting_off",
        target_values={
            "brightness": 128,
            "hs_color": None,
            "color_temp_kelvin": None,
        },
        validation_key="brightness",
        error_message="mismatch",
    )


async def test_final_verification_skips_started_off_light():
    app = make_app({
    "light.hue_plug": state("off", {
        "brightness": 0,
        "hs_color": None,
        "supported_color_modes": ["brightness"],
    }),
    })
    app._lights_started_off = {"light.hue_plug"}

    await verify_and_correct_target(app,
    "light.hue_plug",
    {
        "brightness": 255,
        "hs_color": None,
        "color_temp_kelvin": None,
    },
    )

    assert "light.hue_plug" not in app._lights_started_off


async def test_final_verification_cancels_manually_turned_off_light():
    app = make_app({
        "light.vegard_taklampe": state("off", {
            "brightness": 0,
            "hs_color": None,
            "supported_color_modes": ["color_temp", "xy"],
        }),
    })

    with pytest.raises(ValueError, match="turned off after or during"):
        await verify_and_correct_target(app,
            "light.vegard_taklampe",
            {
                "brightness": 255,
                "hs_color": [220, 60],
                "color_temp_kelvin": None,
            },
        )


async def test_final_verification_uses_per_transition_started_off_flag():
    app = make_app({
        "light.hue_plug": state("off", {
            "brightness": 0,
            "hs_color": None,
            "supported_color_modes": ["xy"],
        }),
    })
    app._lights_started_off = set()

    await verify_and_correct_target(app,
        "light.hue_plug",
        {
            "brightness": 255,
            "hs_color": None,
            "color_temp_kelvin": None,
        },
        started_off=True,
    )


async def test_kelvin_only_and_hs_color_lights_transition_in_their_supported_modes():
    app = make_app({
        "light.kelvin": state(attributes={
            "brightness": 100,
            "color_temp_kelvin": 3000,
            "hs_color": None,
            "group_entities": [],
            "supported_color_modes": ["color_temp"],
        }),
        "light.color": state(attributes={
            "brightness": 100,
            "hs_color": [200, 50],
            "color_temp_kelvin": None,
            "group_entities": [],
            "supported_color_modes": ["xy"],
        }),
    })
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.kelvin",
        {
            "brightness": 150,
            "hs_color": [0, 0],
            "color_temp_kelvin": 4000,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )
    await transition_to_light(app,
        "light.color",
        {
            "brightness": 150,
            "hs_color": [220, 60],
            "color_temp_kelvin": None,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )

    kelvin_calls = [kwargs for entity_id, kwargs in calls if entity_id == "light.kelvin"]
    color_calls = [kwargs for entity_id, kwargs in calls if entity_id == "light.color"]

    assert kelvin_calls == [
        {"brightness": 125, "transition": 15.0},
        {"color_temp_kelvin": 3500, "transition": 15.0},
        {"brightness": 150, "transition": 15.0},
        {"color_temp_kelvin": 4000, "transition": 15.0},
    ]
    assert [kwargs.get("hs_color") for kwargs in color_calls if "hs_color" in kwargs] == [
        (210, 55),
        (220, 60),
    ]


async def test_transition_across_more_than_one_step():
    app = make_app({
        "light.group": state(attributes={
            "brightness": 40,
            "hs_color": [180, 40],
            "group_entities": [],
        }),
    })
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.group",
        {
            "brightness": 100,
            "hs_color": [180, 40],
            "color_temp_kelvin": None,
        },
        transition_seconds=90,
        scene_entity_id="scene.test",
    )

    assert [kwargs["brightness"] for _entity_id, kwargs in calls] == [60, 80, 100]


async def test_transition_to_white_before_target_color():
    app = make_app({
        "light.group": state(attributes={
            "brightness": 100,
            "hs_color": [300, 50],
            "group_entities": [],
        }),
    })
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.group",
        {
            "brightness": 100,
            "hs_color": [30, 50],
            "color_temp_kelvin": None,
        },
        transition_seconds=120,
        scene_entity_id="scene.test",
    )

    hs_calls = [
        kwargs["hs_color"]
        for _entity_id, kwargs in calls
        if "hs_color" in kwargs
    ]

    assert hs_calls == [
        (150, 25),
        (0, 0),
        (15, 25),
        (30, 50),
    ]


async def test_individual_light_transition_targets_only_the_individual_entity():
    app = make_app({
        "light.individual": state(attributes={
            "brightness": 50,
            "hs_color": [200, 50],
        }),
    })
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.individual",
        {
            "brightness": 100,
            "hs_color": [200, 50],
            "color_temp_kelvin": None,
        },
        transition_seconds=60,
        scene_entity_id="scene.test",
    )

    assert all(entity_id == "light.individual" for entity_id, _kwargs in calls)


async def test_transition_across_sixty_and_ninety_seconds():
    for transition_seconds, expected_brightness in (
        (60, [75, 100]),
        (90, [67, 83, 100]),
    ):
        app = make_app({
            "light.individual": state("on", {
                "brightness": 50,
                "hs_color": [200, 50],
            }),
        })
        calls = []

        async def turn_on(entity_id, **kwargs):
            calls.append((entity_id, kwargs))

        async def no_op(*args, **kwargs):
            return None

        app.turn_on = turn_on
        app.sleep = no_op

        await transition_to_light(app,
            "light.individual",
            {
                "brightness": 100,
                "hs_color": [200, 50],
                "color_temp_kelvin": None,
            },
            transition_seconds=transition_seconds,
            scene_entity_id="scene.test",
        )

        assert [kwargs["brightness"] for _entity_id, kwargs in calls] == expected_brightness


@pytest.mark.parametrize(
    ("transition_seconds", "expected_step_count", "expected_first", "expected_last"),
    [
        (1, 1, 100, 100),
        (2, 1, 100, 100),
        (1800, 60, 51, 100),
        (3600, 120, 50, 100),
    ],
)
async def test_transition_duration_boundaries(
    transition_seconds,
    expected_step_count,
    expected_first,
    expected_last,
):
    app = make_app({
        "light.individual": state("on", {
            "brightness": 50,
            "hs_color": [200, 50],
        }),
    })
    calls = []

    async def turn_on(entity_id, **kwargs):
        calls.append((entity_id, kwargs))

    async def no_op(*args, **kwargs):
        return None

    app.turn_on = turn_on
    app.sleep = no_op

    await transition_to_light(app,
        "light.individual",
        {
            "brightness": 100,
            "hs_color": [200, 50],
            "color_temp_kelvin": None,
        },
        transition_seconds=transition_seconds,
        scene_entity_id="scene.test",
    )

    brightness_calls = [
        kwargs["brightness"]
        for _entity_id, kwargs in calls
        if "brightness" in kwargs
    ]

    assert len(brightness_calls) == expected_step_count
    assert brightness_calls[0] == expected_first
    assert brightness_calls[-1] == expected_last
