import hassapi as hass


from .scenes import apply_scene, load_scene_configuration


from .errors import TransitionError

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
            scene_configuration = await load_scene_configuration(self, scene_entity_id)
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
        await apply_scene(self, scene_entity_id, transition_seconds)
