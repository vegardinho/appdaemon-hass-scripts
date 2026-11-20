def brightness_turn_on_kwargs(brightness, transition):
    return {
        "brightness": brightness,
        "transition": transition,
    }


def hs_turn_on_kwargs(hs_color, transition):
    hue, saturation = hs_color
    return {
        "hs_color": (hue, saturation),
        "transition": transition,
    }


def kelvin_turn_on_kwargs(color_temp_kelvin, transition):
    return {
        "color_temp_kelvin": color_temp_kelvin,
        "transition": transition,
    }


def assert_no_legacy_kelvin(kwargs):
    if "kelvin" in kwargs:
        raise ValueError(
            "Legacy 'kelvin' service parameter is unsupported; "
            "use 'color_temp_kelvin'."
        )
    return kwargs
