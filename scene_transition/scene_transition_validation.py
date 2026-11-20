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
