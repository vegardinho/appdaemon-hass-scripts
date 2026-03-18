import hassapi as hass
import yaml
import asyncio
import traceback


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
        Trigger an initial scene transition for testing.
        """
        self.transition_step_seconds = 30  # Step duration in seconds
        self.hue_difference_threshold = (
            120  # Threshold for first transitioning to white color
        )
        self._active_scenes = {}  # {scene_entity_id: {"tasks": {entity_id: task, ...}}}
        self.log("SceneTransition initialized")
        # await self.apply_scene("scene.tv_stue_formiddag", transition_seconds=35)  # Example scene transition
        # await self.apply_scene("scene.tv_stue_natt", transition_seconds=35)  # Example scene transition
        self.log("Initial scene transition completed")
        
        # Listen for event to apply scene with transition
        self.listen_event(self.event_apply_scene, "appdaemon_scene_transition")
        self.log("Registered event listener for 'appdaemon_scene_transition'")

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
            dict: A dictionary containing light attributes (e.g., hs_color, brightness, color_temp_kelvin).
        """
        state = await self.get_state(entity_id, attribute="all")
        attributes = state.get("attributes", {})

        hs_color = attributes.get("hs_color")
        brightness = attributes.get("brightness")
        color_temp_kelvin = attributes.get("color_temp_kelvin")

        if hs_color is not None and len(hs_color) != 2:
            raise ValueError(f"Invalid hs_color format for {entity_id}: {hs_color}")

        return {
            "hs_color": hs_color,
            "brightness": brightness,
            "color_temp_kelvin": color_temp_kelvin,
        }

    async def validate_transition_state(self, entity_id, target_values, validation_key, error_message):
        """
        Validate that light has reached target state after transition.
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
        match_status = self.is_at_target(current, target_values)
        
        if not match_status[validation_key]:
            # Build detailed error message with actual vs expected values
            if validation_key == "brightness":
                actual_brightness = current.get("brightness")
                expected_brightness = target_values.get("brightness")
                detailed_msg = f"Brightness mismatch: expected {expected_brightness}, got {actual_brightness}"
            elif validation_key == "color":
                if target_values.get("color_temp_kelvin") is not None:
                    actual_temp = current.get("color_temp_kelvin")
                    expected_temp = target_values.get("color_temp_kelvin")
                    detailed_msg = f"Color temp mismatch: expected {expected_temp}K, got {actual_temp}K"
                else:
                    actual_color = current.get("hs_color")
                    expected_color = target_values.get("hs_color")
                    detailed_msg = f"HS Color mismatch: expected {expected_color}, got {actual_color}"
            else:
                detailed_msg = error_message
            
            error_msg = f"Lights not at expected value according to transition step. {detailed_msg} for {entity_id}"
            self.log(error_msg, level="ERROR")
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
        brightness_match = abs(current_values["brightness"] - target_values["brightness"]) <= brightness_tolerance
        
        use_color_temp = target_values.get("color_temp_kelvin") is not None
        
        if use_color_temp:
            color_match = (
                current_values.get("color_temp_kelvin") is not None and
                abs(current_values["color_temp_kelvin"] - target_values["color_temp_kelvin"]) <= color_temp_tolerance
            )
        else:
            hue_match = abs(current_values["hs_color"][0] - target_values["hs_color"][0]) <= hue_tolerance
            sat_match = abs(current_values["hs_color"][1] - target_values["hs_color"][1]) <= sat_tolerance
            color_match = hue_match and sat_match
        
        return {
            'brightness': brightness_match,
            'color': color_match,
            'overall': brightness_match and color_match
        }

    async def verify_and_correct_target(self, entity_id, target_values):
        """
        Verify that light has reached target values and apply corrections if needed.
        
        Args:
            entity_id (str): The entity ID of the light.
            target_values (dict): Target attribute values.
        """
        final_values = await self.get_light_attributes(entity_id)
        match_status = self.is_at_target(final_values, target_values)
        
        corrections_needed = []
        use_color_temp = target_values.get("color_temp_kelvin") is not None
        
        if not match_status['brightness']:
            self.log(f"Brightness mismatch: {final_values['brightness']} vs target {target_values['brightness']}")
            corrections_needed.append("brightness")
        
        if not match_status['color']:
            if use_color_temp:
                self.log(f"Color temp mismatch: {final_values.get('color_temp_kelvin')} vs target {target_values['color_temp_kelvin']}")
                corrections_needed.append("color_temp")
            else:
                self.log(f"HS color mismatch: {final_values['hs_color']} vs target {target_values['hs_color']}")
                corrections_needed.append("hs_color")
        
        # Apply corrections sequentially if needed
        if corrections_needed:
            self.log(f"Applying corrections for {entity_id}: {corrections_needed}")
            
            if "brightness" in corrections_needed:
                await self.turn_on(entity_id=entity_id, brightness=target_values["brightness"], transition=1)
                await self.sleep(1)
            
            if "color_temp" in corrections_needed:
                await self.turn_on(entity_id=entity_id, color_temp_kelvin=target_values["color_temp_kelvin"], transition=1)
                await self.sleep(1)
            
            if "hs_color" in corrections_needed:
                await self.turn_on(entity_id=entity_id, hs_color=target_values["hs_color"], transition=1)
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
        try:
            # Get current state
            current_values = await self.get_light_attributes(entity_id)
            
            # If light is off, current hs_color will be None - that's expected
            # We'll still transition it to the target scene values
            if current_values["hs_color"] is None:
                # Light is off - use target values as the baseline for "current" color
                # This allows us to transition brightness from 0 to target
                self.log(f"Light {entity_id} is currently off, will transition from off state")
                current_values["hs_color"] = target_values.get("hs_color", [0, 0])
                current_values["brightness"] = 0
            elif len(current_values["hs_color"]) != 2:
                raise ValueError(f"Invalid hs_color for {entity_id}: {current_values['hs_color']}")

            # Check if already at target
            match_status = self.is_at_target(current_values, target_values)
            
            if match_status['overall']:
                self.log(f"Light {entity_id} already at target, skipping transition")
                return
            
            # Determine what needs adjusting
            needs_brightness = not match_status['brightness']
            needs_color = not match_status['color']
            
            # Log current and target values
            if current_values.get("color_temp_kelvin") is not None:
                self.log(f"Current for {entity_id}: brightness={current_values['brightness']}, color_temp={current_values['color_temp_kelvin']}K")
            else:
                self.log(f"Current for {entity_id}: brightness={current_values['brightness']}, hs_color={current_values['hs_color']}")
            
            use_color_temp_for_target = target_values.get("color_temp_kelvin") is not None
            if use_color_temp_for_target:
                self.log(f"Target for {entity_id}: brightness={target_values['brightness']}, color_temp={target_values['color_temp_kelvin']}K")
            else:
                self.log(f"Target for {entity_id}: brightness={target_values['brightness']}, hs_color={target_values['hs_color']}")
            
            if needs_brightness and needs_color:
                self.log(f"Both brightness and color need adjustment for {entity_id}")
            elif needs_brightness:
                self.log(f"Only brightness needs adjustment for {entity_id}")
            elif needs_color:
                self.log(f"Only color needs adjustment for {entity_id}")

            # Calculate steps
            # Each step takes transition_step_seconds regardless of what's being adjusted
            total_steps = transition_seconds // self.transition_step_seconds
            remainder = transition_seconds % self.transition_step_seconds
            if remainder > 0:
                total_steps += 1
            
            # Determine transition mode (only if color needs adjusting)
            use_color_temp = False
            needs_two_phases = False
            steps_to_white = 0
            steps_to_target = total_steps
            
            if needs_color:
                # Use color_temp mode if target specifies it
                use_color_temp = target_values.get("color_temp_kelvin") is not None
                
                # Check if we need two-phase transition (via white) - only for hs_color mode
                if not use_color_temp:
                    hue_diff = abs(current_values["hs_color"][0] - target_values["hs_color"][0])
                    if hue_diff > self.hue_difference_threshold:
                        needs_two_phases = True
                        self.log(f"Hue diff {hue_diff} > {self.hue_difference_threshold}, using 2-phase transition")
                
                # Allocate steps for color transition
                if needs_two_phases:
                    # Split steps evenly between white and target phases
                    steps_to_white = total_steps // 2
                    steps_to_target = total_steps - steps_to_white
            
            self.log(
                f"Plan: {total_steps} steps ({steps_to_white} to-white, {steps_to_target} to-target)"
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
            
            self.log(f"Transition completed for {entity_id}")
        except asyncio.CancelledError:
            self.log(f"Transition cancelled for {entity_id}", level="DEBUG")
            return
        except ValueError as e:
            # Wrap validation errors as TransitionError
            raise TransitionError(str(e), entity_id=entity_id) from e
        except Exception as e:
            self.log(f"Error in transition: {traceback.format_exc()}", level="ERROR")
            raise
    
    async def transition_step(self, step, total_steps, remainder, needs_brightness, needs_color, brightness_start, brightness_end, hue_start, sat_start, hue_end, sat_end, white_hue, white_sat, use_color_temp, color_temp_start, color_temp_end, entity_id, steps_to_white, steps_to_target, needs_two_phases, scene_entity_id):
        step_num = step + 1
        step_duration = remainder if step_num == total_steps and remainder > 0 else self.transition_step_seconds
        is_last_step = (step_num == total_steps)
        
        # Log step header once at the beginning
        self.log(f"Step {step_num}/{total_steps}")

        
        # Determine transition durations based on what needs adjusting
        if needs_brightness and needs_color:
            # Split step time in half
            brightness_duration = step_duration / 2
            color_duration = step_duration / 2
        else:
            # Single attribute gets full step time
            brightness_duration = step_duration if needs_brightness else 0
            color_duration = step_duration if needs_color else 0
        
        # Apply brightness first if needed
        if needs_brightness:
            brightness_progress = step_num / total_steps
            new_brightness = round(brightness_start + (brightness_end - brightness_start) * brightness_progress)
            
            # Subtract 1 from transition to avoid overlap, but only if duration is long enough
            # and not the last transition overall
            is_last_transition = is_last_step and not needs_color
            should_subtract = brightness_duration >= self.transition_step_seconds and not is_last_transition
            brightness_transition = brightness_duration - 1 if should_subtract else brightness_duration
            
            await self.turn_on(entity_id=entity_id, brightness=new_brightness, transition=brightness_transition)
            # Log brightness
            self.log(f"a) Brightness target: {new_brightness}")
            # Log color if not adjusting it (static color)
            if not needs_color:
                if use_color_temp and color_temp_end is not None:
                    self.log(f"b) Color temp target: {color_temp_end}K")
                elif not use_color_temp and hue_end is not None and sat_end is not None:
                    self.log(f"b) HS Color target: ({hue_end}, {sat_end})")
            
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
                new_hue = round(hue_start + (white_hue - hue_start) * progress)
                new_sat = round(sat_start + (white_sat - sat_start) * progress)
                
                await self.turn_on(entity_id=entity_id, hs_color=(new_hue, new_sat), transition=color_transition)
                self.log(f"b) HS Color: ({new_hue}, {new_sat})")
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
                    new_color_temp = round(color_temp_start + (color_temp_end - color_temp_start) * progress)
                    await self.turn_on(entity_id=entity_id, color_temp_kelvin=new_color_temp, transition=color_transition)
                    self.log(f"b) Color temp target: {new_color_temp}K")
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
                    progress = (target_step + 1) / steps_to_target
                    new_hue = round(transition_hue_start + (hue_end - transition_hue_start) * progress)
                    new_sat = round(transition_sat_start + (sat_end - transition_sat_start) * progress)
                    await self.turn_on(entity_id=entity_id, hs_color=(new_hue, new_sat), transition=color_transition)
                    self.log(f"b) HS Color target: ({new_hue}, {new_sat})")
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
                    self.log(f"Task {completed_task} was cancelled", level=DEBUG)
                    pass
                # BEWARE: Appdaemon (possibly Asyncio) will log this as if not caught, because of how it handles task exceptions. We catch it to 
                # prevent our app from crashing, but it may still log an error in the Appdaemon logs. This is likely a quirk of handling 
                # cancellations in Appdaemon. Possible workaround is to return a dict with success/failure status instead of raising exceptions.
                except Exception as e:
                    # Task failed - cancel all remaining tasks immediately
                    self.log("Something went wrong with scene transition. Cancelling transitions (tasks) for entities in scene.", level="DEBUG")
                    await self._cancel_scene_tasks(tasks)
                    return
            
            self.log(f"Scene {scene_entity_id} applied successfully.")
        finally:
            # Clean up the scene record
            if scene_entity_id in self._active_scenes:
                del self._active_scenes[scene_entity_id]

    async def event_apply_scene(self, event_name, data, kwargs):
        """
        Event handler for applying a scene with custom transition time.
        
        Args:
            data: Must contain 'scene_entity_id' and either 'transition_seconds' or 'transition_minutes'.
        """
        scene_entity_id = data.get("scene_entity_id")
        transition_seconds = data.get("transition_seconds")
        transition_minutes = data.get("transition_minutes")
        only_if_lights_on = data.get("only_if_lights_on", False)

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


def hs_to_kelvin(hue, sat):
    """
    Dynamically convert hs_color (hue, saturation) to color temperature (kelvin).

    Args:
        hue (float): Hue value (0-360 degrees).
        sat (float): Saturation value (0-100%).

    Returns:
        int: Approximate color temperature in kelvin.
    """
    # If saturation is very low, assume it's white light
    if sat < 10:
        return 4000  # Neutral white as a default

    # Dynamically map hue (20-300) to color temperature (2000K-6500K)
    if 20 <= hue <= 300:
        kelvin = int(2000 + (hue - 20) * (6500 - 2000) / (300 - 20))
        return kelvin

    # For hues outside the range, return default warm light
    return 2000 if hue < 20 else 6500
