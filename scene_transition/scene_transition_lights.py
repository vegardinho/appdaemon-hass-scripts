def parse_group_entities(attributes):
    members = attributes.get("group_entities")
    if isinstance(members, str):
        members = [member.strip() for member in members.split(",")]
    elif not isinstance(members, (list, tuple, set)):
        members = []

    return list(dict.fromkeys(
        member
        for member in members
        if isinstance(member, str) and member.startswith("light.")
    ))


def classify_light_state(entity_state):
    if entity_state in (None, "unknown", "unavailable"):
        return "unavailable"
    if entity_state == "off":
        return "off"
    if entity_state == "on":
        return "on"
    return "other"


def normalize_light_state(entity_id, state):
    if not isinstance(state, dict):
        return {
            "state": "unavailable",
            "available": False,
            "hs_color": None,
            "brightness": None,
            "color_temp_kelvin": None,
            "member_entity_ids": [],
            "supported_color_modes": [],
            "min_color_temp_kelvin": None,
            "max_color_temp_kelvin": None,
        }

    attributes = state.get("attributes", {})
    entity_state = state.get("state")
    state_kind = classify_light_state(entity_state)

    hs_color = attributes.get("hs_color")
    brightness = attributes.get("brightness")
    color_temp_kelvin = attributes.get("color_temp_kelvin")

    if hs_color is not None and len(hs_color) != 2:
        raise ValueError(f"Invalid hs_color format for {entity_id}: {hs_color}")

    return {
        "state": entity_state,
        "available": state_kind not in ("unavailable",),
        "hs_color": hs_color,
        "brightness": brightness,
        "color_temp_kelvin": color_temp_kelvin,
        "member_entity_ids": [
            member
            for member in parse_group_entities(attributes)
            if member != entity_id
        ],
        "supported_color_modes": attributes.get("supported_color_modes", []),
        "min_color_temp_kelvin": attributes.get("min_color_temp_kelvin"),
        "max_color_temp_kelvin": attributes.get("max_color_temp_kelvin"),
    }


async def expand_light_entities(entity_id, get_light_attributes):
    visited = set()
    expanded = []

    async def inspect(current_entity_id):
        if current_entity_id in visited:
            return
        visited.add(current_entity_id)

        attributes = await get_light_attributes(current_entity_id)
        member_ids = attributes["member_entity_ids"]
        if not member_ids:
            if attributes["available"]:
                expanded.append(current_entity_id)
            return

        for member_id in member_ids:
            await inspect(member_id)

    await inspect(entity_id)
    return list(dict.fromkeys(expanded))


async def find_off_members(entity_id, get_light_attributes):
    off_members = []
    visited = {entity_id}

    async def inspect(member_entity_ids):
        for member_id in member_entity_ids:
            if member_id in visited:
                continue
            visited.add(member_id)
            member = await get_light_attributes(member_id)
            if not member["available"]:
                continue
            if member["state"] == "off":
                off_members.append(member_id)
            await inspect(member["member_entity_ids"])

    group = await get_light_attributes(entity_id)
    await inspect(group["member_entity_ids"])
    return off_members
