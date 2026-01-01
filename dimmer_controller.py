"""
Dimmer controller for AppDaemon.

This app handles continuous dimming when a button is held down.
It works in conjunction with the Shelly controller or other button controllers
that write dimming state to an input_text helper.
"""

import hassapi as hass
import json
import asyncio


class DimmerController(hass.Hass):
    """Handle continuous dimming based on helper state changes."""

    def initialize(self):
        """Initialize the dimmer controller app."""
        self.log("DimmerController initialized")
        
        # Track active dimming tasks per light entity to prevent conflicts
        self.active_tasks = {}
        
        # Track cancellation flags per light entity
        self.cancel_flags = {}
        
        # Listen for custom events fired by the HA blueprint
        self.listen_event(self.handle_dimmer_event, "appdaemon_dimmer_controller")

    async def handle_dimmer_event(self, event_name, data, kwargs):
        """
        Handle dimmer events from Home Assistant blueprint.
        
        Args:
            event_name: The event name (appdaemon_dimmer_controller)
            data: Event data containing:
                - dimmer_helper: Entity ID of the helper
                - brightness_step: Entity ID of brightness step input_number
                - transition_length: Entity ID of transition length input_number
                - update_interval: Entity ID of update interval input_number
        """
        dimmer_helper = data.get("dimmer_helper")
        brightness_step_entity = data.get("brightness_step")
        transition_length_entity = data.get("transition_length")
        update_interval_entity = data.get("update_interval")
        
        # Fetch states from entities
        helper_state = await self.get_state(dimmer_helper)
        helper_data = json.loads(helper_state)
        light_entity = helper_data.get("light_entity")
        trigger_state = helper_data.get("trigger_state")
        
        bright_incr = float(await self.get_state(brightness_step_entity))
        trans = float(await self.get_state(transition_length_entity))
        update_interval_ms = float(await self.get_state(update_interval_entity))

        self.log(f"Dimmer event: light={light_entity}, state={trigger_state}, incr={bright_incr}%")

        # If trigger_state is 0, dimming has stopped - cancel any active task and return
        if trigger_state == 0:
            self.log("Dimming stopped (trigger_state=0)")
            if light_entity in self.cancel_flags:
                self.cancel_flags[light_entity] = True
            if light_entity in self.active_tasks:
                self.log(f"Cancelling active dimming task for {light_entity}")
                try:
                    self.active_tasks[light_entity].cancel()
                except Exception as e:
                    self.log(f"Error cancelling task: {e}", level="WARNING")
            return

        # Cancel any existing dimming task for this light by setting flag
        if light_entity in self.cancel_flags:
            self.cancel_flags[light_entity] = True
            
        # Cancel task object as well
        if light_entity in self.active_tasks:
            self.log(f"Cancelling previous dimming task for {light_entity}")
            try:
                self.active_tasks[light_entity].cancel()
            except Exception as e:
                self.log(f"Error cancelling task: {e}", level="WARNING")

        # Reset cancel flag for new task
        self.cancel_flags[light_entity] = False

        # Create and store new dimming task
        task = self.create_task(self._dimming_loop(
            light_entity=light_entity,
            trigger_state=trigger_state,
            bright_incr=bright_incr,
            trans=trans,
            dimmer_helper=dimmer_helper,
            update_interval_ms=update_interval_ms
        ))
        self.active_tasks[light_entity] = task

    async def _dimming_loop(self, light_entity, trigger_state, bright_incr, trans, dimmer_helper, update_interval_ms):
        """
        Execute the dimming loop.
        
        Args:
            light_entity: Entity ID of the light
            trigger_state: 1 (brighten) or -1 (dim)
            bright_incr: Brightness step percentage
            trans: Transition time in seconds
            dimmer_helper: Entity ID of helper to monitor for stop condition
            update_interval_ms: Update interval in milliseconds
        """
        try:
            while True:
                # Check cancellation flag immediately
                if self.cancel_flags.get(light_entity, False):
                    self.log(f"Dimming cancelled by flag for {light_entity}")
                    break
                    
                # Check current brightness
                current_brightness = await self.get_state(light_entity, attribute="brightness")
                if current_brightness is None:
                    current_brightness = 0
                else:
                    current_brightness = int(current_brightness)

                # Check if we've reached limits
                if current_brightness >= 255 and trigger_state == 1:
                    self.log("Reached maximum brightness (255)")
                    break
                if current_brightness <= 0 and trigger_state == -1:
                    self.log("Reached minimum brightness (0)")
                    break

                # Calculate brightness step
                brightness_step = bright_incr if trigger_state == 1 else -bright_incr

                # Apply brightness change
                await self.call_service(
                    "light/turn_on",
                    entity_id=light_entity,
                    brightness_step_pct=brightness_step,
                    transition=trans
                )

                # Wait for update interval or until helper indicates stop
                # Wait for the update interval with a check for stop condition
                await self.sleep(update_interval_ms / 1000.0)
                
                # Check cancellation flag after sleep
                if self.cancel_flags.get(light_entity, False):
                    self.log(f"Dimming cancelled by flag after sleep for {light_entity}")
                    break
                
                # Check if dimming should stop
                helper_state = await self.get_state(dimmer_helper)
                if helper_state:
                    try:
                        helper_data = json.loads(helper_state)
                        # Check if this is for the same light and if trigger_state is now 0
                        if (helper_data.get("light_entity") == light_entity and 
                            helper_data.get("trigger_state") == 0):
                            self.log("Dimming stopped by helper state change")
                            break
                    except (json.JSONDecodeError, ValueError):
                        pass  # Invalid JSON, continue dimming
                        
        except asyncio.CancelledError:
            self.log(f"Dimming loop cancelled for {light_entity}")
        except Exception as e:
            self.log(f"Error in dimming loop: {e}", level="ERROR")
        finally:
            self.log("Dimming loop ended")
            
            # Clean up cancel flag
            if light_entity in self.cancel_flags:
                del self.cancel_flags[light_entity]
            
            # Remove task from active tasks when loop completes
            if light_entity in self.active_tasks:
                del self.active_tasks[light_entity]