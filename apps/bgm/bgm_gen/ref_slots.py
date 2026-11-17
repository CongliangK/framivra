"""Select dependency-scan slots, without validating paths or loading engines."""


def track_ref_slots(project):
    """Return (track index, original ref) for v2 pure nonblank string slots.

    This is a shape filter, not a path-safety check or a project validator.
    """
    if not isinstance(project, dict):
        return []
    meta, tracks = project.get("meta"), project.get("tracks")
    if not isinstance(meta, dict) or str(meta.get("spec_version")) != "2" or not isinstance(tracks, list):
        return []
    return [(i, track["$ref"]) for i, track in enumerate(tracks)
            if isinstance(track, dict) and set(track) == {"$ref"}
            and isinstance(track["$ref"], str) and track["$ref"].strip()]
