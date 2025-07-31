import hassapi as hass
import yaml
import asyncio
import traceback


class AutoScene(hass.Hass):
    async def initialize(self):
        """
        Initialize the AutoScene app.
        Set default transition time and hue difference threshold.
        Trigger an initial scene transition for testing.
        """
        self.transition_seconds = 66  # Default transition time in seconds
        self.transition_step_seconds = 29  # Step duration in seconds
        self.hue_difference_threshold = (
            120  # Threshold for first transitioning to white color
        )
        self.log("AutoScene initialized")
        await self.apply_scene("scene.stue_formiddag")  # Example scene transition
        self.log("Initial scene transition completed")

    async def load_scene_configuration(self, scene):
        """
        Load the details of a scene from the scenes.yaml file.

        Args:
            scene (str): The scene entity ID.

        Returns:
            dict: A dictionary of entities and their target attributes.
        """
        self.log(f'Loading details for scene: "{scene}"')
        name = await self.get_state(scene, attribute="friendly_name")
        scene_file_path = "/homeassistant/scenes.yaml"

        with open(scene_file_path, "r") as file:
            scenes = yaml.safe_load(file)

        matching_scenes = [s for s in scenes if s["name"] == name]

        if len(matching_scenes) == 0:
            raise ValueError(f"No scene found with name '{name}'")
        elif len(matching_scenes) > 1:
            raise ValueError(f"Multiple scenes found with name '{name}'")

        scene_details = matching_scenes[0]
        scene_entities = {}

        for entity_id, entity in scene_details["entities"].items():
            hs_color = entity.get("hs_color")
            brightness = entity.get("brightness")
            color_temp = entity.get("color_temp_kelvin")

            if hs_color is None or brightness is None or color_temp is None:
                raise ValueError(
                    f"Scene '{name}' has undefined hs_color, brightness, or color_temp values for entity '{entity_id}'"
                )

            scene_entities[entity_id] = {
                "hs_color": hs_color,
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
        self, entity_id, current_values, target_values, transition_time, attributes
    ):
        """
        Gradually transition light attributes over a specified time.

        Args:
            entity_id (str): The entity ID of the light.
            current_values (dict): Current attribute values in the format returned by get_light_attributes.
            target_values (dict): Target attribute values in the same format.
            transition_time (int): Total transition time in seconds.
            attributes (list): List of attributes to transition (e.g., ["hs_color", "brightness"]).
        """
        try:
            steps = (
                transition_time // self.transition_step_seconds
            ) + 1  # Allow for remainder. No impact if no remainder.
            transition_step = (
                self.transition_step_seconds / 2
            )  # Halve because brightness and hue separate

            for step in range(steps):
                # Calculate intermediate values for each step
                intermediate_values = {}
                for attr in attributes:
                    if attr == "hs_color":
                        # Handle hs_color as a tuple
                        current_hue, current_sat = current_values["hs_color"]
                        target_hue, target_sat = target_values["hs_color"]
                        intermediate_values["hs_color"] = (
                            round(
                                current_hue
                                + (target_hue - current_hue) * (step + 1) / steps
                            ),
                            round(
                                current_sat
                                + (target_sat - current_sat) * (step + 1) / steps
                            ),
                        )
                    else:
                        # Handle other attributes normally
                        intermediate_values[attr] = round(
                            current_values[attr]
                            + (target_values[attr] - current_values[attr])
                            * (step + 1)
                            / steps
                        )
                self.log(f"Step target values: {intermediate_values}")

                # Prepare service data for the transition
                service_data = {attr: intermediate_values[attr] for attr in attributes}
                service_data["transition"] = transition_step

                # First transition hue, then brightness (bulbs can't dim both simultaneously)
                trans_attr = {
                    k: v for k, v in service_data.items() if k != "brightness"
                }
                self.log(f"Starting color transition: {trans_attr}")
                await self.turn_on(entity_id=entity_id, **trans_attr)
                await self.sleep(transition_step)

                trans_attr = {
                    k: v
                    for k, v in service_data.items()
                    if k not in ["hs_color", "color_temp_kelvin"]
                }
                self.log(f"Starting brightness transition: {trans_attr}")
                await self.turn_on(entity_id=entity_id, **trans_attr)
                await self.sleep(transition_step)

                # Check if the transition was interrupted (if light attributes are not as expected)
                current_attributes = await self.get_light_attributes(entity_id)
                self.log(
                    f"Step {step + 1}/{steps} for {entity_id}: {current_attributes}"
                )
                if (
                    any(
                        current_attributes.get(attr) != intermediate_values[attr]
                        for attr in attributes
                        if attr != "color_temp_kelvin"
                    )
                    or abs(
                        current_attributes["color_temp_kelvin"]
                        - intermediate_values["color_temp_kelvin"]
                    )
                    > 5
                ):  # Allow for some tolerance (bulbs are not precise)
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

    async def transition_to_scene(self, entity_id, target_values):
        """
        Transition a light to the target values, handling both color temperature and hue.

        Args:
            entity_id (str): The entity ID of the light.
            target_values (dict): Target attribute values.
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

            transition_time = self.transition_seconds

            # Determine whether to transition using color temperature or hue
            if (
                "color_temp_kelvin" in target_values
                and current_values["color_temp_kelvin"] is not None
            ):
                # Transition using color temperature
                attributes = ["color_temp_kelvin", "brightness"]
            else:
                # Transition using hue
                attributes = ["hs_color", "brightness"]

                # Calculate hue difference and handle threshold
                hue_diff = abs(
                    current_values["hs_color"][0] - target_values["hs_color"][0]
                )
                if hue_diff > self.hue_difference_threshold:
                    self.log(
                        f"Hue difference {hue_diff} exceeds threshold {self.hue_difference_threshold}, transitioning to white color first for {entity_id}"
                    )
                    transition_time = self.transition_seconds // 2
                    # Transition to white color first
                    await self.transition_steps(
                        entity_id,
                        current_values,
                        {"hs_color": (0, 0), "brightness": target_values["brightness"]},
                        transition_time,
                        ["hs_color", "brightness"],
                    )

            self.log(
                f"Transitioning {entity_id} from current values {current_values} to target values: {target_values}"
            )
            # Perform the final transition to the target values
            await self.transition_steps(
                entity_id,
                current_values,
                target_values,
                transition_time,
                attributes,
            )
        except Exception as e:
            self.log(
                f"Error transitioning scene to entity {entity_id}: {traceback.format_exc()}",
                level="ERROR",
            )

    async def apply_scene(self, scene_id):
        """
        Apply a scene by transitioning all entities to their target states concurrently.

        Args:
            scene_id (str): The scene entity ID.
        """
        try:
            # Load the scene configuration
            scene_configuration = await self.load_scene_configuration(scene_id)

            # Create tasks for transitioning each entity.
            # Use create_task because of Appdaemon specifics
            for entity_id, target_values in scene_configuration.items():
                self.create_task(
                    self.transition_to_scene(entity_id, target_values),
                    self.log(f"Adding task for {entity_id}"),
                )

            self.log(f"Scene {scene_id} applied successfully.")
        except Exception as e:
            self.log(
                f"Error applying scene {scene_id}: {traceback.format_exc()}",
                level="ERROR",
            )


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
