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


def split_attribute_durations(step_duration, needs_brightness, needs_color):
    if needs_brightness and needs_color:
        return step_duration / 2, step_duration / 2
    return (
        step_duration if needs_brightness else 0,
        step_duration if needs_color else 0,
    )


def calculate_phase_plan(total_steps, needs_two_phases):
    if not needs_two_phases:
        return 0, total_steps
    steps_to_white = total_steps // 2
    return steps_to_white, total_steps - steps_to_white
