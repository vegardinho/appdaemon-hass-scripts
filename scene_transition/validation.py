from .lights import get_light_attributes, get_off_group_members


def is_at_target(
    current_values,
    target_values,
    brightness_tolerance=2,
    hue_tolerance=2,
    sat_tolerance=2,
    color_temp_tolerance=50,
):
    brightness = current_values.get("brightness")
    brightness_match = (
        brightness is not None
        and abs(brightness - target_values["brightness"]) <= brightness_tolerance
    )

    use_color_temp = target_values.get("color_temp_kelvin") is not None
    if use_color_temp:
        current_temp = current_values.get("color_temp_kelvin")
        color_match = (
            current_temp is not None
            and abs(current_temp - target_values["color_temp_kelvin"])
            <= color_temp_tolerance
        )
    else:
        current_hs = current_values.get("hs_color")
        target_hs = target_values.get("hs_color")
        if target_hs is None:
            color_match = True
        elif current_hs is None:
            color_match = False
        else:
            hue_match = (
                abs(current_hs[0] - target_hs[0]) <= hue_tolerance
            )
            sat_match = (
                abs(current_hs[1] - target_hs[1]) <= sat_tolerance
            )
            color_match = hue_match and sat_match

    return {
        "brightness": brightness_match,
        "color": color_match,
        "overall": brightness_match and color_match,
    }


def get_required_validation_value(current_values, target_values, validation_key):
    if validation_key == "brightness":
        return current_values.get("brightness")
    if validation_key == "color":
        if target_values.get("color_temp_kelvin") is not None:
            return current_values.get("color_temp_kelvin")
        return current_values.get("hs_color")
    return None


def describe_target_mismatch(
    current_values,
    target_values,
    validation_key,
    fallback_message,
):
    if validation_key == "brightness":
        return (
            f"Brightness mismatch: expected {target_values.get('brightness')}, "
            f"got {current_values.get('brightness')}"
        )

    if validation_key == "color":
        if target_values.get("color_temp_kelvin") is not None:
            return (
                f"Color temp mismatch: "
                f"expected {target_values.get('color_temp_kelvin')}K, "
                f"got {current_values.get('color_temp_kelvin')}K"
            )
        return (
            f"HS Color mismatch: expected {target_values.get('hs_color')}, "
            f"got {current_values.get('hs_color')}"
        )

    return fallback_message


def get_corrections_needed(current_values, target_values):
    match_status = is_at_target(current_values, target_values)
    corrections = []

    if not match_status["brightness"]:
        corrections.append("brightness")

    if not match_status["color"]:
        if target_values.get("color_temp_kelvin") is not None:
            corrections.append("color_temp")
        else:
            corrections.append("hs_color")

    return corrections

async def validate_transition_state(app, entity_id, target_values, validation_key, error_message):
    current = await get_light_attributes(app, entity_id)
    if not current["available"]:
        app.log_entity(entity_id, "Skipping transition validation for unavailable or unknown light", level="WARNING")
        return

    if current["state"] == "off":
        if entity_id in getattr(app, "_lights_started_off", set()):
            app.log_entity(entity_id, "Light is starting from off; waiting for the turn-on state to propagate.", level="DEBUG")
            return
        error_msg = f"Light {entity_id} was turned off while the transition was running; cancelling transition."
        app.log_entity(entity_id, error_msg, level="ERROR")
        raise ValueError(error_msg)

    if current["state"] == "on":
        getattr(app, "_lights_started_off", set()).discard(entity_id)

    off_members = await get_off_group_members(app, entity_id)
    if off_members:
        error_msg = f"Light group {entity_id} contains off member(s) {', '.join(off_members)} while the transition was running; cancelling transition."
        app.log_entity(entity_id, error_msg, level="ERROR")
        raise ValueError(error_msg)

    required_value = get_required_validation_value(current, target_values, validation_key)
    if required_value is None:
        error_msg = f"Transition validation could not read current {validation_key} value for {entity_id}. The light may have been turned off while the transition was running; cancelling transition."
        app.log_entity(entity_id, error_msg, level="ERROR")
        raise ValueError(error_msg)

    match_status = is_at_target(current, target_values)
    if not match_status[validation_key]:
        detailed_msg = describe_target_mismatch(current, target_values, validation_key, error_message)
        error_msg = f"Lights not at expected value according to transition step. {detailed_msg} for {entity_id}"
        app.log_entity(entity_id, error_msg, level="ERROR")
        raise ValueError(error_msg)


async def verify_and_correct_target(app, entity_id, target_values, started_off=None):
    final_values = await get_light_attributes(app, entity_id)
    if not final_values["available"]:
        app.log_entity(entity_id, "Skipping final verification for unavailable or unknown light", level="WARNING")
        return

    if final_values["state"] == "off":
        if started_off is None:
            started_off = entity_id in getattr(app, "_lights_started_off", set())
        if started_off:
            app.log_entity(entity_id, "Light started off and is still off during final verification; skipping correction.", level="WARNING")
            getattr(app, "_lights_started_off", set()).discard(entity_id)
            return
        raise ValueError(f"Light {entity_id} was turned off after or during the transition; cancelling transition.")

    off_members = await get_off_group_members(app, entity_id)
    if off_members:
        raise ValueError(f"Light group {entity_id} contains off member(s) {', '.join(off_members)} after or during the transition; cancelling transition.")

    match_status = is_at_target(final_values, target_values)
    corrections_needed = get_corrections_needed(final_values, target_values)
    use_color_temp = target_values.get("color_temp_kelvin") is not None

    if not match_status["brightness"]:
        app.log_entity(entity_id, f"Brightness mismatch: {final_values['brightness']} vs target {target_values['brightness']}")
    if not match_status["color"]:
        if use_color_temp:
            app.log_entity(entity_id, f"Color temp mismatch: {final_values.get('color_temp_kelvin')} vs target {target_values['color_temp_kelvin']}")
        else:
            app.log_entity(entity_id, f"HS color mismatch: {final_values['hs_color']} vs target {target_values['hs_color']}")

    if corrections_needed:
        app.log_entity(entity_id, f"Applying corrections: {corrections_needed}")
        if "brightness" in corrections_needed:
            await app.turn_on(entity_id=entity_id, brightness=target_values["brightness"], transition=1)
            await app.sleep(1)
        if "color_temp" in corrections_needed:
            await app.turn_on(entity_id=entity_id, color_temp_kelvin=target_values["color_temp_kelvin"], transition=1)
            await app.sleep(1)
        if "hs_color" in corrections_needed:
            hue, saturation = target_values["hs_color"]
            await app.turn_on(entity_id=entity_id, hs_color=(hue, saturation), transition=1)
            await app.sleep(1)
