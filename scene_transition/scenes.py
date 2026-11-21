import asyncio
import yaml

from .lights import expand_light_entities, get_light_attributes
from .transitions import transition_to_light

async def cancel_scene_tasks(app, tasks_dict):
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
            app.log(
                f"Task already failed during scene cancellation: {e}",
                level="DEBUG",
            )

async def expand_scene_configuration(app, scene_configuration):
    """
    Expand group entities into available individual light entities.

    Individual light entities are preserved as-is. Group entities are
    never used as transition targets and are replaced by their available
    individual members.
    """
    expanded_configuration = {}

    for entity_id, target_values in scene_configuration.items():
        attributes = await get_light_attributes(app, entity_id)
        member_ids = attributes["member_entity_ids"]

        if not member_ids:
            if attributes["available"]:
                expanded_configuration.setdefault(entity_id, target_values)
            continue

        members = await expand_light_entities(
            entity_id,
            lambda member_id: get_light_attributes(app, member_id),
        )
        for member_id in members:
            expanded_configuration.setdefault(member_id, target_values)

    return expanded_configuration

async def apply_scene(app, scene_entity_id, transition_seconds):
    """
    Apply a scene by transitioning all entities to their target states concurrently.

    Args:
        scene_entity_id (str): The scene entity ID.
        transition_seconds (int): Transition time in seconds.
    """
    try:
        # Load the scene configuration
        scene_configuration = await load_scene_configuration(app, scene_entity_id)
        scene_configuration = await expand_scene_configuration(
            app,
            scene_configuration
        )
        lights_in_scene = set(scene_configuration.keys())

        # Find and cancel any OTHER scenes that share lights with this one
        for other_scene_id, scene_data in list(app._active_scenes.items()):
            if other_scene_id != scene_entity_id:
                lights_in_other_scene = set(scene_data["tasks"].keys())
                if lights_in_scene & lights_in_other_scene:  # Intersection found
                    app.log(f"Cancelling scene {other_scene_id} to start {scene_entity_id} (shared lights)")
                    await cancel_scene_tasks(app, scene_data["tasks"])
                    del app._active_scenes[other_scene_id]

        # Create tasks for transitioning each entity
        tasks = {}
        for entity_id, target_values in scene_configuration.items():
            task = app.create_task(
                transition_to_light(app, entity_id, target_values, transition_seconds, scene_entity_id)
            )
            tasks[entity_id] = task

        # Store the scene and its tasks
        app._active_scenes[scene_entity_id] = {"tasks": tasks}

        # Wait for tasks to complete, handling exceptions in real-time
        for completed_task in asyncio.as_completed(tasks.values()):
            try:
                await completed_task
            except asyncio.CancelledError:
                # This task was cancelled by us, expected
                app.log(f"Task {completed_task} was cancelled", level="DEBUG")
                pass
            # BEWARE: Appdaemon (possibly Asyncio) will log this as if not caught, because of how it handles task exceptions. We catch it to
            # prevent our app from crashing, but it may still log an error in the Appdaemon logs. This is likely a quirk of handling
            # cancellations in Appdaemon. Possible workaround is to return a dict with success/failure status instead of raising exceptions.
            except Exception as e:
                # Task failed - cancel all remaining tasks immediately
                app.log(
                    f"Scene transition for {scene_entity_id} failed in task "
                    f"{completed_task}: {e}. Cancelling remaining transition tasks.",
                    level="ERROR"
                )
                await cancel_scene_tasks(app, tasks)
                return

        app.log(f"Scene {scene_entity_id} applied successfully.")
    finally:
        # Clean up the scene record
        if scene_entity_id in app._active_scenes:
            del app._active_scenes[scene_entity_id]

async def load_scene_configuration(app, scene_entity_id):
    """
    Load the details of a scene from the scenes.yaml file.

    Args:
        scene_entity_id (str): The scene entity ID (e.g., 'scene.tv_stue_natt').

    Returns:
        dict: A dictionary of entities and their target attributes.
    """
    app.log(f'Loading scene configuration for entity_id: "{scene_entity_id}"')
    scene_id = await app.get_state(scene_entity_id, attribute="id")

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

async def get_scene_light_entities(app, scene_entity_id):
    """
    Get list of light entity IDs from a scene.

    Args:
        scene_entity_id (str): The scene entity ID.

    Returns:
        list: List of light entity IDs in the scene, or empty list if error.
    """
    try:
        scene_config = await load_scene_configuration(app, scene_entity_id)
        return [entity_id for entity_id in scene_config.keys() if entity_id.startswith("light.")]
    except Exception as e:
        app.log(f"Error getting scene lights for {scene_entity_id}: {e}", level="ERROR")
        return []
