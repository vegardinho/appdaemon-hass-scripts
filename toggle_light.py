import hassapi as hass


class ToggleLight(hass.Hass):
    """
    Toggle calendar-based scenes: turn off if lights are on, cycle to next scene if off.
    """

    async def initialize(self):
        """Initialize the ToggleLight app."""
        self.listen_event(self.handle_toggle_event, "appdaemon_toggle_calendar_scene")
        self.log("ToggleLight initialized")

    async def check_any_lights_on(self, scene_entity_id):
        """
        Check if any lights in the scene are currently on.

        Args:
            scene_entity_id: The scene entity ID

        Returns:
            bool: True if any light is on, False otherwise
        """
        try:
            from scene_transition.scenes import get_scene_light_entities

            scene_transition = await self.get_app("scene_transition")
            light_entities = await get_scene_light_entities(scene_transition, scene_entity_id)

            if not light_entities:
                self.log(f"No light entities found in scene {scene_entity_id}", level="WARNING")
                return False

            # Check each light entity in the scene
            for entity_id in light_entities:
                state = await self.get_state(entity_id)
                if state == "on":
                    self.log(f"Light {entity_id} is on in scene {scene_entity_id}")
                    return True

            self.log(f"All lights are off in scene {scene_entity_id}")
            return False

        except Exception as e:
            self.log(f"Error checking lights: {e}", level="ERROR")
            return False

    async def turn_off_scene_lights(self, scene_entity_id, transition_seconds):
        """
        Turn off all lights in a scene with transition.

        Args:
            scene_entity_id: The scene entity ID
            transition_seconds: Transition time in seconds
        """
        try:
            from scene_transition.scenes import get_scene_light_entities

            scene_transition = await self.get_app("scene_transition")
            light_entities = await get_scene_light_entities(scene_transition, scene_entity_id)

            if not light_entities:
                self.log(f"No light entities found in scene {scene_entity_id}", level="WARNING")
                return

            # Turn off each light entity
            for entity_id in light_entities:
                try:
                    self.log(f"Turning off {entity_id} with {transition_seconds}s transition")
                    await self.turn_off(entity_id=entity_id, transition=transition_seconds)

                except Exception as e:
                    self.log(f"Error turning off {entity_id}: {e}", level="ERROR")

        except Exception as e:
            self.log(f"Error turning off lights: {e}", level="ERROR")

    async def handle_toggle_event(self, _event_name, data, _kwargs):
        """
        Handle appdaemon_toggle_calendar_scene events.

        Args:
            data: Must contain 'calendar_entity_id' and 'transition_seconds'
        """

        calendar_entity_id = data.get("calendar_entity_id")
        transition_seconds = data.get("transition_seconds", 1)

        if not calendar_entity_id:
            self.log("No calendar_entity_id provided to appdaemon_toggle_calendar_scene event", level="ERROR")
            await self.call_service(
                "persistent_notification/create",
                message="Calendar entity ID is required for scene toggle",
                title="Calendar Toggle Error"
            )
            return

        try:
            # Get CalendarSceneState app
            calendar_scene_state = await self.get_app("calendar_scene_state")

            # Register calendar for state tracking if not already registered
            await calendar_scene_state.register_calendar(calendar_entity_id)

            # Get current scene from saved index
            current_scene = await calendar_scene_state.get_current_scene(calendar_entity_id)

            if not current_scene:
                self.log(f"No current scene found for {calendar_entity_id}", level="WARNING")
                await self.call_service(
                    "persistent_notification/create",
                    message=f"No events found in {calendar_entity_id} for the next 24 hours",
                    title="Calendar Toggle - No Events"
                )
                return

            # Check if any lights in current scene are on
            any_lights_on = await self.check_any_lights_on(current_scene)

            if any_lights_on:
                # Turn off lights, don't advance index
                self.log(f"Lights are on in {current_scene}, turning off with {transition_seconds}s transition")
                await self.turn_off_scene_lights(current_scene, transition_seconds)
            else:
                # Just turn on the current scene
                self.log(
                    f"Toggle request found lights off; emitting scene transition "
                    f"for current calendar scene {current_scene}"
                )

                # Fire scene transition event with calendar_entity_id for tracking
                await self.fire_event(
                    "appdaemon_scene_transition",
                    scene_entity_id=current_scene,
                    transition_seconds=transition_seconds,
                    only_if_lights_on=False,
                    calendar_entity_id=calendar_entity_id,
                    source="manual_toggle"
                )

        except Exception as e:
            self.log(f"Error in toggle handler: {e}", level="ERROR")
            await self.call_service(
                "persistent_notification/create",
                message=f"Error toggling calendar scene: {str(e)}",
                title="Calendar Toggle Error"
            )
