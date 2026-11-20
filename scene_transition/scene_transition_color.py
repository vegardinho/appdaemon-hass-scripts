def hs_to_kelvin(hue, sat):
    """
    Convert an HS color to an approximate color temperature in Kelvin.
    """
    if sat < 10:
        return 4000

    if 20 <= hue <= 300:
        return int(2000 + (hue - 20) * (6500 - 2000) / (300 - 20))

    return 2000 if hue < 20 else 6500


def supports_hs_or_xy(attributes):
    modes = attributes.get("supported_color_modes", [])
    if isinstance(modes, str):
        modes = [mode.strip() for mode in modes.split(",")]
    return bool(set(modes or []) & {"hs", "xy", "rgb", "rgbw", "rgbww"})


def supports_kelvin(attributes):
    modes = attributes.get("supported_color_modes", [])
    if isinstance(modes, str):
        modes = [mode.strip() for mode in modes.split(",")]
    return "color_temp" in (modes or [])


def get_kelvin_range(attributes):
    minimum = attributes.get("min_color_temp_kelvin")
    maximum = attributes.get("max_color_temp_kelvin")
    return minimum, maximum


def convert_hs_to_kelvin(hs_color):
    if not hs_color or len(hs_color) != 2:
        return None
    hue, saturation = hs_color
    return hs_to_kelvin(hue, saturation)


def adapt_target_to_light(light_attributes, scene_target):
    """
    Return a target shape supported by one light entity.

    Kelvin values are only emitted for bulbs that support color_temp.
    HS/XY values are preserved for bulbs that support an HS-like mode.
    """
    adapted = {
        "brightness": scene_target.get("brightness"),
        "hs_color": scene_target.get("hs_color"),
        "color_temp_kelvin": scene_target.get("color_temp_kelvin"),
    }

    supports_kelvin_mode = supports_kelvin(light_attributes)
    supports_hs_mode = supports_hs_or_xy(light_attributes)
    hs_color = adapted["hs_color"]
    modes = light_attributes.get("supported_color_modes")
    if isinstance(modes, str):
        modes = [mode.strip() for mode in modes.split(",")]
    if not modes:
        return adapted

    if supports_kelvin_mode and not supports_hs_mode:
        if adapted["color_temp_kelvin"] is None and hs_color is not None:
            adapted["color_temp_kelvin"] = convert_hs_to_kelvin(hs_color)
        adapted["hs_color"] = [0, 0]
        return adapted

    if supports_hs_mode:
        if adapted["color_temp_kelvin"] is not None:
            adapted["hs_color"] = [0, 0]
            return adapted
        return adapted

    adapted["hs_color"] = None
    adapted["color_temp_kelvin"] = None
    return adapted
