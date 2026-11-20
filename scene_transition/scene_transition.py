import hassapi as hass
import yaml
import asyncio
import traceback

from .scene_transition_color import (
    adapt_target_to_light,
    hs_to_kelvin,
)
from .scene_transition_lights import (
    expand_light_entities,
    find_off_members,
    normalize_light_state,
)
from .scene_transition_validation import (
    describe_target_mismatch,
    get_corrections_needed,
    get_required_validation_value,
    is_at_target as is_at_target_helper,
)
from .scene_transition_steps import (
    calculate_phase_plan,
    calculate_step_count,
    calculate_step_duration,
    interpolate,
    interpolate_hs,
    split_attribute_durations,
)
from .scene_transition_service_calls import (
    assert_no_legacy_kelvin,
    brightness_turn_on_kwargs,
    hs_turn_on_kwargs,
    kelvin_turn_on_kwargs,
)


class TransitionError(Exception):
    """Exception raised when a scene transition fails validation."""
    def __init__(self, message: str, entity_id: str = None, step: int = None):
        self.message = message
        self.entity_id = entity_id
        self.step = step
        super().__init__(message)


class SceneTransition(hass.Hass):
    async def initialize(self):
        """
        Initialize the AutoScene app.
        Set default transition time and hue difference threshold.
        """
        self.transition_step_seconds = 30  # Step duration in seconds
        self.hue_difference_threshold = (
            120  # Threshold for first transitioning to white color
        )
        self._active_scenes = {}  # {scene_entity_id: {"tasks": {entity_id: task, ...}}}
        self._lights_started_off = set()
        self._entity_scene_ids = {}
        self.log("Class object SceneTransition initialized")
        # await self.apply_scene("scene.tv_stue_formiddag", transition_seconds=35)  # Example scene transition
        # await self.apply_scene("scene.tv_stue_natt", transition_seconds=35)  # Example scene transition
        # self.log("Initial scene transition completed")
        
        # Listen for event to apply scene with transition
        self.listen_event(self.event_apply_scene, "appdaemon_scene_transition")
        self.log("Registered event listener for 'appdaemon_scene_transition'")

    def log_entity(self, entity_id, message, level="INFO", scene_entity_id=None):
        """Log a transition message with scene and entity context."""
        if scene_entity_id is None:
            scene_entity_id = getattr(self, "_entity_scene_ids", {}).get(entity_id)
        prefix = f"[{scene_entity_id}]" if scene_entity_id else ""
        self.log(f"{prefix}[{entity_id}] {message}", level=level)

    async def load_scene_configuration(self, scene_entity_id):
        """
        Load the details of a scene from the scenes.yaml file.

        Args:
            scene_entity_id (str): The scene entity ID (e.g., 'scene.tv_stue_natt').

        Returns:
            dict: A dictionary of entities and their target attributes.
        """
        self.log(f'Loading scene configuration for entity_id: "{scene_entity_id}"')
        scene_id = await self.get_state(scene_entity_id, attribute="id")
        
        if scene_id is None:
            raise ValueError(f"Scene entity '{scene_entity_id}' not found or has no id attribute. Make sure the scene exists in Home Assistant.")
        
        scene_file_path = "/homeassistant/scenes.yaml"

        with open(scene_file_path, "r") as file:
            scenes = yaml.safe_load(file)

        matching_scenes = [s for s in scenes if s.get("id") == scene_id]

        if len(matching_scenes) == 0:
            raise ValueError(f"No scene found with id '{scene_id}' in scenes.yaml")
        elif len(matching_scenes) > 1:
            raise ValueError(f"Multiple scenes found with id '{scene_id}'")

        scene_details = matching_scenes[0]
        scene_entities = {}

        for entity_id, entity in scene_details["entities"].items():
            hs_color = entity.get("hs_color")
            brightness = entity.get("brightness")
            color_temp = entity.get("color_temp_kelvin")

            # Validate that we have brightness and at least one color attribute
            if brightness is None:
                raise ValueError(
                    f"Scene with id '{scene_id}' has undefined brightness for entity '{entity_id}'"
                )
            
            if hs_color is None and color_temp is None:
                raise ValueError(
                    f"Scene with id '{scene_id}' has both hs_color and color_temp_kelvin undefined for entity '{entity_id}'"
                )

            scene_entities[entity_id] = {
                "hs_color": hs_color if hs_color is not None else [0, 0],
                "brightness": brightness,
                "color_temp_kelvin": color_temp,
            }

        return scene_entities

    async def get_scene_light_entities(self, scene_entity_id):
        """
        Get list of light entity IDs from a scene.
        
        Args:
            scene_entity_id (str): The scene entity ID.
            
        Returns:
            list: List of light entity IDs in the scene, or empty list if error.
        """
        try:
            scene_config = await self.load_scene_configuration(scene_entity_id)
            return [entity_id for entity_id in scene_config.keys() if entity_id.startswith("light.")]
        except Exception as e:
            self.log(f"Error getting scene lights for {scene_entity_id}: {e}", level="ERROR")
            return []

    async def get_light_attributes(self, entity_id):
        """
        Get the current state of a light entity.

        Args:
            entity_id (str): The entity ID of the light.

        Returns:
            dict: A dictionary containing state and light attributes.
        """
        state = await self.get_state(entity_id, attribute="all")
        return normalize_light_state(entity_id, state)

    async def get_off_group_members(self, entity_id):
        """
        Return entity IDs for off devices contained in a light group.

        Only the ``group_entities`` attribute is used as the source of group
        membership. Native Zigbee2MQTT groups that do not expose this attribute
        cannot be inspected for individual off members.
        """
        return await find_off_members(entity_id, self.get_light_attributes)

    async def validate_transition_state(self, entity_id, target_values, validation_key, error_message):
        """
        Validate that light has reached target state after transition.

        If the light reports no brightness, it may have been turned off by
        another automation or manually while this transition was running.
        Fail immediately with a descriptive error so apply_scene() can cancel
        the remaining tasks without extending the transition.
        Raises TransitionError if validation fails.
        
        Args:
            entity_id: The light entity ID
            target_values: Target attributes to validate against (dict with brightness, hs_color, color_temp_kelvin)
            validation_key: Key to check in is_at_target result ('brightness' or 'color')
            error_message: Descriptive error message to log if validation fails
            
        Raises:
            TransitionError: If validation fails
        """
        current = await self.get_light_attributes(entity_id)
        if not current["available"]:
            self.log_entity(
                entity_id,
                "Skipping transition validation for unavailable or unknown light",
                level="WARNING",
            )
            return

        if current["state"] == "off":
            if entity_id in getattr(self, "_lights_started_off", set()):
                self.log_entity(
                    entity_id,
                    "Light is starting from off; "
                    "waiting for the turn-on state to propagate.",
                    level="DEBUG",
                )
                return
            error_msg = (
                f"Light {entity_id} was turned off while the transition was running; "
                "cancelling transition."
            )
            self.log_entity(entity_id, error_msg, level="ERROR")
            raise ValueError(error_msg)

        if current["state"] == "on":
            getattr(self, "_lights_started_off", set()).discard(entity_id)

        off_members = await self.get_off_group_members(entity_id)
        if off_members:
            error_msg = (
                f"Light group {entity_id} contains off member(s) "
                f"{', '.join(off_members)} while the transition was running; "
                "cancelling transition."
            )
            self.log_entity(entity_id, error_msg, level="ERROR")
            raise ValueError(error_msg)

        required_value = get_required_validation_value(
            current,
            target_values,
            validation_key,
        )

        if required_value is None:
            error_msg = (
                f"Transition validation could not read current {validation_key} "
                f"value for {entity_id}. The light may have been "
                "turned off while the transition was running; cancelling transition."
            )
            self.log_entity(entity_id, error_msg, level="ERROR")
            raise ValueError(error_msg)

        match_status = self.is_at_target(current, target_values)
        
        if not match_status[validation_key]:
            detailed_msg = describe_target_mismatch(
                current,
                target_values,
                validation_key,
                error_message,
            )
            error_msg = f"Lights not at expected value according to transition step. {detailed_msg} for {entity_id}"
            self.log_entity(entity_id, error_msg, level="ERROR")
            raise ValueError(error_msg)

    def is_at_target(self, current_values, target_values, brightness_tolerance=2, hue_tolerance=2, sat_tolerance=2, color_temp_tolerance=50):
        """
        Check if current values match target values within tolerances.
        
        Args:
            current_values (dict): Current light attributes.
            target_values (dict): Target light attributes.
            brightness_tolerance (int): Tolerance for brightness comparison.
            hue_tolerance (int): Tolerance for hue comparison.
            sat_tolerance (int): Tolerance for saturation comparison.
            color_temp_tolerance (int): Tolerance for color temperature comparison.
            
        Returns:
            dict: {'brightness': bool, 'color': bool, 'overall': bool}
        """
        return is_at_target_helper(
            current_values,
            target_values,
            brightness_tolerance,
            hue_tolerance,
            sat_tolerance,
            color_temp_tolerance,
        )

    async def verify_and_correct_target(self, entity_id, target_values):
        """
        Verify that light has reached target values and apply corrections if needed.
        
        Args:
            entity_id (str): The entity ID of the light.
            target_values (dict): Target attribute values.
        """
        final_values = await self.get_light_attributes(entity_id)
        if not final_values["available"]:
            self.log_entity(
                entity_id,
                "Skipping final verification for unavailable or unknown light",
                level="WARNING",
            )
            return
        if final_values["state"] == "off":
            started_off = getattr(self, "_lights_started_off", set())
            if entity_id in started_off:
                self.log_entity(
                    entity_id,
                    "Light started off and is still off during "
                    "final verification; skipping correction.",
                    level="WARNING",
                )
                started_off.discard(entity_id)
                return
            raise ValueError(
                f"Light {entity_id} was turned off after or during the transition; "
                "cancelling transition."
            )
        off_members = await self.get_off_group_members(entity_id)
        if off_members:
            raise ValueError(
                f"Light group {entity_id} contains off member(s) "
                f"{', '.join(off_members)} after or during the transition; "
                "cancelling transition."
            )

        match_status = self.is_at_target(final_values, target_values)
        corrections_needed = get_corrections_needed(final_values, target_values)
        use_color_temp = target_values.get("color_temp_kelvin") is not None
        
        if not match_status['brightness']:
            self.log_entity(entity_id, f"Brightness mismatch: {final_values['brightness']} vs target {target_values['brightness']}")
        
        if not match_status['color']:
            if use_color_temp:
                self.log_entity(entity_id, f"Color temp mismatch: {final_values.get('color_temp_kelvin')} vs target {target_values['color_temp_kelvin']}")
            else:
                self.log_entity(entity_id, f"HS color mismatch: {final_values['hs_color']} vs target {target_values['hs_color']}")
        
        # Apply corrections sequentially if needed
        if corrections_needed:
            self.log_entity(entity_id, f"Applying corrections: {corrections_needed}")
            
            if "brightness" in corrections_needed:
                kwargs = assert_no_legacy_kelvin(
                    brightness_turn_on_kwargs(target_values["brightness"], 1)
                )
                await self.turn_on(entity_id=entity_id, **kwargs)
                await self.sleep(1)
            
            if "color_temp" in corrections_needed:
                kwargs = assert_no_legacy_kelvin(
                    kelvin_turn_on_kwargs(target_values["color_temp_kelvin"], 1)
                )
                await self.turn_on(entity_id=entity_id, **kwargs)
                await self.sleep(1)
            
            if "hs_color" in corrections_needed:
                kwargs = assert_no_legacy_kelvin(
                    hs_turn_on_kwargs(target_values["hs_color"], 1)
                )
                await self.turn_on(entity_id=entity_id, **kwargs)
                await self.sleep(1)

    async def transition_to_scene(self, entity_id, target_values, transition_seconds, scene_entity_id):
        """
        Transition a light to the target values with brightness and color adjustments.

        Args:
            entity_id (str): The entity ID of the light.
            target_values (dict): Target attribute values.
            transition_seconds (int): Transition time in seconds.
            scene_entity_id (str): The scene entity ID (for error tracking).
        """
        getattr(self, "_entity_scene_ids", {})[entity_id] = scene_entity_id
        try:
            # Get current state
            current_values = await self.get_light_attributes(entity_id)
            if not current_values["available"]:
                self.log_entity(
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
                # Start from neutral color and zero brightness. This ensures an
                # HS transition sends an explicit color command rather than
                # treating the target color as already reached.
                self.log_entity(entity_id, "Light is currently off, will transition from off state")
                current_values["hs_color"] = [0, 0]
                current_values["brightness"] = 0
                getattr(self, "_lights_started_off", set()).add(entity_id)
            elif current_values["hs_color"] is not None and len(current_values["hs_color"]) != 2:
                raise ValueError(f"Invalid hs_color for {entity_id}: {current_values['hs_color']}")

            # Check if already at target
            match_status = self.is_at_target(current_values, target_values)
            
            if match_status['overall']:
                self.log_entity(entity_id, "Already at target, skipping transition")
                return
            
            # Determine what needs adjusting
            needs_brightness = not match_status['brightness']
            needs_color = not match_status['color']
            
            # Log current and target values
            if current_values.get("color_temp_kelvin") is not None:
                self.log_entity(entity_id, f"Current: brightness={current_values['brightness']}, color_temp={current_values['color_temp_kelvin']}K")
            else:
                self.log_entity(entity_id, f"Current: brightness={current_values['brightness']}, hs_color={current_values['hs_color']}")
            
            use_color_temp_for_target = target_values.get("color_temp_kelvin") is not None
            if use_color_temp_for_target:
                self.log_entity(entity_id, f"Target: brightness={target_values['brightness']}, color_temp={target_values['color_temp_kelvin']}K")
            else:
                self.log_entity(entity_id, f"Target: brightness={target_values['brightness']}, hs_color={target_values['hs_color']}")
            
            if needs_brightness and needs_color:
                self.log_entity(entity_id, "Both brightness and color need adjustment")
            elif needs_brightness:
                self.log_entity(entity_id, "Only brightness needs adjustment")
            elif needs_color:
                self.log_entity(entity_id, "Only color needs adjustment")

            # Calculate steps
            # Each step takes transition_step_seconds regardless of what's being adjusted
            total_steps, remainder = calculate_step_count(
                transition_seconds,
                self.transition_step_seconds,
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
                if not use_color_temp and target_values.get("hs_color") is not None:
                    hue_diff = abs(current_values["hs_color"][0] - target_values["hs_color"][0])
                    if hue_diff > self.hue_difference_threshold:
                        needs_two_phases = True
                        self.log_entity(entity_id, f"Hue diff {hue_diff} > {self.hue_difference_threshold}, using 2-phase transition")
                
                # Allocate steps for color transition
                steps_to_white, steps_to_target = calculate_phase_plan(
                    total_steps,
                    needs_two_phases,
                )
            
            self.log_entity(
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
                else:
                    hue_start, sat_start = current_values["hs_color"]
                    hue_end, sat_end = target_values["hs_color"]
                    white_hue = 0
                    white_sat = 0
            
            # Execute transition - brightness first, then color in each step
            for step in range(total_steps):
                await self.transition_step(
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
                    scene_entity_id=scene_entity_id
                )
            
            # Verify and correct final state
            await self.verify_and_correct_target(entity_id, target_values)
            
            self.log_entity(entity_id, "Transition completed")
            getattr(self, "_entity_scene_ids", {}).pop(entity_id, None)
        except asyncio.CancelledError:
            self.log_entity(entity_id, "Transition cancelled", level="DEBUG")
            getattr(self, "_entity_scene_ids", {}).pop(entity_id, None)
            return
        except ValueError as e:
            getattr(self, "_entity_scene_ids", {}).pop(entity_id, None)
            # Wrap validation errors as TransitionError
            raise TransitionError(str(e), entity_id=entity_id) from e
        except Exception as e:
            self.log_entity(entity_id, f"Error in transition: {traceback.format_exc()}", level="ERROR")
            getattr(self, "_entity_scene_ids", {}).pop(entity_id, None)
            raise
    
    async def transition_step(self, step, total_steps, remainder, needs_brightness, needs_color, brightness_start, brightness_end, hue_start, sat_start, hue_end, sat_end, white_hue, white_sat, use_color_temp, color_temp_start, color_temp_end, entity_id, steps_to_white, steps_to_target, needs_two_phases, scene_entity_id):
        step_num = step + 1
        step_duration = calculate_step_duration(
            step_num,
            total_steps,
            remainder,
            self.transition_step_seconds,
        )
        is_last_step = (step_num == total_steps)
        
        # Log step header once at the beginning
        self.log_entity(entity_id, f"Step {step_num}/{total_steps}", scene_entity_id=scene_entity_id)

        
        # Determine transition durations based on what needs adjusting
        brightness_duration, color_duration = split_attribute_durations(
            step_duration,
            needs_brightness,
            needs_color,
        )
        
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
            should_subtract = brightness_duration >= self.transition_step_seconds and not is_last_transition
            brightness_transition = brightness_duration - 1 if should_subtract else brightness_duration
            
            kwargs = assert_no_legacy_kelvin(
                brightness_turn_on_kwargs(new_brightness, brightness_transition)
            )
            await self.turn_on(entity_id=entity_id, **kwargs)
            # Log brightness
            self.log_entity(entity_id, f"a) Brightness target: {new_brightness}", scene_entity_id=scene_entity_id)
            # Log color if not adjusting it (static color)
            if not needs_color:
                if use_color_temp and color_temp_end is not None:
                    self.log_entity(entity_id, f"b) Color temp target: {color_temp_end}K", scene_entity_id=scene_entity_id)
                elif not use_color_temp and hue_end is not None and sat_end is not None:
                    self.log_entity(entity_id, f"b) HS Color target: ({hue_end}, {sat_end})", scene_entity_id=scene_entity_id)
            
            await self.sleep(brightness_duration)
        
            await self.validate_transition_state(
                entity_id=entity_id,
                target_values={"brightness": new_brightness, "hs_color": [0, 0], "color_temp_kelvin": None},
                validation_key="brightness",
                error_message=f"Brightness validation FAILED for {entity_id}: expected {new_brightness}, got {new_brightness}"
            )
        
        # Apply color second if needed
        if needs_color:
            # Determine if we should subtract 1 to avoid overlap
            # Only for long transitions and not the last transition overall
            should_subtract = color_duration >= self.transition_step_seconds and not is_last_step
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
                
                kwargs = assert_no_legacy_kelvin(
                    hs_turn_on_kwargs([new_hue, new_sat], color_transition)
                )
                await self.turn_on(entity_id=entity_id, **kwargs)
                self.log_entity(entity_id, f"b) HS Color: ({new_hue}, {new_sat})", scene_entity_id=scene_entity_id)
                await self.sleep(color_duration)
                
                await self.validate_transition_state(
                    entity_id=entity_id,
                    target_values={"brightness": 0, "hs_color": [new_hue, new_sat], "color_temp_kelvin": None},
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
                    kwargs = assert_no_legacy_kelvin(
                        kelvin_turn_on_kwargs(new_color_temp, color_transition)
                    )
                    await self.turn_on(entity_id=entity_id, **kwargs)
                    self.log_entity(entity_id, f"b) Color temp target: {new_color_temp}K", scene_entity_id=scene_entity_id)
                    await self.sleep(color_duration)
                    
                    await self.validate_transition_state(
                        entity_id=entity_id,
                        target_values={"brightness": 0, "hs_color": [0, 0], "color_temp_kelvin": new_color_temp},
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
                    kwargs = assert_no_legacy_kelvin(
                        hs_turn_on_kwargs([new_hue, new_sat], color_transition)
                    )
                    await self.turn_on(entity_id=entity_id, **kwargs)
                    self.log_entity(entity_id, f"b) HS Color target: ({new_hue}, {new_sat})", scene_entity_id=scene_entity_id)
                    await self.sleep(color_duration)
                    
                    await self.validate_transition_state(
                        entity_id=entity_id,
                        target_values={"brightness": 0, "hs_color": [new_hue, new_sat], "color_temp_kelvin": None},
                        validation_key="color",
                        error_message=f"Color validation FAILED for {entity_id} (target phase): expected ({new_hue}, {new_sat}), got ({new_hue}, {new_sat})"
                    )


    async def _cancel_scene_tasks(self, tasks_dict):
        """
        Cancel all tasks in a scene.
        
        Args:
            tasks_dict (dict): Dictionary mapping entity_id to task
        """
        for entity_id, task in tasks_dict.items():
            if not task.done():
                task.cancel()
        
        # Wait for all cancellations to complete
        for task in tasks_dict.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.log(
                    f"Task already failed during scene cancellation: {e}",
                    level="DEBUG",
                )

    async def expand_scene_configuration(self, scene_configuration):
        """
        Expand group entities into available individual light entities.

        Individual light entities are preserved as-is. Group entities are
        never used as transition targets and are replaced by their available
        individual members.
        """
        expanded_configuration = {}

        for entity_id, target_values in scene_configuration.items():
            attributes = await self.get_light_attributes(entity_id)
            member_ids = attributes["member_entity_ids"]

            if not member_ids:
                if attributes["available"]:
                    expanded_configuration.setdefault(entity_id, target_values)
                continue

            members = await expand_light_entities(
                entity_id,
                self.get_light_attributes,
            )
            for member_id in members:
                expanded_configuration.setdefault(member_id, target_values)

        return expanded_configuration

    async def apply_scene(self, scene_entity_id, transition_seconds):
        """
        Apply a scene by transitioning all entities to their target states concurrently.

        Args:
            scene_entity_id (str): The scene entity ID.
            transition_seconds (int): Transition time in seconds.
        """
        try:
            # Load the scene configuration
            scene_configuration = await self.load_scene_configuration(scene_entity_id)
            scene_configuration = await self.expand_scene_configuration(
                scene_configuration
            )
            lights_in_scene = set(scene_configuration.keys())
            
            # Find and cancel any OTHER scenes that share lights with this one
            for other_scene_id, scene_data in list(self._active_scenes.items()):
                if other_scene_id != scene_entity_id:
                    lights_in_other_scene = set(scene_data["tasks"].keys())
                    if lights_in_scene & lights_in_other_scene:  # Intersection found
                        self.log(f"Cancelling scene {other_scene_id} to start {scene_entity_id} (shared lights)")
                        await self._cancel_scene_tasks(scene_data["tasks"])
                        del self._active_scenes[other_scene_id]
            
            # Create tasks for transitioning each entity
            tasks = {}
            for entity_id, target_values in scene_configuration.items():
                task = self.create_task(
                    self.transition_to_scene(entity_id, target_values, transition_seconds, scene_entity_id)
                )
                tasks[entity_id] = task
            
            # Store the scene and its tasks
            self._active_scenes[scene_entity_id] = {"tasks": tasks}
            
            # Wait for tasks to complete, handling exceptions in real-time
            for completed_task in asyncio.as_completed(tasks.values()):
                try:
                    await completed_task
                except asyncio.CancelledError:
                    # This task was cancelled by us, expected
                    self.log(f"Task {completed_task} was cancelled", level="DEBUG")
                    pass
                # BEWARE: Appdaemon (possibly Asyncio) will log this as if not caught, because of how it handles task exceptions. We catch it to 
                # prevent our app from crashing, but it may still log an error in the Appdaemon logs. This is likely a quirk of handling 
                # cancellations in Appdaemon. Possible workaround is to return a dict with success/failure status instead of raising exceptions.
                except Exception as e:
                    # Task failed - cancel all remaining tasks immediately
                    self.log(
                        f"Scene transition for {scene_entity_id} failed in task "
                        f"{completed_task}: {e}. Cancelling remaining transition tasks.",
                        level="ERROR"
                    )
                    await self._cancel_scene_tasks(tasks)
                    return
            
            self.log(f"Scene {scene_entity_id} applied successfully.")
        finally:
            # Clean up the scene record
            if scene_entity_id in self._active_scenes:
                del self._active_scenes[scene_entity_id]

    async def event_apply_scene(self, event_name, data, kwargs):
        """
        Apply a scene after receiving an AppDaemon transition event.

        The event can originate from Home Assistant's calendar-start
        automation, a manual calendar toggle, or a manual calendar cycle.
        ``source`` and ``calendar_entity_id`` are optional context fields used
        only to make the trigger visible in the log.
        
        Args:
            data: Must contain 'scene_entity_id' and either 'transition_seconds' or 'transition_minutes'.
        """
        scene_entity_id = data.get("scene_entity_id")
        transition_seconds = data.get("transition_seconds")
        transition_minutes = data.get("transition_minutes")
        only_if_lights_on = data.get("only_if_lights_on", False)
        source = data.get("source", "unknown")
        calendar_entity_id = data.get("calendar_entity_id")
        requested_duration = (
            f"{transition_minutes}m"
            if transition_minutes is not None
            else f"{transition_seconds}s"
        )

        self.log(
            "Received appdaemon_scene_transition trigger: "
            f"source={source}, scene={scene_entity_id}, "
            f"calendar={calendar_entity_id or 'none'}, "
            f"requested_duration={requested_duration}"
        )

        if only_if_lights_on:
            # Check if lights on 
            scene_configuration = await self.load_scene_configuration(scene_entity_id)
            any_light_on = False
            for entity_id in scene_configuration.keys():
                state = await self.get_state(entity_id)
                if state == "on":
                    any_light_on = True
                    break
            if not any_light_on:
                self.log(f"Skipping scene {scene_entity_id} application because all lights are off.")
                return
        
        # Validate scene_entity_id
        if not scene_entity_id:
            self.log("No scene_entity_id provided to appdaemon_scene_transition event", level="ERROR")
            return
        
        # Validate and calculate transition time
        if transition_seconds is None and transition_minutes is None:
            self.log(
                "Either transition_seconds or transition_minutes must be provided to appdaemon_scene_transition event",
                level="ERROR"
            )
            return
        
        if transition_seconds is not None and transition_minutes is not None:
            self.log(
                "Cannot provide both transition_seconds and transition_minutes. Please provide only one.",
                level="ERROR"
            )
            return
        
        # Calculate transition time in seconds
        if transition_minutes is not None:
            transition_seconds = int(transition_minutes * 60)
        else:
            transition_seconds = int(transition_seconds)
        
        self.log(
            f"Applying scene {scene_entity_id} with transition time of {transition_seconds} seconds"
        )
        await self.apply_scene(scene_entity_id, transition_seconds)


