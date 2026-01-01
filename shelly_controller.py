"""
Shelly Dimmer 2 controller for AppDaemon.

This app handles Shelly button events triggered from Home Assistant blueprints.
The HA blueprint fires custom events that this app listens for and processes.
"""

import hassapi as hass
import json


class ShellyController(hass.Hass):
    """Handle Shelly Dimmer 2 button events and control lights."""

    def initialize(self):
        """Initialize the Shelly controller app."""
        self.log("ShellyController initialized")
        
        # Listen for custom events fired by the HA blueprint
        self.listen_event(self.handle_shelly_event, "appdaemon_shelly_controller")

    def handle_shelly_event(self, event_name, data, kwargs):
        """
        Handle Shelly button events from Home Assistant blueprint.
        
        Args:
            event_name: The event name (appdaemon_shelly_controller)
            data: Event data containing:
                - trigger_platform: 'event' or 'device'
                - event_type: 'single' or 'long' (when trigger_platform is 'event')
                - channel: 1 (clockwise) or 2 (counter-clockwise)
                - light_entity: Entity ID of the light to control
                - light_on_script: Optional script entity ID for turning on
                - light_off_script: Optional script entity ID for turning off
                - dimmer_helper: Entity ID for dimmer state helper
        """
        trigger_platform = data.get("trigger_platform")
        event_type = data.get("event_type")
        channel = data.get("channel")
        light_entity = data.get("light_entity")
        light_on_script = data.get("light_on_script", "")
        light_off_script = data.get("light_off_script", "")
        dimmer_helper = data.get("dimmer_helper")

        self.log(f"Received event: platform={trigger_platform}, type={event_type}, channel={channel}, light={light_entity}")

        if trigger_platform == "event":
            # Handle shelly.click events (single or long press start)
            direction = "clockwise" if channel == 1 else "counter-clockwise"
            
            if event_type == "single":
                self._handle_single_click(direction, light_entity, light_on_script, light_off_script)
            elif event_type == "long":
                # Long press started - begin dimming
                trigger_state = 1 if direction == "clockwise" else -1
                self._update_dimmer_state(light_entity, dimmer_helper, trigger_state, direction)
        elif trigger_platform == "device":
            # Handle channel release events (long press end)
            self._update_dimmer_state(light_entity, dimmer_helper, 0)

    def _handle_single_click(self, direction, light_entity, light_on_script, light_off_script):
        """
        Handle single click events.
        
        Args:
            direction: 'clockwise' or 'counter-clockwise'
            light_entity: Entity ID of the light
            light_on_script: Optional script for turning on
            light_off_script: Optional script for turning off
        """
        if direction == "clockwise":
            # Turn on light or run custom script
            if light_on_script and light_on_script.strip():
                self.log(f"Calling light on script: {light_on_script}")
                self.call_service(light_on_script.replace(".", "/"))
            else:
                self.log(f"Turning on light: {light_entity}")
                self.turn_on(light_entity)
        else:  # counter-clockwise
            # Turn off light or run custom script
            if light_off_script and light_off_script.strip():
                self.log(f"Calling light off script: {light_off_script}")
                self.call_service(light_off_script.replace(".", "/"))
            else:
                self.log(f"Turning off light: {light_entity}")
                self.turn_off(light_entity)

    def _update_dimmer_state(self, light_entity, dimmer_helper, trigger_state, direction=None):
        """
        Update dimmer helper with current state.
        
        Args:
            light_entity: Entity ID of the light
            dimmer_helper: Entity ID of the dimmer helper
            trigger_state: 1 (dim up), -1 (dim down), or 0 (stop)
            direction: Optional 'clockwise' or 'counter-clockwise' for logging
        """
        helper_data = {
            "light_entity": light_entity,
            "trigger_state": trigger_state
        }
        
        helper_json = json.dumps(helper_data)
        
        if trigger_state == 0:
            self.log(f"Long press released: {helper_json}")
        else:
            action = "started" if direction else "updated"
            self.log(f"Long press {action}: {helper_json}")
        
        self.call_service(
            "input_text/set_value",
            entity_id=dimmer_helper,
            value=helper_json
        )