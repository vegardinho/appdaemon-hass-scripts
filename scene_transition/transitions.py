import asyncio
import traceback

from .color import adapt_target_to_light
from .errors import TransitionError
from .lights import get_light_attributes
from .steps import (
    calculate_phase_plan,
    calculate_step_count,
    execute_transition_step,
)
from .validation import is_at_target, verify_and_correct_target


async def transition_to_light(app, entity_id, target_values, transition_seconds, scene_entity_id):
    """
    Transition a light to the target values with brightness and color adjustments.

    Args:
        entity_id (str): The entity ID of the light.
        target_values (dict): Target attribute values.
        transition_seconds (int): Transition time in seconds.
        scene_entity_id (str): The scene entity ID (for error tracking).
    """
    getattr(app, "_entity_scene_ids", {})[entity_id] = scene_entity_id
    try:
        # Get current state
        current_values = await get_light_attributes(app, entity_id)
        if not current_values["available"]:
            app.log_entity(
                entity_id,
                "Skipping transition for unavailable or unknown light",
                level="WARNING",
            )
            return

        target_values = adapt_target_to_light(
            current_values,
            target_values,
        )

        # If the light is off, establish a baseline for its color and
        # brightness before transitioning it on. Kelvin-only lights may
        # legitimately report hs_color=None while their state is on.
        if current_values["state"] == "off":
            app.log_entity(entity_id, "Light is currently off, will transition from off state")
            current_values["brightness"] = 0
            getattr(app, "_lights_started_off", set()).add(entity_id)
            started_off = True
        elif current_values["hs_color"] is not None and len(current_values["hs_color"]) != 2:
            raise ValueError(f"Invalid hs_color for {entity_id}: {current_values['hs_color']}")
        else:
            started_off = False

        # Check if already at target
        match_status = is_at_target(current_values, target_values)

        if match_status['overall']:
            app.log_entity(entity_id, "Already at target, skipping transition")
            return

        # Determine what needs adjusting
        needs_brightness = not match_status['brightness']
        needs_color = not match_status['color']

        # Log current and target values
        if current_values.get("color_temp_kelvin") is not None:
            app.log_entity(entity_id, f"Current: brightness={current_values['brightness']}, color_temp={current_values['color_temp_kelvin']}K")
        else:
            app.log_entity(entity_id, f"Current: brightness={current_values['brightness']}, hs_color={current_values['hs_color']}")

        use_color_temp_for_target = target_values.get("color_temp_kelvin") is not None
        if use_color_temp_for_target:
            app.log_entity(entity_id, f"Target: brightness={target_values['brightness']}, color_temp={target_values['color_temp_kelvin']}K")
        else:
            app.log_entity(entity_id, f"Target: brightness={target_values['brightness']}, hs_color={target_values['hs_color']}")

        if needs_brightness and needs_color:
            app.log_entity(entity_id, "Both brightness and color need adjustment")
        elif needs_brightness:
            app.log_entity(entity_id, "Only brightness needs adjustment")
        elif needs_color:
            app.log_entity(entity_id, "Only color needs adjustment")

        # Calculate steps
        # Each step takes transition_step_seconds regardless of what's being adjusted
        total_steps, remainder = calculate_step_count(
            transition_seconds,
            app.transition_step_seconds,
        )

        # Determine transition mode (only if color needs adjusting)
        use_color_temp = False
        needs_two_phases = False
        steps_to_white = 0
        steps_to_target = total_steps

        if needs_color:
            # Use color_temp mode if target specifies it
            use_color_temp = target_values.get("color_temp_kelvin") is not None

            # Check if we need two-phase transition (via white) - only for hs_color mode
            if (
                not use_color_temp
                and target_values.get("hs_color") is not None
                and current_values.get("hs_color") is not None
            ):
                hue_diff = abs(current_values["hs_color"][0] - target_values["hs_color"][0])
                if hue_diff > app.hue_difference_threshold:
                    needs_two_phases = True
                    app.log_entity(entity_id, f"Hue diff {hue_diff} > {app.hue_difference_threshold}, using 2-phase transition")

            # Allocate steps for color transition
            steps_to_white, steps_to_target = calculate_phase_plan(
                total_steps,
                needs_two_phases,
            )

        app.log_entity(
            entity_id,
            f"Plan: {total_steps} steps ({steps_to_white} to-white, {steps_to_target} to-target)",
        )

        # Setup interpolation parameters
        brightness_start = None
        brightness_end = None
        hue_start = None
        sat_start = None
        hue_end = None
        sat_end = None
        white_hue = None
        white_sat = None
        color_temp_start = None
        color_temp_end = None

        if needs_brightness:
            brightness_start = current_values["brightness"]
            brightness_end = target_values["brightness"]

        if needs_color:
            if use_color_temp:
                # For color_temp transitions, use current color_temp if available, otherwise start from target
                # (light will switch modes immediately)
                color_temp_start = current_values.get("color_temp_kelvin") or target_values["color_temp_kelvin"]
                color_temp_end = target_values["color_temp_kelvin"]
            elif current_values["hs_color"] is not None:
                hue_start, sat_start = current_values["hs_color"]
                hue_end, sat_end = target_values["hs_color"]
                white_hue = 0
                white_sat = 0
            else:
                hue_end, sat_end = target_values["hs_color"]
                white_hue = 0
                white_sat = 0

        # Execute transition - brightness first, then color in each step
        startup_context = {
            "was_off": current_values["state"] == "off",
            "captured": False,
        }
        for step in range(total_steps):
            await execute_transition_step(app,
                step=step,
                total_steps=total_steps,
                remainder=remainder,
                needs_brightness=needs_brightness,
                needs_color=needs_color,
                brightness_start=brightness_start,
                brightness_end=brightness_end,
                hue_start=hue_start,
                sat_start=sat_start,
                hue_end=hue_end,
                sat_end=sat_end,
                white_hue=white_hue,
                white_sat=white_sat,
                use_color_temp=use_color_temp,
                color_temp_start=color_temp_start,
                color_temp_end=color_temp_end,
                entity_id=entity_id,
                steps_to_white=steps_to_white,
                steps_to_target=steps_to_target,
                needs_two_phases=needs_two_phases,
                scene_entity_id=scene_entity_id,
                startup_context=startup_context,
            )

        # Verify and correct final state
        await verify_and_correct_target(
            app,
            entity_id,
            target_values,
            started_off=started_off,
        )

        app.log_entity(entity_id, "Transition completed")
        getattr(app, "_entity_scene_ids", {}).pop(entity_id, None)
    except asyncio.CancelledError:
        app.log_entity(entity_id, "Transition cancelled", level="DEBUG")
        getattr(app, "_entity_scene_ids", {}).pop(entity_id, None)
        return
    except ValueError as e:
        getattr(app, "_entity_scene_ids", {}).pop(entity_id, None)
        # Wrap validation errors as TransitionError
        raise TransitionError(str(e), entity_id=entity_id) from e
    except Exception as e:
        app.log_entity(entity_id, f"Error in transition: {traceback.format_exc()}", level="ERROR")
        getattr(app, "_entity_scene_ids", {}).pop(entity_id, None)
        raise
