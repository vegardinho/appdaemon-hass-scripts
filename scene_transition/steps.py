from .lights import get_light_attributes
from .validation import validate_transition_state


def calculate_step_count(transition_seconds, step_seconds):
    total_steps = transition_seconds // step_seconds
    remainder = transition_seconds % step_seconds
    if remainder > 0:
        total_steps += 1
    return total_steps, remainder


def calculate_step_duration(step_number, total_steps, remainder, step_seconds):
    if step_number == total_steps and remainder > 0:
        return remainder
    return step_seconds


def interpolate(start, end, step_number, total_steps):
    progress = step_number / total_steps
    return round(start + (end - start) * progress)


def interpolate_hs(hue_start, sat_start, hue_end, sat_end, step_number, total_steps):
    return (
        interpolate(hue_start, hue_end, step_number, total_steps),
        interpolate(sat_start, sat_end, step_number, total_steps),
    )


def calculate_phase_plan(total_steps, needs_two_phases):
    if not needs_two_phases:
        return 0, total_steps
    steps_to_white = total_steps // 2
    return steps_to_white, total_steps - steps_to_white



async def capture_startup_state(app, entity_id):
    for attempt in range(2):
        startup_values = await get_light_attributes(app, entity_id)
        if (
            startup_values["available"]
            and startup_values["state"] == "on"
            and (
                startup_values["hs_color"] is not None
                or startup_values["color_temp_kelvin"] is not None
            )
        ):
            return startup_values
        if attempt == 0:
            await app.sleep(0.1)
    return startup_values


async def execute_transition_step(
    app,
    step,
    total_steps,
    remainder,
    needs_brightness,
    needs_color,
    brightness_start,
    brightness_end,
    hue_start,
    sat_start,
    hue_end,
    sat_end,
    white_hue,
    white_sat,
    use_color_temp,
    color_temp_start,
    color_temp_end,
    entity_id,
    steps_to_white,
    steps_to_target,
    needs_two_phases,
    scene_entity_id,
    startup_context=None,
):
    step_num = step + 1
    step_duration = calculate_step_duration(
        step_num,
        total_steps,
        remainder,
        app.transition_step_seconds,
    )
    is_last_step = (step_num == total_steps)

    # Log step header once at the beginning
    app.log_entity(entity_id, f"Step {step_num}/{total_steps}", scene_entity_id=scene_entity_id)


    if needs_brightness and needs_color:
        brightness_duration = step_duration / 2
        color_duration = step_duration / 2
    else:
        brightness_duration = step_duration if needs_brightness else 0
        color_duration = step_duration if needs_color else 0

    # Apply brightness first if needed
    if needs_brightness:
        new_brightness = interpolate(
            brightness_start,
            brightness_end,
            step_num,
            total_steps,
        )

        # Subtract 1 from transition to avoid overlap, but only if duration is long enough
        # and not the last transition overall
        is_last_transition = is_last_step and not needs_color
        should_subtract = brightness_duration >= app.transition_step_seconds and not is_last_transition
        brightness_transition = brightness_duration - 1 if should_subtract else brightness_duration

        await app.turn_on(
            entity_id=entity_id,
            brightness=new_brightness,
            transition=brightness_transition,
        )
        # Log brightness
        app.log_entity(entity_id, f"a) Brightness target: {new_brightness}", scene_entity_id=scene_entity_id)
        # Log color if not adjusting it (static color)
        if not needs_color:
            if use_color_temp and color_temp_end is not None:
                app.log_entity(entity_id, f"b) Color temp target: {color_temp_end}K", scene_entity_id=scene_entity_id)
            elif not use_color_temp and hue_end is not None and sat_end is not None:
                app.log_entity(entity_id, f"b) HS Color target: ({hue_end}, {sat_end})", scene_entity_id=scene_entity_id)

        await app.sleep(brightness_duration)
        if (
            startup_context is not None
            and startup_context.get("was_off")
            and not startup_context.get("captured")
        ):
            startup_values = await capture_startup_state(app, entity_id)
            startup_context["captured"] = True

            if use_color_temp:
                startup_temp = startup_values.get("color_temp_kelvin")
                startup_context["color_temp_start"] = (
                    startup_temp
                    if startup_temp is not None
                    else color_temp_end
                )
                color_temp_start = startup_context["color_temp_start"]
            elif hue_end is not None and sat_end is not None:
                startup_hs = startup_values.get("hs_color")
                if startup_hs is not None and len(startup_hs) == 2:
                    startup_context["hue_start"] = startup_hs[0]
                    startup_context["sat_start"] = startup_hs[1]
                    hue_start = startup_hs[0]
                    sat_start = startup_hs[1]
                else:
                    startup_context["hue_start"] = hue_end
                    startup_context["sat_start"] = sat_end
                    hue_start = hue_end
                    sat_start = sat_end

                if abs(hue_start - hue_end) > app.hue_difference_threshold:
                    needs_two_phases = True
                    steps_to_white = total_steps // 2
                    steps_to_target = total_steps - steps_to_white
                    startup_context["needs_two_phases"] = True
                    startup_context["steps_to_white"] = steps_to_white
                    startup_context["steps_to_target"] = steps_to_target

        if startup_context is not None and startup_context.get("captured"):
            if "color_temp_start" in startup_context:
                color_temp_start = startup_context["color_temp_start"]
            if "hue_start" in startup_context:
                hue_start = startup_context["hue_start"]
                sat_start = startup_context["sat_start"]
            if "needs_two_phases" in startup_context:
                needs_two_phases = startup_context["needs_two_phases"]
                steps_to_white = startup_context["steps_to_white"]
                steps_to_target = startup_context["steps_to_target"]

        await validate_transition_state(
            app,
            entity_id=entity_id,
            target_values={"brightness": new_brightness, "hs_color": [0, 0], "color_temp_kelvin": None},
            validation_key="brightness",
            error_message=f"Brightness validation FAILED for {entity_id}: expected {new_brightness}, got {new_brightness}"
        )

    # Apply color second if needed
    if needs_color:
        # Determine if we should subtract 1 to avoid overlap
        # Only for long transitions and not the last transition overall
        should_subtract = color_duration >= app.transition_step_seconds and not is_last_step
        color_transition = color_duration - 1 if should_subtract else color_duration

        if step < steps_to_white:
            # Color adjustment to white
            progress = (step + 1) / steps_to_white
            new_hue, new_sat = interpolate_hs(
                hue_start,
                sat_start,
                white_hue,
                white_sat,
                step + 1,
                steps_to_white,
            )

            await app.turn_on(
                entity_id=entity_id,
                hs_color=(new_hue, new_sat),
                transition=color_transition,
            )
            app.log_entity(entity_id, f"b) HS Color: ({new_hue}, {new_sat})", scene_entity_id=scene_entity_id)
            await app.sleep(color_duration)

            await validate_transition_state(
                app,
                entity_id=entity_id,
                target_values={"brightness": brightness_end, "hs_color": [new_hue, new_sat], "color_temp_kelvin": None},
                validation_key="color",
                error_message=f"Color validation FAILED for {entity_id} (white phase): expected ({new_hue}, {new_sat}), got ({new_hue}, {new_sat})"
            )
        else:
            # Color adjustment to target
            target_step = step - steps_to_white

            if use_color_temp:
                progress = (target_step + 1) / steps_to_target
                new_color_temp = interpolate(
                    color_temp_start,
                    color_temp_end,
                    target_step + 1,
                    steps_to_target,
                )
                await app.turn_on(
                    entity_id=entity_id,
                    color_temp_kelvin=new_color_temp,
                    transition=color_transition,
                )
                app.log_entity(entity_id, f"b) Color temp target: {new_color_temp}K", scene_entity_id=scene_entity_id)
                await app.sleep(color_duration)

                await validate_transition_state(
                    app,
                    entity_id=entity_id,
                    target_values={"brightness": brightness_end, "hs_color": [0, 0], "color_temp_kelvin": new_color_temp},
                    validation_key="color",
                    error_message=f"Color temp validation FAILED for {entity_id}: expected {new_color_temp}K, got {new_color_temp}K"
                )
            else:
                transition_hue_start = white_hue if needs_two_phases else hue_start
                transition_sat_start = white_sat if needs_two_phases else sat_start
                new_hue, new_sat = interpolate_hs(
                    transition_hue_start,
                    transition_sat_start,
                    hue_end,
                    sat_end,
                    target_step + 1,
                    steps_to_target,
                )
                await app.turn_on(
                    entity_id=entity_id,
                    hs_color=(new_hue, new_sat),
                    transition=color_transition,
                )
                app.log_entity(entity_id, f"b) HS Color target: ({new_hue}, {new_sat})", scene_entity_id=scene_entity_id)
                await app.sleep(color_duration)

                await validate_transition_state(
                    app,
                    entity_id=entity_id,
                    target_values={"brightness": brightness_end, "hs_color": [new_hue, new_sat], "color_temp_kelvin": None},
                    validation_key="color",
                    error_message=f"Color validation FAILED for {entity_id} (target phase): expected ({new_hue}, {new_sat}), got ({new_hue}, {new_sat})"
                )
