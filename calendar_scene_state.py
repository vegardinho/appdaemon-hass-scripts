import hassapi as hass
import json
import os
from datetime import datetime, timedelta


class CalendarSceneState(hass.Hass):
    """
        Tracks which calendar scene is active for manual scene cycling.

        This app has two separate responsibilities:
        - listen to the calendar ``message`` attribute and synchronize the saved
            cycle index when Home Assistant changes the active event;
        - handle ``appdaemon_cycle_scene`` events from manual controls.

        The state listener does not apply scenes and does not start transitions.
        Home Assistant's calendar-start automation sends the separate
        ``appdaemon_scene_transition`` event directly to SceneTransition.
        Multiple calendars are supported independently.
    """

    async def initialize(self):
        """Load persistent indices and register calendar/cycle listeners."""
        self.state_file = "/conf/data/calendar_cycle_state.json"
        state_data = await self.load_state()
        self.calendar_indices = state_data.get("indices", {})
        self.registered_calendars = set(state_data.get("registered_calendars", []))
        
        # Re-establish state listeners for registered calendars
        for calendar_id in self.registered_calendars:
            self.listen_state(
                self.handle_calendar_state_change, 
                calendar_id, 
                attribute="message"
            )
            self.log(f"Registered state listener for {calendar_id}")
        
        # Listen for cycle event
        self.listen_event(self.handle_cycle_event, "appdaemon_cycle_scene")
        
        self.log("CalendarSceneState initialized")
        self.log(f"Loaded indices: {self.calendar_indices}")
        self.log(f"Registered calendars: {self.registered_calendars}")

    async def load_state(self):
        """Load the cycle state from JSON file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    state = json.load(f)
                    self.log(f"Loaded state from {self.state_file}")
                    return state
            except Exception as e:
                self.log(f"Error loading state file: {e}", level="ERROR")
                return {"indices": {}, "registered_calendars": []}
        else:
            self.log(f"State file {self.state_file} does not exist, starting fresh")
            return {"indices": {}, "registered_calendars": []}

    async def save_state(self):
        """Save the cycle state to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            state_data = {
                "indices": self.calendar_indices,
                "registered_calendars": list(self.registered_calendars)
            }
            with open(self.state_file, "w") as f:
                json.dump(state_data, f, indent=2)
            self.log(f"Saved state to {self.state_file}")
        except Exception as e:
            self.log(f"Error saving state file: {e}", level="ERROR")

    async def register_calendar(self, calendar_entity_id):
        """
        Register a calendar's ``message`` attribute for index synchronization.

        Registration only enables state tracking. It does not subscribe to
        calendar start events and it does not apply a scene.
        
        Args:
            calendar_entity_id: The calendar entity ID to register
        """
        if calendar_entity_id not in self.registered_calendars:
            self.listen_state(
                self.handle_calendar_state_change,
                calendar_entity_id,
                attribute="message"
            )
            self.registered_calendars.add(calendar_entity_id)
            await self.save_state()
            self.log(f"Registered calendar {calendar_entity_id} for state tracking")

    async def handle_calendar_state_change(self, entity, attribute, old, new, kwargs):
        """
        Synchronize the saved index when the active calendar message changes.

        Home Assistant owns the calendar event-start automation. This callback
        only records which scene is active for a later manual toggle/cycle.
        
        Args:
            entity: The calendar entity ID
            new: The new message attribute (scene entity ID)
        """
        if new and new != old:
            self.log(
                f"Calendar state listener triggered for {entity}: "
                f"message changed from {old!r} to {new!r}; "
                "synchronizing cycle index only (no scene transition)"
            )
            await self.update_index_for_scene(entity, new)

    async def fetch_calendar_events(self, calendar_entity_id, hours=24):
        """
        Fetch events from calendar using calendar.get_events service.
        
        Args:
            calendar_entity_id: The calendar entity ID
            hours: Number of hours to fetch ahead (default: 24)
            
        Returns:
            List of event summaries (scene entity IDs)
        """
        try:
            # Use Home Assistant's calendar.get_events service
            import datetime
            from datetime import timezone
            
            # Get current time and calculate end time based on hours parameter
            start_time = datetime.datetime.now(timezone.utc)
            end_time = start_time + datetime.timedelta(hours=hours)
            
            # Call calendar.get_events service
            response = await self.call_service(
                "calendar/get_events",
                entity_id=calendar_entity_id,
                start_date_time=start_time.isoformat(),
                end_date_time=end_time.isoformat()
            )
            
            # Parse the nested response format
            if not response or not response.get('success'):
                self.log(f"Calendar service call failed for {calendar_entity_id}")
                return []
            
            # Navigate through the nested response structure
            result = response.get('result', {})
            service_response = result.get('response', {})
            calendar_data = service_response.get(calendar_entity_id, {})
            events_data = calendar_data.get('events', [])
            
            if not events_data:
                self.log(f"No events found in {calendar_entity_id} for next 24 hours")
                return []
            
            # Extract event summaries (scene entity IDs)
            events = []
            for event in events_data:
                summary = event.get("summary")
                if summary:
                    events.append(summary)
            
            # Remove duplicates while preserving order (for wrap-around)
            seen = set()
            unique_events = []
            for event in events:
                if event not in seen:
                    seen.add(event)
                    unique_events.append(event)
            
            self.log(f"Found {len(unique_events)} unique events in {calendar_entity_id}: {unique_events}")
            return unique_events
            
        except Exception as e:
            self.log(f"Error fetching calendar events: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")
            return []
            return unique_events
            
        except Exception as e:
            self.log(f"Error fetching calendar events: {e}", level="ERROR")
            return []

    async def get_current_scene(self, calendar_entity_id):
        """
        Read the currently active scene for a manual toggle request.

        This is a read operation. It does not start a transition; the caller
        decides whether to turn the lights off or emit a scene-transition event.
        
        Args:
            calendar_entity_id: The calendar entity ID
            
        Returns:
            The active scene entity_id from calendar, or None if no active event
        """
        try:
            # Get calendar entity state
            state = await self.get_state(calendar_entity_id, attribute="all")
            
            if not state or state.get("state") != "on":
                self.log(f"Calendar {calendar_entity_id} has no active event")
                return None
            
            # The active scene is in the 'message' attribute
            active_scene = state.get("attributes", {}).get("message")
            
            if active_scene:
                self.log(f"Active scene from {calendar_entity_id}: {active_scene}")
                return active_scene
            else:
                self.log(f"No message attribute in {calendar_entity_id} state", level="WARNING")
                return None
                
        except Exception as e:
            self.log(f"Error getting current scene: {e}", level="ERROR")
            return None

    async def cycle_and_get_next_scene(self, calendar_entity_id, reverse=False):
        """
        Increment or decrement the cycle index and return the next scene.
        
        Args:
            calendar_entity_id: The calendar entity ID
            reverse: If True, cycle backwards; if False, cycle forwards
            
        Returns:
            The next scene entity_id, or None if no events
        """
        try:
            events = await self.fetch_calendar_events(calendar_entity_id)
            
            if not events:
                self.log(f"No events found in {calendar_entity_id}")
                return None
            
            # Get current index (default to -1 so first cycle goes to 0, or len(events) for reverse)
            if reverse:
                current_index = self.calendar_indices.get(calendar_entity_id, len(events))
            else:
                current_index = self.calendar_indices.get(calendar_entity_id, -1)
            
            # Increment or decrement and wrap around
            if reverse:
                next_index = (current_index - 1) % len(events)
            else:
                next_index = (current_index + 1) % len(events)
            next_scene = events[next_index]
            
            # Update state
            self.calendar_indices[calendar_entity_id] = next_index
            await self.save_state()
            
            direction = "backwards" if reverse else "forwards"
            self.log(f"Cycled {calendar_entity_id} {direction}: index {current_index} → {next_index}, scene: {next_scene}")
            return next_scene
            
        except Exception as e:
            self.log(f"Error cycling to next scene: {e}", level="ERROR")
            return None

    async def update_index_for_scene(self, calendar_entity_id, scene_entity_id):
        """
        Update the cycle index to match a specific scene.
        Called when a scene is applied (manual or auto).
        
        Args:
            calendar_entity_id: The calendar entity ID
            scene_entity_id: The scene that was applied
        """
        try:
            events = await self.fetch_calendar_events(calendar_entity_id)
            
            if not events:
                self.log(f"No events found in {calendar_entity_id}, cannot update index")
                return
            
            # Find the index of the applied scene
            if scene_entity_id in events:
                new_index = events.index(scene_entity_id)
                self.calendar_indices[calendar_entity_id] = new_index
                await self.save_state()
                self.log(f"Updated {calendar_entity_id} index to {new_index} (scene: {scene_entity_id})")
            else:
                self.log(f"Scene {scene_entity_id} not found in {calendar_entity_id} events", level="WARNING")
                
        except Exception as e:
            self.log(f"Error updating index: {e}", level="ERROR")

    async def handle_cycle_event(self, _event_name, data, _kwargs):
        """
        Handle manual ``appdaemon_cycle_scene`` events.

        Unlike the calendar state listener, this handler advances the saved
        index and emits a scene-transition event.
        
        Args:
            data: Must contain 'calendar_entity_id', 'transition_seconds', and optional 'reverse'
        """
        calendar_entity_id = data.get("calendar_entity_id")
        transition_seconds = data.get("transition_seconds", 1)
        reverse = data.get("reverse", False)
        
        if not calendar_entity_id:
            self.log("No calendar_entity_id provided to appdaemon_cycle_scene event", level="ERROR")
            await self.call_service(
                "persistent_notification/create",
                message="Calendar entity ID is required for scene cycling",
                title="Calendar Cycle Error"
            )
            return
        
        # Register calendar for state tracking if not already registered
        await self.register_calendar(calendar_entity_id)
        
        # Get calendar events
        events = await self.fetch_calendar_events(calendar_entity_id)
        
        if not events:
            self.log(f"No events found in {calendar_entity_id}, cannot cycle", level="WARNING")
            await self.call_service(
                "persistent_notification/create",
                message=f"No events found in {calendar_entity_id} for the next 24 hours",
                title="Calendar Cycle - No Events"
            )
            return
        
        # Get current index
        if reverse:
            current_index = self.calendar_indices.get(calendar_entity_id, len(events))
        else:
            current_index = self.calendar_indices.get(calendar_entity_id, -1)
        
        # Increment or decrement and wrap around
        if reverse:
            next_index = (current_index - 1) % len(events)
        else:
            next_index = (current_index + 1) % len(events)
        next_scene = events[next_index]
        
        # Update state
        self.calendar_indices[calendar_entity_id] = next_index
        await self.save_state()
        
        direction = "backwards" if reverse else "forwards"
        self.log(
            f"Cycling {calendar_entity_id} {direction}: index {current_index} → {next_index}, "
            f"scene: {next_scene}, transition: {transition_seconds}s"
        )
        
        # Fire scene transition event
        await self.fire_event(
            "appdaemon_scene_transition",
            scene_entity_id=next_scene,
            transition_seconds=transition_seconds,
            only_if_lights_on=False,
            calendar_entity_id=calendar_entity_id,
            source="manual_cycle"
        )
