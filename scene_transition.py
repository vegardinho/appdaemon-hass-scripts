import hassapi as hass
import yaml
import asyncio
import traceback


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

    async def transition_steps(
        self, entity_id, current_values, target_values, transition_time, attributes, phase_name=None
    ):
        """
        Gradually transition light attributes over a specified time with constant rate of change.

        Args:
            entity_id (str): The entity ID of the light.
            current_values (dict): Current attribute values in the format returned by get_light_attributes.
            target_values (dict): Target attribute values in the same format.
            transition_time (int): Total transition time in seconds.
            attributes (list): List of attributes to transition (e.g., ["hs_color", "brightness"]).
            phase_name (str): Optional name for this transition phase (e.g., "Phase 1: To white").
        """
        try:
            if phase_name:
                self.log(f"{phase_name} - {transition_time}s transition starting")
            
            # Calculate number of full steps and remainder
            full_steps = transition_time // self.transition_step_seconds
            remainder = transition_time % self.transition_step_seconds
            
            # Filter out attributes with None values in target
            valid_attributes = [
                attr for attr in attributes 
                if target_values.get(attr) is not None
            ]
            
            # Calculate rate of change per second for each valid attribute
            rates = {}
            for attr in valid_attributes:
                if attr == "hs_color":
                    current_hue, current_sat = current_values["hs_color"]
                    target_hue, target_sat = target_values["hs_color"]
                    rates["hue"] = (target_hue - current_hue) / transition_time
                    rates["sat"] = (target_sat - current_sat) / transition_time
                else:
                    rates[attr] = (target_values[attr] - current_values[attr]) / transition_time
            
            # Track elapsed time and current position
            elapsed_time = 0
            
            for step in range(full_steps + (1 if remainder > 0 else 0)):
                # Determine step duration (full step or remainder)
                is_last_step = (step == full_steps)
                step_duration = remainder if is_last_step else self.transition_step_seconds
                elapsed_time += step_duration
                
                # Calculate target values for this step based on elapsed time
                intermediate_values = {}
                for attr in valid_attributes:
                    if attr == "hs_color":
                        current_hue, current_sat = current_values["hs_color"]
                        # Use exact target on last step to avoid rounding errors
                        if is_last_step and remainder > 0:
                            intermediate_values["hs_color"] = target_values["hs_color"]
                        else:
                            new_hue = current_hue + rates["hue"] * elapsed_time
                            new_sat = current_sat + rates["sat"] * elapsed_time
                            intermediate_values["hs_color"] = (round(new_hue), round(new_sat))
                    else:
                        # Use exact target on last step to avoid rounding errors
                        if is_last_step and remainder > 0:
                            intermediate_values[attr] = target_values[attr]
                        else:
                            intermediate_values[attr] = round(
                                current_values[attr] + rates[attr] * elapsed_time
                            )
                
                total_steps = full_steps + (1 if remainder > 0 else 0)
                step_label = f"Step {step + 1}/{total_steps}"
                if phase_name:
                    step_label = f"{phase_name} - {step_label}"
                
                self.log(f"{step_label} - Target: {intermediate_values}")

                # Prepare service data for the transition (only include valid attributes)
                service_data = {attr: intermediate_values[attr] for attr in valid_attributes}
                # Halve step duration because we transition color and brightness separately
                half_step_duration = step_duration / 2
                service_data["transition"] = half_step_duration

                # First transition brightness, then hue/color temp (bulbs can't change both simultaneously)
                trans_attr = {
                    k: v
                    for k, v in service_data.items()
                    if k not in ["hs_color", "color_temp_kelvin"]
                }
                self.log(f"{step_label} a) - Brightness sub-transition ({half_step_duration}s): {trans_attr}")
                await self.turn_on(entity_id=entity_id, **trans_attr)
                await self.sleep(half_step_duration)

                trans_attr = {
                    k: v for k, v in service_data.items() if k != "brightness"
                }
                self.log(f"{step_label} b) - Color sub-transition ({half_step_duration}s): {trans_attr}")
                await self.turn_on(entity_id=entity_id, **trans_attr)
                await self.sleep(half_step_duration)

                # Check if the transition was interrupted (if light attributes are not as expected)
                current_attributes = await self.get_light_attributes(entity_id)
                self.log(
                    f"{step_label} - Result: {current_attributes}"
                )
                
                # Only check attributes that were actually transitioned and are not None
                mismatch = False
                for attr in valid_attributes:
                    if attr == "color_temp_kelvin":
                        # Use tolerance for color temp comparison
                        if intermediate_values.get(attr) is not None and current_attributes.get(attr) is not None:
                            if abs(current_attributes[attr] - intermediate_values[attr]) > 50:
                                mismatch = True
                                break
                    elif attr != "hs_color":
                        # Direct comparison for other attributes
                        if current_attributes.get(attr) != intermediate_values[attr]:
                            mismatch = True
                            break
                
                if mismatch:
                    self.log(
                        f"Current values does not match target values for step transition. Cancelling transition. {entity_id}",
                        level="WARNING",
                    )
                    return
        except Exception as _:
            self.log(
                f"Error during transition for {entity_id}: {traceback.format_exc()}",
                level="ERROR",
            )

    async def transition_to_scene(self, entity_id, target_values, transition_seconds):
        """
        Transition a light to the target values, handling both color temperature and hue.

        Args:
            entity_id (str): The entity ID of the light.
            target_values (dict): Target attribute values.
            transition_seconds (int): Transition time in seconds.
        """
        try:
            # Get the current light attributes
            current_values = await self.get_light_attributes(entity_id)

            if (
                current_values["hs_color"] is None
                or len(current_values["hs_color"]) != 2
            ):
                raise ValueError(
                    f"Invalid hs_color for {entity_id}: {current_values['hs_color']}"
                )

            transition_time = transition_seconds

            # Determine whether to transition using color temperature or hue
            # Use color temp mode only if BOTH current and target have non-None color_temp
            if (
                target_values.get("color_temp_kelvin") is not None
                and current_values.get("color_temp_kelvin") is not None
            ):
                # Transition using color temperature
                attributes = ["color_temp_kelvin", "brightness"]
            else:
                # Transition using hue (even if current is in white mode)
                attributes = ["hs_color", "brightness"]

                # Calculate hue difference and handle threshold
                hue_diff = abs(
                    current_values["hs_color"][0] - target_values["hs_color"][0]
                )
                if hue_diff > self.hue_difference_threshold:
                    self.log(
                        f"Hue difference {hue_diff} exceeds threshold {self.hue_difference_threshold}, using 2-phase transition for {entity_id}"
                    )
                    # Split transition time in half for two-stage transition
                    first_transition_time = transition_seconds // 2
                    # Transition to white color first
                    await self.transition_steps(
                        entity_id,
                        current_values,
                        {"hs_color": (0, 0), "brightness": target_values["brightness"]},
                        first_transition_time,
                        ["hs_color", "brightness"],
                        phase_name="Phase 1: To white",
                    )
                    # Update current values and transition time for second stage
                    current_values = await self.get_light_attributes(entity_id)
                    transition_time = transition_seconds - first_transition_time

            # Determine phase name for logging
            if hue_diff > self.hue_difference_threshold:
                phase_name = "Phase 2: To target color"
            else:
                phase_name = None
            
            # Perform the final transition to the target values
            await self.transition_steps(
                entity_id,
                current_values,
                target_values,
                transition_time,
                attributes,
                phase_name=phase_name,
            )
            self.log(f"Transition completed for {entity_id}")
        except Exception as e:
            self.log(
                f"Error transitioning scene to entity {entity_id}: {traceback.format_exc()}",
                level="ERROR",
            )

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

            # Create tasks for transitioning each entity.
            # Use create_task because of Appdaemon specifics
            for entity_id, target_values in scene_configuration.items():
                self.create_task(
                    self.transition_to_scene(entity_id, target_values, transition_seconds),
                    self.log(f"Adding task for {entity_id}"),
                )

            self.log(f"Scene {scene_entity_id} applied successfully.")
        except Exception as e:
            self.log(
                f"Error applying scene {scene_entity_id}: {traceback.format_exc()}",
                level="ERROR",
            )

    async def event_apply_scene(self, event_name, data, kwargs):
        """
        Event handler for applying a scene with custom transition time.
        
        Args:
            data: Must contain 'scene_entity_id' and either 'transition_seconds' or 'transition_minutes'.
        """
        scene_entity_id = data.get("scene_entity_id")
        transition_seconds = data.get("transition_seconds")
        transition_minutes = data.get("transition_minutes")
        
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
