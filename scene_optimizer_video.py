import json
import math
import os
import sys
import time
from typing import Dict, List, Any, Set, Tuple, Optional

from scene_optimizer import (
    _resolve_scene_cm_per_unit,
    _resolve_scene_rotation_radians,
    _resolve_scene_plan_pivot_cm,
    _transform_plan_point_cm,
)

# Configuration
# Legacy payloads use metre-based camera data; 2.0.0 video paths use cm.
SCALE_METERS_TO_CM = 100.0


def _resolve_camera_cm_per_unit(payload: dict) -> float:
    """
    Resolve the camera coordinate scale in centimetres per source unit.

    Newer video payloads often store camera paths in centimetres already
    (units="cm"), while older callers emitted metres. Default to metres
    when the payload does not provide an explicit unit hint.
    """
    unit = str(payload.get("units", "")).strip().lower() if isinstance(payload, dict) else ""
    if unit in {"cm", "centimeter", "centimeters", "centimetre", "centimetres"}:
        return 1.0
    if unit in {"mm", "millimeter", "millimeters", "millimetre", "millimetres"}:
        return 0.1
    if unit in {"ft", "feet", "foot"}:
        return 30.48
    if unit in {"m", "meter", "meters", "metre", "metres"}:
        return 100.0
    return 100.0

# Culling logs folder
CULLING_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "culling_logs")
os.makedirs(CULLING_LOGS_DIR, exist_ok=True)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Floor-related items always kept
FLOOR_ITEM_KEYWORDS = ["floor", "slab", "ground", "terrain", "level"]

# Exterior-only items — kept ONLY in EXTERIOR mode, removed in INTERIOR mode
# These are identified by name/type keywords (case-insensitive substring match)
EXTERIOR_ITEM_KEYWORDS = ["roof", "grill", "balcony", "exterior", "facade", "chimney", "antenna", "gutter", "downspout"]

# Item proximity rescue tolerance (cm) — catches items slightly outside polygon
ITEM_RESCUE_TOLERANCE_CM = 85.0

# Extra visibility slack for items.  We still keep the item-aware cone test,
# but this gives a small buffer so partially visible furniture is not dropped.
ITEM_VIEW_MARGIN_DEG = 15.0

# Tolerance for matching vertex positions between rooms (cm)
POSITION_TOLERANCE_CM = 1.0

# Interior precision-culling defaults
DEFAULT_FOV_HALF_DEG = 30.0
DEFAULT_VIEW_DISTANCE_CM = 4500.0
PORTAL_ANGLE_MARGIN_DEG = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# GEOMETRY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray-casting algorithm: returns True if (x,y) is inside the polygon."""
    inside = False
    n = len(polygon)
    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def _point_in_any_area(x: float, y: float, areas: dict, vertices: dict) -> bool:
    """Returns True if (x,y) is inside ANY area polygon."""
    for area_id, area in areas.items():
        poly_verts = []
        for v_id in area.get("vertices", []):
            v = vertices.get(v_id)
            if v:
                poly_verts.append((v.get("x", 0), v.get("y", 0)))
        if poly_verts and _point_in_polygon(x, y, poly_verts):
            return True
    return False


def _layer_has_geometry(layer: dict) -> bool:
    """True when a layer contains any meaningful scene content."""
    if not isinstance(layer, dict):
        return False
    for key in ("lines", "vertices", "areas", "items", "holes", "structures"):
        value = layer.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _pick_primary_layer_id(layers: dict, preferred_id: Optional[str] = None) -> Optional[str]:
    """Choose the best layer to optimize when a layered export is provided."""
    if preferred_id and preferred_id in layers and _layer_has_geometry(layers[preferred_id]):
        return preferred_id
    for layer_id, layer in layers.items():
        if _layer_has_geometry(layer):
            return layer_id
    return next(iter(layers), None)


def _min_dist_to_polygon(ix: float, iy: float, poly_verts: List[Tuple[float, float]]) -> float:
    """Minimum distance from point (ix, iy) to the edges of a polygon."""
    min_dist = float("inf")
    n = len(poly_verts)
    for i in range(n):
        x1, y1 = poly_verts[i]
        x2, y2 = poly_verts[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            dist = math.hypot(ix - x1, iy - y1)
        else:
            t = ((ix - x1) * dx + (iy - y1) * dy) / (dx * dx + dy * dy)
            t = max(0.0, min(1.0, t))
            cx, cy = x1 + t * dx, y1 + t * dy
            dist = math.hypot(ix - cx, iy - cy)
        if dist < min_dist:
            min_dist = dist
    return min_dist


def _angle_to_point(
    cam_x: float,
    cam_y: float,
    dir_x: float,
    dir_y: float,
    px: float,
    py: float,
) -> float:
    """Signed angle (radians) from camera forward direction to a point."""
    dx, dy = px - cam_x, py - cam_y
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return 0.0
    cos_a = max(-1.0, min(1.0, (dx * dir_x + dy * dir_y) / dist))
    return math.acos(cos_a)


def _portal_visible_in_fov(
    cam_x: float,
    cam_y: float,
    dir_x: float,
    dir_y: float,
    fov_half_rad: float,
    p1x: float,
    p1y: float,
    p2x: float,
    p2y: float,
    n_samples: int = 5,
) -> bool:
    """Returns True if any sampled point on the segment falls inside the view cone."""
    for i in range(n_samples):
        t = i / max(1, n_samples - 1)
        sx = p1x + t * (p2x - p1x)
        sy = p1y + t * (p2y - p1y)
        if _angle_to_point(cam_x, cam_y, dir_x, dir_y, sx, sy) <= fov_half_rad:
            return True
    return False


def _portal_world_endpoints(
    line: dict,
    hole: dict,
    vertices: dict,
    cm_per_unit: float,
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Compute the 2D plan endpoints of a hole on a wall."""
    v_ids = line.get("vertices", [])
    if len(v_ids) < 2:
        return None

    v1 = vertices.get(str(v_ids[0]), vertices.get(v_ids[0]))
    v2 = vertices.get(str(v_ids[1]), vertices.get(v_ids[1]))
    if not v1 or not v2:
        return None

    ax, ay = float(v1["x"]), float(v1["y"])
    bx, by = float(v2["x"]), float(v2["y"])
    wall_len = math.hypot(bx - ax, by - ay)
    if wall_len < 1e-6:
        return None

    ux = (bx - ax) / wall_len
    uy = (by - ay) / wall_len

    raw_offset = float(hole.get("offset", 0.5))
    # Match the image pipeline: offsets may be stored either as a wall
    # fraction (0..1) or as an absolute source-unit distance along the wall.
    if abs(raw_offset) <= 1.5:
        offset_cm = raw_offset * wall_len
    else:
        offset_cm = raw_offset * cm_per_unit
    cx = ax + offset_cm * ux
    cy = ay + offset_cm * uy

    raw_w = hole.get("properties", {}).get("width", hole.get("width", 100))
    if isinstance(raw_w, dict):
        half_w = float(raw_w.get("length", 100)) / 2.0
    else:
        half_w = float(raw_w) / 2.0

    p1x, p1y = cx - half_w * ux, cy - half_w * uy
    p2x, p2y = cx + half_w * ux, cy + half_w * uy
    return (p1x, p1y), (p2x, p2y)


def _rooms_beyond_portal(
    line: dict,
    active_area_id: str,
    areas: dict,
    vertices: dict,
) -> Set[str]:
    """Find rooms on the opposite side of a portal-bearing wall."""
    v_ids = [str(v) for v in line.get("vertices", [])[:2]]
    if len(v_ids) < 2:
        return set()

    wall_positions: Set[Tuple[float, float]] = set()
    for vid in v_ids:
        v = vertices.get(vid, vertices.get(str(vid)))
        if v:
            wall_positions.add((round(float(v["x"]), 0), round(float(v["y"]), 0)))

    beyond: Set[str] = set()
    for aid, area in areas.items():
        if aid == active_area_id:
            continue
        area_verts = [str(v) for v in area.get("vertices", [])]
        if any(av in v_ids for av in area_verts):
            beyond.add(aid)
            continue

        for av in area_verts:
            av_data = vertices.get(av, vertices.get(str(av)))
            if av_data:
                key = (round(float(av_data["x"]), 0), round(float(av_data["y"]), 0))
                if key in wall_positions:
                    beyond.add(aid)
                    break

    return beyond


def _collect_portal_visible_rooms(
    active_area_id: str,
    areas: dict,
    lines: dict,
    holes: dict,
    vertices: dict,
    cm_per_unit: float,
    cam_x: float,
    cam_y: float,
    dir_x: float,
    dir_y: float,
    fov_half_rad: float,
    max_dist_cm: float,
    log_fn,
    sample_label: str = "",
) -> Set[str]:
    """Collect rooms visible through the active room's portals."""
    prefix = f"{sample_label} " if sample_label else ""
    log_fn(f"  {prefix}Portal visibility sweep from active room {active_area_id} ...")

    active_verts: Set[str] = set(str(v) for v in areas[active_area_id].get("vertices", []))
    visible_rooms: Set[str] = set()
    effective_fov = fov_half_rad + math.radians(PORTAL_ANGLE_MARGIN_DEG)

    for lid, line in lines.items():
        v_ids = [str(v) for v in line.get("vertices", [])[:2]]
        if len(v_ids) < 2:
            continue

        if not any(vid in active_verts for vid in v_ids):
            continue

        line_holes = line.get("holes", [])
        is_hidden  = line.get("visible") is False
        
        if not line_holes and not is_hidden:
            continue

        # (a) Regular portals
        for hole_id in line_holes:
            hid = str(hole_id)
            hole = holes.get(hid, holes.get(hole_id))
            if not hole:
                continue

            endpoints = _portal_world_endpoints(line, hole, vertices, cm_per_unit)
            if not endpoints:
                continue

            (ep1x, ep1y), (ep2x, ep2y) = endpoints
            portal_cx = (ep1x + ep2x) / 2.0
            portal_cy = (ep1y + ep2y) / 2.0
            dist = math.hypot(portal_cx - cam_x, portal_cy - cam_y)

            if dist > max_dist_cm:
                continue

            if not _portal_visible_in_fov(
                cam_x, cam_y, dir_x, dir_y, effective_fov, ep1x, ep1y, ep2x, ep2y
            ):
                continue

            beyond = _rooms_beyond_portal(line, active_area_id, areas, vertices)
            if beyond:
                visible_rooms.update(beyond)

        # (b) Hidden walls (entire wall spans acts as a portal)
        if is_hidden:
            vd1 = vertices.get(v_ids[0], vertices.get(str(v_ids[0])))
            vd2 = vertices.get(v_ids[1], vertices.get(str(v_ids[1])))
            if vd1 and vd2:
                p1x, p1y = float(vd1.get("x", 0)), float(vd1.get("y", 0))
                p2x, p2y = float(vd2.get("x", 0)), float(vd2.get("y", 0))
                portal_cx, portal_cy = (p1x + p2x) / 2.0, (p1y + p2y) / 2.0
                dist = math.hypot(portal_cx - cam_x, portal_cy - cam_y)
                if dist <= max_dist_cm:
                    if _portal_visible_in_fov(cam_x, cam_y, dir_x, dir_y, effective_fov, p1x, p1y, p2x, p2y):
                        beyond = _rooms_beyond_portal(line, active_area_id, areas, vertices)
                        if beyond:
                            visible_rooms.update(beyond)

    return visible_rooms


# ─────────────────────────────────────────────────────────────────────────────
# BFS FLOOD-FILL  (shared helper)
# ─────────────────────────────────────────────────────────────────────────────

def _flood_fill_connected_rooms(
    seed_ids: Set[str],
    areas: dict,
    lines: dict,
    vertices: dict,
    log_fn,
) -> Set[str]:
    """
    Starting from seed_ids, expand kept rooms via:
      (a) Shared vertex positions (adjacent rooms share a wall corner)
      (b) Walls that have holes (openings) or are hidden/invisible
    Returns the full set of kept area IDs.
    """
    area_positions: Dict[str, List[Tuple[float, float]]] = {}
    for a_id, area in areas.items():
        positions = []
        for v_id in area.get("vertices", []):
            v = vertices.get(v_id)
            if v:
                positions.append((v.get("x", 0), v.get("y", 0)))
        area_positions[a_id] = positions

    def areas_are_neighbors(aid1: str, aid2: str) -> bool:
        for (x1, y1) in area_positions.get(aid1, []):
            for (x2, y2) in area_positions.get(aid2, []):
                if (
                    abs(x1 - x2) < POSITION_TOLERANCE_CM
                    and abs(y1 - y2) < POSITION_TOLERANCE_CM
                ):
                    return True
        return False

    kept: Set[str] = set(seed_ids)
    changed = True

    while changed:
        changed = False

        # (a) Position-based adjacency
        for a_id in list(areas.keys()):
            if a_id in kept:
                continue
            for kept_id in list(kept):
                if areas_are_neighbors(kept_id, a_id):
                    kept.add(a_id)
                    changed = True
                    log_fn(
                        f"  ➕ Adding Neighbour Room (Position Match): "
                        f"'{areas[a_id].get('name')}' ({a_id})"
                    )
                    break

        # (b) Hole / hidden-wall connections
        all_kept_verts: Set[str] = set()
        for aid in kept:
            all_kept_verts.update(areas[aid].get("vertices", []))

        for lid, line in lines.items():
            line_holes  = line.get("holes", [])
            line_hidden = line.get("visible") is False
            if not line_holes and not line_hidden:
                continue

            v_ids = line.get("vertices", [])
            if len(v_ids) < 2:
                continue
            v1, v2 = v_ids[0], v_ids[1]

            v1_pos = (vertices[v1].get("x", 0), vertices[v1].get("y", 0)) if v1 in vertices else None
            v2_pos = (vertices[v2].get("x", 0), vertices[v2].get("y", 0)) if v2 in vertices else None

            touches_kept = v1 in all_kept_verts or v2 in all_kept_verts
            if not touches_kept:
                for aid in kept:
                    for (px, py) in area_positions.get(aid, []):
                        if v1_pos and abs(v1_pos[0] - px) < POSITION_TOLERANCE_CM and abs(v1_pos[1] - py) < POSITION_TOLERANCE_CM:
                            touches_kept = True
                            break
                        if v2_pos and abs(v2_pos[0] - px) < POSITION_TOLERANCE_CM and abs(v2_pos[1] - py) < POSITION_TOLERANCE_CM:
                            touches_kept = True
                            break
                    if touches_kept:
                        break

            if touches_kept:
                for a_id, area in areas.items():
                    if a_id in kept:
                        continue
                    area_verts = set(area.get("vertices", []))
                    if v1 in area_verts or v2 in area_verts:
                        kept.add(a_id)
                        changed = True
                        reason = "Hole-Connected" if line_holes else "Hidden-Wall-Connected"
                        log_fn(
                            f"  ➕ Adding Neighbour Room ({reason}): "
                            f"'{area.get('name')}' ({a_id})"
                        )

    return kept


# ─────────────────────────────────────────────────────────────────────────────
# INTERIOR CULLING  (video)
# ─────────────────────────────────────────────────────────────────────────────

def _is_exterior_asset_video(item: dict) -> bool:
    """Check if an item is an exterior-only asset by its name/type keywords."""
    item_type = str(item.get("type", "")).lower()
    item_name = str(item.get("name", "")).lower()
    full_desc = item_type + " " + item_name
    return any(kw in full_desc for kw in EXTERIOR_ITEM_KEYWORDS)


def _item_dimension_cm(
    item: dict,
    key: str,
    default_cm: float = 100.0,
    cm_per_unit: float = 1.0,
) -> float:
    """Read an item's dimension in centimetres from top-level or properties."""
    raw = item.get(key, None)
    if raw is None and isinstance(item.get("properties"), dict):
        raw = item["properties"].get(key, None)

    if isinstance(raw, dict):
        if "length" in raw:
            try:
                return float(raw.get("length", default_cm))
            except Exception:
                return default_cm
        if "value" in raw:
            try:
                return float(raw.get("value", default_cm))
            except Exception:
                return default_cm
    try:
        if raw is not None:
            return float(raw) * cm_per_unit
    except Exception:
        pass
    return default_cm


def _item_footprint_radius_cm(item: dict, cm_per_unit: float = 1.0) -> float:
    """
    Approximate the item footprint with a circle radius in plan view.
    Using half the diagonal keeps partially visible furniture from being
    culled when only part of it enters the camera cone.
    Item dimensions are stored in the same source units as the plan, so we
    scale them to centimetres before visibility checks.
    """
    width = max(_item_dimension_cm(item, "width", 100.0, cm_per_unit), 1.0)
    depth = max(_item_dimension_cm(item, "depth", 100.0, cm_per_unit), 1.0)
    return 0.5 * math.hypot(width, depth)


def _item_visible_from_sample(
    item: dict,
    sample: Dict[str, Any],
    item_x: float,
    item_y: float,
    max_view_dist_cm: float,
    cm_per_unit: float = 1.0,
) -> bool:
    """Return True when the item's footprint overlaps the camera view cone."""
    cam_x = float(sample["cam_x"])
    cam_y = float(sample["cam_y"])
    cam_dir_x = float(sample["cam_dir_x"])
    cam_dir_y = float(sample["cam_dir_y"])
    fov_half_deg = float(sample["fov_half_deg"])

    radius_cm = _item_footprint_radius_cm(item, cm_per_unit)
    dist_cm = math.hypot(item_x - cam_x, item_y - cam_y)
    if max(0.0, dist_cm - radius_cm) > max_view_dist_cm:
        return False

    angle_deg = math.degrees(
        _angle_to_point(cam_x, cam_y, cam_dir_x, cam_dir_y, item_x, item_y)
    )
    # Expand the FOV by the item's apparent angular radius so items that only
    # partially overlap the cone are kept.
    if dist_cm < 1e-6:
        angular_radius_deg = 90.0
    else:
        angular_radius_deg = math.degrees(
            math.asin(min(1.0, radius_cm / max(dist_cm, 1.0)))
        )

    fov_limit_deg = fov_half_deg + ITEM_VIEW_MARGIN_DEG + angular_radius_deg
    return angle_deg <= fov_limit_deg


def _cull_interior_video(
    active_area_ids: Set[str],
    active_samples: List[Dict[str, Any]],
    vertices: dict,
    lines: dict,
    areas: dict,
    items: dict,
    holes: dict,
    cm_per_unit: float,
    rotation_radians: float,
    pivot: Tuple[float, float],
    log_fn,
    max_view_dist_cm: float = DEFAULT_VIEW_DISTANCE_CM,
) -> Tuple[dict, dict, dict, dict, dict]:
    """
    INTERIOR MODE — camera path passes through one or more rooms.

    Keep:
      • All active rooms + rooms reachable via openings/hidden walls (BFS)
      • Walls whose both endpoints belong to kept rooms
      • Holes (doors/windows) on kept walls
      • Items inside kept rooms (+ 85 cm rescue)
      • Floor assets always

    Remove:
      • All rooms not reachable from any active room
      • Walls, holes, items outside kept rooms
      • Exterior-only items (roof, grill, balcony, etc.) — even if geometrically inside
    """
    log_fn("  Interior Culling (Video Portal Mode)")

    log_fn(
        f"  Interior samples: {len(active_samples)} | MaxDist={max_view_dist_cm:.0f}cm"
    )

    transformed_vertices: Dict[str, dict] = {}
    for vid, vertex in vertices.items():
        if not isinstance(vertex, dict):
            continue
        tx, ty = _transform_plan_point_cm(
            float(vertex.get("x", 0.0)),
            float(vertex.get("y", 0.0)),
            cm_per_unit,
            rotation_radians,
            pivot,
        )
        v_copy = dict(vertex)
        v_copy["x"] = tx
        v_copy["y"] = ty
        transformed_vertices[vid] = v_copy

    transformed_area_polys: Dict[str, List[Tuple[float, float]]] = {}
    for aid, area in areas.items():
        poly_verts: List[Tuple[float, float]] = []
        for v_id in area.get("vertices", []):
            v = transformed_vertices.get(str(v_id), transformed_vertices.get(v_id))
            if v:
                poly_verts.append((float(v.get("x", 0.0)), float(v.get("y", 0.0))))
        if poly_verts:
            transformed_area_polys[aid] = poly_verts

    kept_area_ids: Set[str] = set(active_area_ids)
    for idx, sample in enumerate(active_samples):
        active_area_id = sample.get("active_area_id")
        if not active_area_id or active_area_id not in areas:
            continue

        visible_rooms = _collect_portal_visible_rooms(
            active_area_id,
            areas,
            lines,
            holes,
            transformed_vertices,
            cm_per_unit,
            float(sample["cam_x"]),
            float(sample["cam_y"]),
            float(sample["cam_dir_x"]),
            float(sample["cam_dir_y"]),
            math.radians(float(sample["fov_half_deg"])),
            max_view_dist_cm,
            log_fn,
            sample_label=f"[sample {idx}]",
        )
        kept_area_ids.update(visible_rooms)

    log_fn(f"  Total kept rooms: {len(kept_area_ids)}")
    for aid in kept_area_ids:
        room_tag = "ACTIVE" if aid in active_area_ids else "PORTAL"
        log_fn(f"     - [{room_tag}] {areas[aid].get('name', 'Unknown')} ({aid})")

    # ── Areas ────────────────────────────────────────────────────────────────
    new_areas = {aid: areas[aid] for aid in kept_area_ids}

    # ── Vertices ─────────────────────────────────────────────────────────────
    kept_vertex_ids: Set[str] = set()
    for area in new_areas.values():
        kept_vertex_ids.update(area.get("vertices", []))

    kept_positions: Set[Tuple] = set()
    for vid in kept_vertex_ids:
        v = transformed_vertices.get(vid)
        if v:
            kept_positions.add((round(v.get("x", 0), 0), round(v.get("y", 0), 0)))

    def vertex_is_near_kept(vid: str) -> bool:
        if vid in kept_vertex_ids:
            return True
        v = transformed_vertices.get(vid)
        if v:
            return (round(v.get("x", 0), 0), round(v.get("y", 0), 0)) in kept_positions
        return False

    new_vertices = {vid: vertices[vid] for vid in kept_vertex_ids if vid in vertices}

    # Keep active room shell walls even if vertex matching is imperfect.
    active_room_vertex_ids: Set[str] = set()
    active_room_positions: Set[Tuple] = set()
    for aid in active_area_ids:
        area = areas.get(aid)
        if not area:
            continue
        for vid in area.get("vertices", []):
            vid = str(vid)
            active_room_vertex_ids.add(vid)
            v = transformed_vertices.get(vid)
            if v:
                active_room_positions.add((round(v.get("x", 0), 0), round(v.get("y", 0), 0)))

    def wall_belongs_to_active_room(v1_id: str, v2_id: str) -> bool:
        if v1_id in active_room_vertex_ids or v2_id in active_room_vertex_ids:
            return True
        for vid in (v1_id, v2_id):
            v = transformed_vertices.get(vid)
            if not v:
                continue
            if (round(v.get("x", 0), 0), round(v.get("y", 0), 0)) in active_room_positions:
                return True
        return False

    # ── Lines ─────────────────────────────────────────────────────────────────
    new_lines: Dict[str, Any] = {}
    culled_walls: List[str] = []
    for lid, line in lines.items():
        v_ids = line.get("vertices", [])
        if len(v_ids) < 2:
            continue

        v1, v2 = v_ids[0], v_ids[1]
        if not (vertex_is_near_kept(v1) and vertex_is_near_kept(v2)):
            culled_walls.append(f"Wall {lid} [not touching kept rooms]")
            continue

        vd1 = vertices.get(v1)
        vd2 = vertices.get(v2)
        if not vd1 or not vd2:
            continue

        if wall_belongs_to_active_room(v1, v2):
            # The active room shell should remain intact in interior video
            # renders even when there are no portals to reveal neighbors.
            new_lines[lid] = line
            for vid in (v1, v2):
                if vid not in new_vertices and vid in vertices:
                    new_vertices[vid] = vertices[vid]
                    kept_vertex_ids.add(vid)
            continue

        # Keep the room shell intact. Interior FOV pruning is for items and
        # adjacent rooms, not for walls that bound the active room.
        new_lines[lid] = line
        for vid in (v1, v2):
            if vid not in new_vertices and vid in vertices:
                new_vertices[vid] = vertices[vid]
                kept_vertex_ids.add(vid)

    if culled_walls:
        log_fn(f"\n  Culled {len(culled_walls)} walls:")
        for cw in culled_walls:
            log_fn(f"     - {cw}")

    # ── Holes ─────────────────────────────────────────────────────────────────
    new_holes = {
        hid: hole
        for hid, hole in holes.items()
        if hole.get("line") in new_lines
    }

    # ── Items ─────────────────────────────────────────────────────────────────
    new_items: Dict[str, Any] = {}
    culled_items: List[str] = []

    for iid, item in items.items():
        item_type = str(item.get("type", "")).lower()
        item_name = str(item.get("name", "")).lower()

        if any(kw in item_type or kw in item_name for kw in FLOOR_ITEM_KEYWORDS):
            new_items[iid] = item
            log_fn(f"  🛡️ Preserving Floor Asset: '{item.get('name')}' ({iid})")
            continue

        # Remove exterior-only assets (roof, grill, balcony) in interior render
        if _is_exterior_asset_video(item):
            culled_items.append(f"{item.get('name', 'Unknown')} ({iid}) [exterior-only asset]")
            log_fn(f"  🗑️ Removing exterior-only asset in interior render: '{item.get('name', 'Unknown')}' ({iid})")
            continue

        ix, iy = _transform_plan_point_cm(
            float(item.get("x", 0)),
            float(item.get("y", 0)),
            cm_per_unit,
            rotation_radians,
            pivot,
        )

        visible_from_path = False
        for sample in active_samples:
            if _item_visible_from_sample(item, sample, ix, iy, max_view_dist_cm, cm_per_unit):
                visible_from_path = True
                break

        if not visible_from_path:
            culled_items.append(
                f"{item.get('name', 'Unknown')} ({iid}) [outside all interior sample FOV/range]"
            )
            continue

        # Pass 1 – polygon inclusion
        is_kept = False
        for aid in kept_area_ids:
            poly_verts = transformed_area_polys.get(aid, [])
            if poly_verts and _point_in_polygon(ix, iy, poly_verts):
                is_kept = True
                break

        if is_kept:
            new_items[iid] = item
            continue

        # Pass 2 – proximity rescue
        rescued = False
        for aid in kept_area_ids:
            poly_verts = transformed_area_polys.get(aid, [])
            if not poly_verts:
                continue
            min_dist = _min_dist_to_polygon(ix, iy, poly_verts)
            if min_dist <= ITEM_RESCUE_TOLERANCE_CM:
                log_fn(
                    f"  ✨ Rescuing item '{item.get('name', 'Unknown')}' ({iid}) "
                    f"— Dist: {min_dist:.1f} cm <= {ITEM_RESCUE_TOLERANCE_CM} cm"
                )
                new_items[iid] = item
                rescued = True
                break

        if not rescued:
            culled_items.append(f"{item.get('name', 'Unknown')} ({iid})")

    if culled_items:
        log_fn(f"\n  🗑️  Culled {len(culled_items)} exterior/out-of-room items:")
        for ci in culled_items:
            log_fn(f"     - {ci}")

    return new_vertices, new_lines, new_areas, new_items, new_holes


# ─────────────────────────────────────────────────────────────────────────────
# EXTERIOR CULLING  (video)
# ─────────────────────────────────────────────────────────────────────────────

def _cull_exterior_video(
    vertices: dict,
    lines: dict,
    areas: dict,
    items: dict,
    holes: dict,
    cm_per_unit: float,
    rotation_radians: float,
    pivot: Tuple[float, float],
    log_fn,
) -> Tuple[dict, dict, dict, dict, dict]:
    """
    EXTERIOR MODE — entire camera path is outside all rooms.

    Keep:
      • ALL walls, areas (building shell)
      • ALL holes (doors/windows) — marked with is_exterior_black=true
        so the renderer fills openings with BLACK (no interior visible)
      • Exterior-named items (roof, grill, balcony, etc.) — by keyword match
      • Items physically outside all room polygons (garden, trees, etc.)
      • Floor assets always

    Remove:
      • ALL interior items inside room polygons
        (sofas, fridges, lights, frames, pictures, mirrors, etc.)
      • Wall fittings are NOT preserved — in exterior mode, nothing inside
        the building should be visible through the black windows

    All holes (doors/windows/openings) are flagged is_exterior_black=true
    so the Godot renderer places a black blocker in the opening. This is
    purely geometry-based — no name matching is used for holes.
    """
    log_fn(f"  ── Exterior Culling (Video) ──")

    # Keep all architectural geometry intact
    new_areas    = dict(areas)
    new_lines    = dict(lines)
    new_vertices = dict(vertices)

    transformed_vertices: Dict[str, dict] = {}
    for vid, vertex in vertices.items():
        if not isinstance(vertex, dict):
            continue
        tx, ty = _transform_plan_point_cm(
            float(vertex.get("x", 0.0)),
            float(vertex.get("y", 0.0)),
            cm_per_unit,
            rotation_radians,
            pivot,
        )
        v_copy = dict(vertex)
        v_copy["x"] = tx
        v_copy["y"] = ty
        transformed_vertices[vid] = v_copy

    transformed_area_polys: Dict[str, List[Tuple[float, float]]] = {}
    for aid, area in areas.items():
        poly_verts: List[Tuple[float, float]] = []
        for v_id in area.get("vertices", []):
            v = transformed_vertices.get(str(v_id), transformed_vertices.get(v_id))
            if v:
                poly_verts.append((float(v.get("x", 0.0)), float(v.get("y", 0.0))))
        if poly_verts:
            transformed_area_polys[aid] = poly_verts

    log_fn(f"  ✓ Keeping ALL {len(new_areas)} areas  (structural mesh)")
    log_fn(f"  ✓ Keeping ALL {len(new_lines)} walls  (building shell)")

    for aid, area in new_areas.items():
        log_fn(f"     ✓ Area: {area.get('name', 'Unknown')} ({aid})")

    # ── Mark ALL holes as exterior-black ──────────────────────────────────────
    # Every door/window/opening gets is_exterior_black=true so the renderer
    # places a black plane in the opening. This uses geometry (holes dict),
    # NOT name-based matching.
    new_holes = {}
    for hid, hole in holes.items():
        hole_copy = dict(hole)
        hole_copy["is_exterior_black"] = True
        new_holes[hid] = hole_copy
        log_fn(f"     ⚫ Hole marked BLACK: ({hole.get('type','?')}) {hole.get('name', hid)}")

    log_fn(f"  ⚫ Marked ALL {len(new_holes)} holes as is_exterior_black=true")

    # ── Filter items ──────────────────────────────────────────────────────────
    # In exterior mode:
    #   - Keep: floor assets, exterior-named items (roof/grill/balcony),
    #           items physically outside all room polygons
    #   - Remove: EVERYTHING else (all interior items, wall fittings,
    #             frames, pictures, mirrors, etc.)
    new_items:           Dict[str, Any] = {}
    culled_items:        List[str]      = []
    kept_exterior_items: List[str]      = []

    for iid, item in items.items():
        item_type = str(item.get("type", "")).lower()
        item_name = str(item.get("name", "")).lower()
        ix, iy = _transform_plan_point_cm(
            float(item.get("x", 0)),
            float(item.get("y", 0)),
            cm_per_unit,
            rotation_radians,
            pivot,
        )

        # Always keep floor/slab assets
        if any(kw in item_type or kw in item_name for kw in FLOOR_ITEM_KEYWORDS):
            new_items[iid] = item
            log_fn(f"  🛡️ Preserving Floor Asset: '{item.get('name')}' ({iid})")
            continue

        # Keep exterior-named assets (roof, grill, balcony, etc.) — by keyword
        if _is_exterior_asset_video(item):
            new_items[iid] = item
            kept_exterior_items.append(f"{item.get('name', 'Unknown')} ({iid}) [exterior keyword]")
            log_fn(
                f"  🏠 Keeping exterior-named asset: "
                f"'{item.get('name', 'Unknown')}' ({iid})"
            )
            continue

        # Item outside all room polygons → external → keep
        is_outside = True
        for poly_verts in transformed_area_polys.values():
            if _point_in_polygon(ix, iy, poly_verts):
                is_outside = False
                break
        if is_outside:
            new_items[iid] = item
            kept_exterior_items.append(f"{item.get('name', 'Unknown')} ({iid}) [outside rooms]")
            log_fn(
                f"  🌳 Keeping external item (outside all rooms): "
                f"'{item.get('name', 'Unknown')}' ({iid}) at ({ix:.1f}, {iy:.1f})"
            )
            continue

        # Everything else inside rooms → cull (no wall-fitting exceptions)
        culled_items.append(f"{item.get('name', 'Unknown')} ({iid})")
        log_fn(f"  🗑️ Culling interior item: '{item.get('name', 'Unknown')}' ({iid})")

    if kept_exterior_items:
        log_fn(f"\n  🌳 Kept {len(kept_exterior_items)} exterior items:")
        for ei in kept_exterior_items:
            log_fn(f"     + {ei}")

    if culled_items:
        log_fn(f"\n  🗑️  Culled {len(culled_items)} interior items:")
        for ci in culled_items:
            log_fn(f"     - {ci}")

    return new_vertices, new_lines, new_areas, new_items, new_holes


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────────────────────────────────────

class VideoSceneOptimizer:
    def __init__(self, log_path: Optional[str] = None):
        """
        Initialize VideoSceneOptimizer.

        Args:
            log_path: Full path for the culling log file. If None, uses default.
        """
        if log_path is None:
            log_path = os.path.join(CULLING_LOGS_DIR, "video_culling_log.txt")

        self.log_path = log_path
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.write("--- Video Scene Culling Log ---\n")
        except Exception:
            pass

    def log(self, msg: str) -> None:
        print(f"[VideoSceneOptimizer] {msg}")
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{msg}\n")
        except Exception:
            pass

    def cull_scene(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Optimise the video render payload using two-mode culling.

        INTERIOR (any keyframe camera point is inside a room)
        ───────────────────────────────────────────────────────
        • Keep all rooms the camera enters + connected rooms via openings/hidden walls
        • Remove all exterior walls, areas, and out-of-room items
        • Textures: interior materials applied normally

        EXTERIOR (ALL keyframe camera points are outside all rooms)
        ─────────────────────────────────────────────────────────────
        • Keep all walls, areas, and holes (building shell)
        • Window/door openings render BLACK — no interior backdrop generated
        • Remove ALL interior furniture (sofa, fridge, decorations, etc.)
        • Keep: items outside room polygons + architectural wall fittings only
        • Textures: exterior-facing materials (handled by renderer)

        Returns a new payload with optimised floor_plan_data.
        """
        try:
            # ── 1. Parse floor_plan_data ──────────────────────────────────────
            fp_str = payload.get("floor_plan_data")
            if not fp_str:
                self.log("⚠️ No floor_plan_data found.")
                return payload

            if isinstance(fp_str, dict):
                fp_data = fp_str
            else:
                try:
                    fp_data = json.loads(fp_str)
                except Exception as e:
                    self.log(f"⚠️ Failed to parse floor_plan_data: {e}")
                    return payload

            fp_version = str(fp_data.get("version", "")).strip() if isinstance(fp_data, dict) else ""
            cm_per_unit = _resolve_scene_cm_per_unit(fp_data)
            plan_rotation_radians = _resolve_scene_rotation_radians(fp_data)
            plan_pivot_cm = _resolve_scene_plan_pivot_cm(fp_data, cm_per_unit)
            camera_cm_per_unit = _resolve_camera_cm_per_unit(payload)
            self.log(
                f"   Plan units: {cm_per_unit:.2f} cm/source unit | "
                f"camera units: {camera_cm_per_unit:.2f} cm/source unit | "
                f"rotation: {math.degrees(plan_rotation_radians):.1f}° | "
                f"pivot: ({plan_pivot_cm[0]:.1f}, {plan_pivot_cm[1]:.1f})"
            )

            # ── 2. Collect camera visibility samples from the animation path ──
            camera_samples: List[Dict[str, Any]] = []
            payload_fov_half_deg: Optional[float] = None
            if "interior_fov_half_deg" in payload:
                payload_fov_half_deg = float(payload["interior_fov_half_deg"])
            max_view_dist_cm = float(
                payload.get("interior_view_dist_cm", DEFAULT_VIEW_DISTANCE_CM)
            )

            if "video_animation" in payload and payload["video_animation"] is not None:
                anim      = payload["video_animation"]
                keyframes = anim.get("keyframes", []) if isinstance(anim, dict) else []

                if keyframes:
                    self.log("🎬 Video Animation detected. Analysing full camera path...")
                    for kf in keyframes:
                        tjs = kf.get("threejs_camera_data")
                        if tjs and isinstance(tjs, dict) and "position" in tjs:
                            pos = tjs["position"]
                            cam_x, cam_y = _transform_plan_point_cm(
                                float(pos.get("x", 0.0)),
                                float(pos.get("z", 0.0)),
                                camera_cm_per_unit,
                                plan_rotation_radians,
                                plan_pivot_cm,
                            )
                            dir_x, dir_y = 1.0, 0.0
                            look_at = tjs.get("lookAt")
                            if isinstance(look_at, dict):
                                look_x, look_y = _transform_plan_point_cm(
                                    float(look_at.get("x", pos.get("x", 0.0))),
                                    float(look_at.get("z", pos.get("z", 0.0))),
                                    camera_cm_per_unit,
                                    plan_rotation_radians,
                                    plan_pivot_cm,
                                )
                                dx = look_x - cam_x
                                dy = look_y - cam_y
                                mag = math.hypot(dx, dy)
                                if mag > 1e-6:
                                    dir_x, dir_y = dx / mag, dy / mag
                            fov_half_deg = payload_fov_half_deg
                            if fov_half_deg is None:
                                fov_half_deg = float(
                                    tjs.get("fov", DEFAULT_FOV_HALF_DEG * 2.0)
                                ) / 2.0
                            camera_samples.append({
                                "cam_x": cam_x,
                                "cam_y": cam_y,
                                "cam_dir_x": dir_x,
                                "cam_dir_y": dir_y,
                                "fov_half_deg": fov_half_deg,
                            })
                        elif "position" in kf:
                            pos = kf["position"]
                            cam_x, cam_y = _transform_plan_point_cm(
                                float(pos.get("x", 0.0)),
                                float(pos.get("z", 0.0)),
                                camera_cm_per_unit,
                                plan_rotation_radians,
                                plan_pivot_cm,
                            )
                            dir_x, dir_y = 1.0, 0.0
                            target = kf.get("target")
                            if isinstance(target, dict):
                                target_x, target_y = _transform_plan_point_cm(
                                    float(target.get("x", pos.get("x", 0.0))),
                                    float(target.get("z", pos.get("z", 0.0))),
                                    camera_cm_per_unit,
                                    plan_rotation_radians,
                                    plan_pivot_cm,
                                )
                                dx = target_x - cam_x
                                dy = target_y - cam_y
                                mag = math.hypot(dx, dy)
                                if mag > 1e-6:
                                    dir_x, dir_y = dx / mag, dy / mag
                            fov_half_deg = payload_fov_half_deg
                            if fov_half_deg is None:
                                fov_half_deg = float(
                                    kf.get("fov", DEFAULT_FOV_HALF_DEG * 2.0)
                                ) / 2.0
                            camera_samples.append({
                                "cam_x": cam_x,
                                "cam_y": cam_y,
                                "cam_dir_x": dir_x,
                                "cam_dir_y": dir_y,
                                "fov_half_deg": fov_half_deg,
                            })
                    self.log(f"   Collected {len(camera_samples)} samples from video path.")
                else:
                    self.log("ℹ️ video_animation has no keyframes. Checking static camera ...")

            # Fallback: static camera position
            if not camera_samples:
                if "threejs_camera" in payload and payload["threejs_camera"] is not None:
                    camera = payload["threejs_camera"]
                    pos = camera.get("position", {})
                    cam_x, cam_y = _transform_plan_point_cm(
                        float(pos.get("x", 0.0)),
                        float(pos.get("z", 0.0)),
                        camera_cm_per_unit,
                        plan_rotation_radians,
                        plan_pivot_cm,
                    )
                    dir_x, dir_y = 1.0, 0.0
                    target = camera.get("target")
                    if isinstance(target, dict):
                        target_x, target_y = _transform_plan_point_cm(
                            float(target.get("x", pos.get("x", 0.0))),
                            float(target.get("z", pos.get("z", 0.0))),
                            camera_cm_per_unit,
                            plan_rotation_radians,
                            plan_pivot_cm,
                        )
                        dx = target_x - cam_x
                        dy = target_y - cam_y
                        mag = math.hypot(dx, dy)
                        if mag > 1e-6:
                            dir_x, dir_y = dx / mag, dy / mag
                    fov_half_deg = payload_fov_half_deg
                    if fov_half_deg is None:
                        fov_half_deg = float(
                            camera.get("fov", DEFAULT_FOV_HALF_DEG * 2.0)
                        ) / 2.0
                    camera_samples.append({
                        "cam_x": cam_x,
                        "cam_y": cam_y,
                        "cam_dir_x": dir_x,
                        "cam_dir_y": dir_y,
                        "fov_half_deg": fov_half_deg,
                    })
                    self.log(f"📍 Using static ThreeJS camera: ({cam_x:.1f}, {cam_y:.1f})")
                else:
                    self.log("⚠️ No camera data found. Skipping optimization.")
                    return payload

            # ── 3. Resolve layer/flat structure ───────────────────────────────
            layers           = fp_data.get("layers", {})
            is_flat          = False
            target_layer_id  = None
            layer            = None

            if layers:
                selected_layer_id = fp_data.get("selectedLayer", "layer-1")
                target_layer_id = _pick_primary_layer_id(layers, selected_layer_id)
                layer = layers.get(target_layer_id) if target_layer_id else None
                if not layer:
                    self.log("⚠️ Layered floor_plan_data found, but no usable layer could be selected.")
                    return payload

                # Match the old single-floor video flow: once we isolate one layer
                # for render, place it at ground level so it does not float at its
                # original building altitude.
                if not bool(fp_data.get("showAllFloors", True)):
                    original_altitude = layer.get("altitude", 0)
                    if isinstance(original_altitude, dict):
                        original_altitude = original_altitude.get("length", 0)
                    try:
                        original_altitude_value = float(original_altitude)
                    except Exception:
                        original_altitude_value = 0.0
                    if abs(original_altitude_value) > 1e-6:
                        self.log(
                            f"ℹ️ Flattening selected layer '{layer.get('name', target_layer_id)}' "
                            f"altitude {original_altitude_value} to 0 for video render."
                        )
                    layer["altitude"] = 0

                vertices = layer.get("vertices", {})
                lines    = layer.get("lines",    {})
                areas    = layer.get("areas",    {})
                items    = layer.get("items",    {})
                holes    = layer.get("holes",    {})
                cull_vertices = {
                    vid: _transform_plan_point_cm(
                        float(v.get("x", 0.0)),
                        float(v.get("y", 0.0)),
                        cm_per_unit,
                        plan_rotation_radians,
                        plan_pivot_cm,
                    )
                    for vid, v in vertices.items()
                    if isinstance(v, dict)
                }
            else:
                is_flat = True
                self.log("ℹ️ No 'layers' key found. Trying flat structure...")

                def list_to_dict(obj):
                    if isinstance(obj, list):
                        return {i.get("id"): i for i in obj if isinstance(i, dict) and i.get("id")}
                    return obj if isinstance(obj, dict) else {}

                vertices = list_to_dict(fp_data.get("vertices", {}))
                lines    = list_to_dict(fp_data.get("lines",    {}))
                areas    = list_to_dict(fp_data.get("areas",    {}))
                items    = list_to_dict(fp_data.get("items",    {}))
                holes    = list_to_dict(fp_data.get("holes",    {}))
                cull_vertices = {
                    vid: _transform_plan_point_cm(
                        float(v.get("x", 0.0)),
                        float(v.get("y", 0.0)),
                        cm_per_unit,
                        plan_rotation_radians,
                        plan_pivot_cm,
                    )
                    for vid, v in vertices.items()
                    if isinstance(v, dict)
                }

                if not vertices and not areas:
                    self.log("⚠️ No vertices or areas found. Skipping.")
                    return payload

            self.log(f"\n{'─'*60}")
            self.log(f"📊 Scene Statistics:")
            self.log(f"{'─'*60}")
            self.log(f"  Areas:    {len(areas)}")
            self.log(f"  Lines:    {len(lines)}")
            self.log(f"  Items:    {len(items)}")
            self.log(f"  Holes:    {len(holes)}")
            self.log(f"  Vertices: {len(vertices)}")
            self.log(f"  Camera path samples: {len(camera_samples)}")

            # ── 4. Pre-compute room bounding boxes ────────────────────────────
            area_bboxes: Dict[str, Any] = {}
            for area_id, area in areas.items():
                poly_verts = []
                for v_id in area.get("vertices", []):
                    v = cull_vertices.get(str(v_id), cull_vertices.get(v_id))
                    if v:
                        poly_verts.append((float(v[0]), float(v[1])) if isinstance(v, tuple) else (float(v.get("x", 0)), float(v.get("y", 0))))
                if poly_verts:
                    xs = [p[0] for p in poly_verts]
                    ys = [p[1] for p in poly_verts]
                    area_bboxes[area_id] = (min(xs), max(xs), min(ys), max(ys), poly_verts)

            # ── 5. Determine active rooms from camera path ────────────────────
            active_area_ids: Set[str] = set()
            active_samples: List[Dict[str, Any]] = []
            self.log(f"\n   Checking {len(camera_samples)} path samples against {len(area_bboxes)} rooms...")

            for i, sample in enumerate(camera_samples):
                cx = float(sample["cam_x"])
                cy = float(sample["cam_y"])
                for area_id, (min_x, max_x, min_y, max_y, poly_verts) in area_bboxes.items():
                    if min_x <= cx <= max_x and min_y <= cy <= max_y:
                        if _point_in_polygon(cx, cy, poly_verts):
                            sample["active_area_id"] = area_id
                            active_samples.append(sample)
                            if area_id not in active_area_ids:
                                active_area_ids.add(area_id)
                                self.log(
                                    f"   ✅ Path Point {i} ({cx:.1f}, {cy:.1f}) inside: "
                                    f"'{areas[area_id].get('name')}' ({area_id})"
                                )
                            break

            # ── 6. Branch: Interior vs Exterior ──────────────────────────────
            if active_area_ids:
                render_mode = "INTERIOR"
                self.log(
                    f"\n  🏠 MODE: INTERIOR VIDEO RENDER "
                    f"(Camera enters {len(active_area_ids)} room(s) along path)"
                )
                for aid in active_area_ids:
                    self.log(f"     ✓ {areas[aid].get('name', 'Unknown')} ({aid})")

                new_vertices, new_lines, new_areas, new_items, new_holes = _cull_interior_video(
                    active_area_ids,
                    active_samples,
                    vertices,
                    lines,
                    areas,
                    items,
                    holes,
                    cm_per_unit,
                    plan_rotation_radians,
                    plan_pivot_cm,
                    self.log,
                    max_view_dist_cm=max_view_dist_cm,
                )
            else:
                render_mode = "EXTERIOR"
                self.log(
                    f"\n  🌍 MODE: EXTERIOR VIDEO RENDER "
                    f"(Camera entirely outside building — windows will render BLACK)"
                )
                new_vertices, new_lines, new_areas, new_items, new_holes = _cull_exterior_video(
                    vertices,
                    lines,
                    areas,
                    items,
                    holes,
                    cm_per_unit,
                    plan_rotation_radians,
                    plan_pivot_cm,
                    self.log,
                )

            # ── 7. Summary ────────────────────────────────────────────────────
            self.log(f"\n{'='*60}")
            self.log(f"📊 VIDEO CULLING SUMMARY  [{render_mode}]")
            self.log(f"{'='*60}")
            self.log(f"  Render Mode: {render_mode}")
            self.log(f"  Camera path samples analysed: {len(camera_samples)}")
            if render_mode == "INTERIOR":
                self.log(f"  Active rooms (camera enters): {len(active_area_ids)}")
            self.log(f"  Areas:  {len(areas)} → {len(new_areas)}  (removed {len(areas) - len(new_areas)})")
            self.log(f"  Items:  {len(items)} → {len(new_items)}  (removed {len(items) - len(new_items)})")
            self.log(f"  Lines:  {len(lines)} → {len(new_lines)}  (removed {len(lines) - len(new_lines)})")
            self.log(f"  Holes:  {len(holes)} → {len(new_holes)}  (removed {len(holes) - len(new_holes)})")
            if render_mode == "EXTERIOR":
                self.log("  ⚫ Window/door openings will render as BLACK (no interior backdrop)")
            self.log(f"{'='*60}")

            # ── 8. Reconstruct data structure ─────────────────────────────────
            if not is_flat and layer is not None:
                layer["vertices"]    = new_vertices
                layer["lines"]       = new_lines
                layer["areas"]       = new_areas
                layer["items"]       = new_items
                layer["holes"]       = new_holes
                layer["render_mode"] = render_mode  # Pass to Godot renderer
            else:
                def dict_to_list_if_was_list(key, new_dict):
                    return (
                        list(new_dict.values())
                        if isinstance(fp_data.get(key), list)
                        else new_dict
                    )
                fp_data["vertices"] = dict_to_list_if_was_list("vertices", new_vertices)
                fp_data["lines"]    = dict_to_list_if_was_list("lines",    new_lines)
                fp_data["areas"]    = dict_to_list_if_was_list("areas",    new_areas)
                fp_data["items"]    = dict_to_list_if_was_list("items",    new_items)
                fp_data["holes"]    = dict_to_list_if_was_list("holes",    new_holes)

            # ── 9. Return new payload ─────────────────────────────────────────
            new_payload = payload.copy()
            new_payload["floor_plan_data"] = (
                fp_data if isinstance(fp_str, dict) else json.dumps(fp_data)
            )
            return new_payload

        except Exception as exc:
            self.log(f"❌ Error during video scene culling: {exc}")
            import traceback
            traceback.print_exc()
            return payload  # Fail-safe: return original
