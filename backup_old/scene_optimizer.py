import json
import math
import os
import time
from typing import Dict, List, Any, Set, Tuple, Optional

# Configuration
# Plan coordinates are in CM. Blender/Camera coordinates are in Meters.
SCALE_METERS_TO_CM = 100.0

# Culling logs folder
CULLING_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "culling_logs")
os.makedirs(CULLING_LOGS_DIR, exist_ok=True)

# Floor-related items always kept
FLOOR_ITEM_KEYWORDS = ["floor", "slab", "ground", "terrain", "level"]

# Exterior-only items — kept ONLY in EXTERIOR mode, removed in INTERIOR mode
# These are identified by name/type keywords (case-insensitive substring match)
EXTERIOR_ITEM_KEYWORDS = [
    "roof", "grill", "balcony", "exterior", "facade", "chimney", "antenna", 
    "gutter", "downspout", "tree", "plant", "garden", "pool", "outdoor", "landscape", 
    "terrain", "road", "street", "car"
]

# Item proximity rescue tolerance (cm) — catches items slightly outside polygon
ITEM_RESCUE_TOLERANCE_CM = 85.0

# Tolerance for matching vertex positions between rooms (cm)
POSITION_TOLERANCE_CM = 1.0

# ── Interior precision-culling constants ──────────────────────────────────────
# Default FOV half-angle.  Read from blender_camera.lens / threejs_camera.fov
# when available.  30° = 60° full FOV, typical architectural camera.
DEFAULT_FOV_HALF_DEG = 30.0

# Maximum sightline distance (cm).  Rooms whose portals (door/window centres)
# are farther than this are never created.  30 m covers most indoor sightlines.
DEFAULT_VIEW_DISTANCE_CM = 4500.0

# Portal angle margin added on top of the camera FOV half-angle.
# A door that sits just outside the nominal FOV can still be partially visible
# if it is wide.  5° extra prevents edge-portal pop-in.
PORTAL_ANGLE_MARGIN_DEG = 5.0

# A portal must have at least this fraction of its width visible inside the
# FOV cone for the room behind it to be created (0.0 = any overlap keeps it).
PORTAL_VISIBLE_FRACTION_MIN = 0.0


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
                poly_verts.append((v["x"], v["y"]))
        if poly_verts and _point_in_polygon(x, y, poly_verts):
            return True
    return False


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


# ─────────────────────────────────────────────────────────────────────────────
# PORTAL VISIBILITY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _angle_to_point(cam_x: float, cam_y: float,
                    dir_x: float, dir_y: float,
                    px: float, py: float) -> float:
    """Signed angle (radians) from camera forward direction to point (px,py).
    Returns float('inf') if point is behind camera (dist ~ 0)."""
    dx, dy = px - cam_x, py - cam_y
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return 0.0
    cos_a = max(-1.0, min(1.0, (dx * dir_x + dy * dir_y) / dist))
    return math.acos(cos_a)


def _portal_visible_in_fov(
    cam_x: float, cam_y: float,
    dir_x: float, dir_y: float,
    fov_half_rad: float,
    p1x: float, p1y: float,
    p2x: float, p2y: float,
    n_samples: int = 5,
) -> bool:
    """
    Returns True if ANY part of the portal segment [p1→p2] falls inside the
    camera's 2-D view cone (half-angle = fov_half_rad from dir).

    Samples n_samples evenly spaced points along the portal width and tests
    each.  This handles wide doorways correctly — a door that straddles the
    FOV edge is still detected.
    """
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
) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """
    Compute the world (plan) endpoints of a portal (hole) on a wall (line).

    Returns ((x1,y1),(x2,y2)) — the two corners of the door/window opening
    in plan-space (cm), or None if the wall vertices are missing.

    The portal runs along the wall direction, centred at the hole offset,
    spanning ±half_width either side.
    """
    v_ids = line.get("vertices", [])
    if len(v_ids) < 2:
        return None
    v1 = vertices.get(str(v_ids[0]))
    v2 = vertices.get(str(v_ids[1]))
    if not v1 or not v2:
        return None

    ax, ay = float(v1["x"]), float(v1["y"])
    bx, by = float(v2["x"]), float(v2["y"])
    wall_len = math.hypot(bx - ax, by - ay)
    if wall_len < 1e-6:
        return None

    # Unit vector along wall
    ux = (bx - ax) / wall_len
    uy = (by - ay) / wall_len

    # Portal centre position along wall
    offset = float(hole.get("offset", 0.5))
    cx = ax + offset * (bx - ax)
    cy = ay + offset * (by - ay)

    # Portal half-width — try properties dict first, then top-level
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
    """
    Given a wall (line) that has a portal visible to the camera, return the
    set of area IDs that lie on the OTHER side of that wall from the active room.

    Strategy: collect all areas whose vertex list shares any vertex with this
    wall.  Exclude the active room itself.
    """
    v_ids = [str(v) for v in line.get("vertices", [])[:2]]
    if len(v_ids) < 2:
        return set()

    # Build position set for the two wall vertices (for loose matching)
    wall_positions: Set[Tuple] = set()
    for vid in v_ids:
        v = vertices.get(vid)
        if v:
            wall_positions.add((round(float(v["x"]), 0), round(float(v["y"]), 0)))

    beyond: Set[str] = set()
    for aid, area in areas.items():
        if aid == active_area_id:
            continue
        area_verts = [str(v) for v in area.get("vertices", [])]
        # Direct vertex-ID match
        if any(av in v_ids for av in area_verts):
            beyond.add(aid)
            continue
        # Position-based match (handles id aliasing after culling)
        for av in area_verts:
            av_data = vertices.get(av)
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
    cam_x: float,
    cam_y: float,
    dir_x: float,
    dir_y: float,
    fov_half_rad: float,
    max_dist_cm: float,
    log_fn,
) -> Set[str]:
    """
    Finds all rooms that should be created in INTERIOR mode by doing
    PORTAL-based visibility from the active room outward.

    Algorithm (single-depth — only rooms directly visible through openings
    in the active room's walls):

      For each wall that belongs to the active room:
        For each hole (door/window) on that wall:
          1. Compute the portal's world endpoints (width-aware, not just centre)
          2. Check if ANY part of the portal falls within the camera FOV
          3. Check if the portal centre is within max_dist_cm of the camera
          4. If visible → find the room(s) on the other side → add to keep set

    Returns set of area IDs to keep (NOT including active_area_id — caller adds it).
    """
    log_fn(f"  🔭 Portal-visibility sweep from active room {active_area_id} ...")

    # Collect the active room's vertex IDs
    active_verts: Set[str] = set(
        str(v) for v in areas[active_area_id].get("vertices", [])
    )

    visible_rooms: Set[str] = set()
    portal_margin_rad = math.radians(PORTAL_ANGLE_MARGIN_DEG)
    effective_fov = fov_half_rad + portal_margin_rad

    for lid, line in lines.items():
        v_ids = [str(v) for v in line.get("vertices", [])[:2]]
        if len(v_ids) < 2:
            continue

        # Wall must touch active room
        if not any(vid in active_verts for vid in v_ids):
            continue

        line_holes = line.get("holes", [])
        if not line_holes:
            continue

        for hole_id in line_holes:
            hid = str(hole_id)
            hole = holes.get(hid)
            if not hole:
                continue

            # Portal endpoints (width-aware)
            endpoints = _portal_world_endpoints(line, hole, vertices)
            if not endpoints:
                continue
            (ep1x, ep1y), (ep2x, ep2y) = endpoints
            portal_cx = (ep1x + ep2x) / 2.0
            portal_cy = (ep1y + ep2y) / 2.0

            # Distance check
            dist = math.hypot(portal_cx - cam_x, portal_cy - cam_y)
            if dist > max_dist_cm:
                log_fn(
                    f"    ↩ Portal '{hole.get('name', hid)}' on wall {lid} — "
                    f"dist {dist:.0f}cm > max {max_dist_cm:.0f}cm → skip"
                )
                continue

            # FOV check — sample across full portal width
            if not _portal_visible_in_fov(
                cam_x, cam_y, dir_x, dir_y, effective_fov,
                ep1x, ep1y, ep2x, ep2y
            ):
                portal_angle = math.degrees(_angle_to_point(
                    cam_x, cam_y, dir_x, dir_y, portal_cx, portal_cy
                ))
                log_fn(
                    f"    ↩ Portal '{hole.get('name', hid)}' angle={portal_angle:.1f}° "
                    f"> FOV ±{math.degrees(effective_fov):.0f}° → culled"
                )
                continue

            # Portal IS visible — find rooms beyond
            beyond = _rooms_beyond_portal(line, active_area_id, areas, vertices)
            portal_angle = math.degrees(_angle_to_point(
                cam_x, cam_y, dir_x, dir_y, portal_cx, portal_cy
            ))
            if beyond:
                log_fn(
                    f"    ✅ Portal '{hole.get('name', hid)}' on wall {lid} "
                    f"angle={portal_angle:.1f}° dist={dist:.0f}cm → "
                    f"reveal: {[areas[a].get('name', a) for a in beyond if a in areas]}"
                )
                visible_rooms.update(beyond)
            else:
                log_fn(
                    f"    ⚠️  Portal '{hole.get('name', hid)}' on wall {lid} "
                    f"angle={portal_angle:.1f}° — no room found beyond"
                )

    return visible_rooms


# ─────────────────────────────────────────────────────────────────────────────
# BFS FLOOD-FILL  (kept for exterior / top-view modes)
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
      (b) Walls that contain holes (door/window openings) or are hidden/invisible
          — these mark passable boundaries between rooms
    Returns the full set of kept area IDs.
    """
    # Pre-compute each area's vertex positions for fast neighbour checks
    area_positions: Dict[str, List[Tuple[float, float]]] = {}
    for a_id, area in areas.items():
        positions = []
        for v_id in area.get("vertices", []):
            v = vertices.get(v_id)
            if v:
                positions.append((v["x"], v["y"]))
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
            line_holes = line.get("holes", [])
            line_hidden = line.get("visible") is False
            if not line_holes and not line_hidden:
                continue

            v_ids = line.get("vertices", [])
            if len(v_ids) < 2:
                continue
            v1, v2 = v_ids[0], v_ids[1]

            v1_pos = (vertices[v1]["x"], vertices[v1]["y"]) if v1 in vertices else None
            v2_pos = (vertices[v2]["x"], vertices[v2]["y"]) if v2 in vertices else None

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
# INTERIOR CULLING
# ─────────────────────────────────────────────────────────────────────────────

def _is_exterior_asset(item: dict) -> bool:
    """Check if an item is an exterior-only asset by its name/type keywords."""
    item_type = str(item.get("type", "")).lower()
    item_name = str(item.get("name", "")).lower()
    full_desc = item_type + " " + item_name
    return any(kw in full_desc for kw in EXTERIOR_ITEM_KEYWORDS)


def _cull_interior(
    layer_id: str,
    active_area_id: str,
    vertices: dict,
    lines: dict,
    areas: dict,
    items: dict,
    holes: dict,
    log_fn,
    cam_x: float = 0.0,
    cam_y: float = 0.0,
    cam_dir_x: float = 1.0,
    cam_dir_y: float = 0.0,
    fov_half_deg: float = DEFAULT_FOV_HALF_DEG,
    max_view_dist_cm: float = DEFAULT_VIEW_DISTANCE_CM,
) -> Tuple[dict, dict, dict, dict, dict]:
    """
    INTERIOR MODE — camera is inside a room.  Portal-based precision culling.

    ┌─────────────────────────────────────────────────────────────────────┐
    │  RULE: Only create what the camera can literally see through         │
    │  the geometry.                                                       │
    │                                                                      │
    │  • Active room  →  ALWAYS created (camera is inside it)             │
    │  • Any other room  →  created ONLY if there is a door/window        │
    │    opening in the active room's walls whose opening falls inside     │
    │    the camera FOV and is within max_view_dist_cm                     │
    │  • No opening in FOV  →  only the active room is created            │
    │  • No openings at all  →  only the active room is created           │
    └─────────────────────────────────────────────────────────────────────┘

    This replaces the old BFS flood-fill which kept ALL connected rooms
    regardless of camera direction, generating far too much geometry.

    Keep:
      • Active room walls, floor, ceiling, items
      • ONLY the rooms directly behind portals (doors/windows) that are
        inside the camera's FOV cone
      • Walls shared between active room and a kept neighbour room
      • Holes (door/window assets) on kept walls
      • Items inside kept rooms + 85 cm rescue tolerance

    Remove:
      • Every room not directly visible through an in-FOV portal
      • All walls, holes, items belonging to removed rooms
      • Exterior-only items (roof, grill, balcony, etc.)
    """
    log_fn(f"  ── Interior Culling for {layer_id} [PORTAL MODE] ──")
    log_fn(
        f"  📷 Pos=({cam_x:.0f},{cam_y:.0f})cm  "
        f"Dir=({cam_dir_x:.3f},{cam_dir_y:.3f})  "
        f"FOV=±{fov_half_deg:.0f}°+{PORTAL_ANGLE_MARGIN_DEG:.0f}°margin  "
        f"MaxDist={max_view_dist_cm:.0f}cm"
    )

    fov_half_rad = math.radians(fov_half_deg)

    # ── Step 1: Portal sweep — find rooms visible through active room's openings ─
    neighbour_ids = _collect_portal_visible_rooms(
        active_area_id, areas, lines, holes, vertices,
        cam_x, cam_y, cam_dir_x, cam_dir_y,
        fov_half_rad, max_view_dist_cm,
        log_fn,
    )

    # Active room is ALWAYS kept
    kept_area_ids: Set[str] = {active_area_id} | neighbour_ids

    log_fn(f"  🎯 Kept rooms on {layer_id}: {len(kept_area_ids)} "
           f"(active + {len(neighbour_ids)} portal-visible)")
    for aid in kept_area_ids:
        tag = "📍ACTIVE" if aid == active_area_id else "🚪PORTAL"
        log_fn(f"     {tag} {areas[aid].get('name', 'Unknown')} ({aid})")

    culled_room_count = len(areas) - len(kept_area_ids)
    if culled_room_count > 0:
        log_fn(f"  🗑️  Culled {culled_room_count} rooms not visible from camera")

    # ── Step 2: Filter areas ─────────────────────────────────────────────────
    new_areas = {aid: areas[aid] for aid in kept_area_ids}

    # ── Step 3: Compute kept vertex set ──────────────────────────────────────
    kept_vertex_ids: Set[str] = set()
    for area in new_areas.values():
        kept_vertex_ids.update(str(v) for v in area.get("vertices", []))

    kept_positions: Set[Tuple] = set()
    for vid in kept_vertex_ids:
        v = vertices.get(vid)
        if v:
            kept_positions.add((round(float(v["x"]), 0), round(float(v["y"]), 0)))

    def vertex_is_near_kept(vid: str) -> bool:
        if str(vid) in kept_vertex_ids:
            return True
        v = vertices.get(str(vid))
        if v:
            return (round(float(v["x"]), 0), round(float(v["y"]), 0)) in kept_positions
        return False

    new_vertices = {vid: vertices[vid] for vid in kept_vertex_ids if vid in vertices}

    # ── Step 4: Filter lines (walls) ─────────────────────────────────────────
    # Aggressive culling: even if a wall belongs to a kept room, we cull it 
    # if it's behind the camera or out of range.
    new_lines: Dict[str, Any] = {}
    culled_walls: List[str] = []
    
    # Use a slightly wider margin for walls than for items to ensure corners 
    # and door frames don't clip at the edges of the view.
    wall_fov_margin_rad = math.radians(fov_half_deg + 20.0)

    for lid, line in lines.items():
        v_ids = line.get("vertices", [])
        if len(v_ids) < 2:
            continue
        v1h, v2h = str(v_ids[0]), str(v_ids[1])
        
        # Check if wall touches a kept room
        if not (vertex_is_near_kept(v1h) and vertex_is_near_kept(v2h)):
            culled_walls.append(f"Wall {lid} [not touching kept rooms]")
            continue
            
        # Get vertex positions
        vd1 = vertices.get(v1h)
        vd2 = vertices.get(v2h)
        if not vd1 or not vd2:
            continue
            
        p1x, p1y = float(vd1["x"]), float(vd1["y"])
        p2x, p2y = float(vd2["x"]), float(vd2["y"])
        
        # Midpoint for distance check
        mx, my = (p1x + p2x) / 2.0, (p1y + p2y) / 2.0
        dist = math.hypot(mx - cam_x, my - cam_y)
        
        if dist > max_view_dist_cm:
            culled_walls.append(f"Wall {lid} [dist {dist:.0f}cm > {max_view_dist_cm:.0f}cm]")
            continue

        # FOV check using segment-sampling helper
        if not _portal_visible_in_fov(
            cam_x, cam_y, cam_dir_x, cam_dir_y, 
            wall_fov_margin_rad,
            p1x, p1y, p2x, p2y
        ):
            angle_deg = math.degrees(_angle_to_point(cam_x, cam_y, cam_dir_x, cam_dir_y, mx, my))
            culled_walls.append(f"Wall {lid} [outside FOV sector: {angle_deg:.1f}°]")
            continue

        # Wall IS visible and connected
        new_lines[lid] = line
        for vid in (v1h, v2h):
            if vid not in new_vertices and vid in vertices:
                new_vertices[vid] = vertices[vid]
                kept_vertex_ids.add(vid)

    if culled_walls:
        log_fn(f"\n  🗑️  Culled {len(culled_walls)} walls from {layer_id}:")
        for cw in culled_walls:
            log_fn(f"     - {cw}")

    # ── Step 5: Filter holes ─────────────────────────────────────────────────
    new_holes: Dict[str, Any] = {}
    culled_holes_count = 0
    for hid, hole in holes.items():
        line_ref = str(hole.get("line"))
        if line_ref in new_lines:
            new_holes[hid] = hole
        else:
            culled_holes_count += 1
    
    if culled_holes_count:
        log_fn(f"  🗑️  Culled {culled_holes_count} holes (doors/windows) due to wall removal.")

    # ── Step 6: Filter items ─────────────────────────────────────────────────
    new_items: Dict[str, Any] = {}
    culled_items: List[str] = []

    for iid, item in items.items():
        item_type = str(item.get("type", "")).lower()
        item_name = str(item.get("name", "")).lower()

        # Always keep floor/slab assets regardless of room
        if any(kw in item_type or kw in item_name for kw in FLOOR_ITEM_KEYWORDS):
            new_items[iid] = item
            log_fn(f"  🛡️  Floor asset kept: '{item.get('name')}' ({iid})")
            continue

        # Remove exterior-only assets unconditionally in interior mode
        if _is_exterior_asset(item):
            culled_items.append(f"{item.get('name', 'Unknown')} ({iid}) [exterior]")
            continue

        ix, iy = float(item.get("x", 0)), float(item.get("y", 0))

        # ── Distance & FOV Culling (30m Range & Camera View) ──
        # Only keep assets that are within the 30m range AND generally in front 
        # of the camera (within FOV + safety margin).
        dist = math.hypot(ix - cam_x, iy - cam_y)
        angle_rad = _angle_to_point(cam_x, cam_y, cam_dir_x, cam_dir_y, ix, iy)
        angle_deg = math.degrees(angle_rad)
        
        # We use a generous margin (e.g. 15°) beyond the actual FOV half-angle
        # to ensure side-assets are kept for peripheral vision/reflections.
        fov_limit = fov_half_deg + 15.0 

        if dist > max_view_dist_cm:
            culled_items.append(f"{item.get('name', 'Unknown')} ({iid}) [out of {max_view_dist_cm/100:.0f}m range: {dist:.0f}cm]")
            continue
            
        if angle_deg > fov_limit:
            culled_items.append(f"{item.get('name', 'Unknown')} ({iid}) [outside FOV sector: {angle_deg:.1f}°]")
            continue

        # Pass 1 — strict polygon containment
        kept = False
        for aid in kept_area_ids:
            area = areas.get(aid)
            if not area:
                continue
            poly = [(float(vertices[str(v)]["x"]), float(vertices[str(v)]["y"]))
                    for v in area.get("vertices", []) if str(v) in vertices]
            if poly and _point_in_polygon(ix, iy, poly):
                kept = True
                log_fn(f"  ✓ Item in room: '{item.get('name', 'Unknown')}' ({iid})")
                break

        if kept:
            new_items[iid] = item
            continue

        # Pass 2 — proximity rescue (items clipped into walls, ≤85 cm)
        rescued = False
        for aid in kept_area_ids:
            area = areas.get(aid)
            if not area:
                continue
            poly = [(float(vertices[str(v)]["x"]), float(vertices[str(v)]["y"]))
                    for v in area.get("vertices", []) if str(v) in vertices]
            if not poly:
                continue
            d = _min_dist_to_polygon(ix, iy, poly)
            if d <= ITEM_RESCUE_TOLERANCE_CM:
                log_fn(f"  ✨ Rescue item '{item.get('name', 'Unknown')}' ({iid}) dist={d:.0f}cm")
                new_items[iid] = item
                rescued = True
                break

        if not rescued:
            culled_items.append(f"{item.get('name', 'Unknown')} ({iid})")

    if culled_items:
        log_fn(f"\n  🗑️  Culled {len(culled_items)} items from {layer_id}:")
        for ci in culled_items:
            log_fn(f"     - {ci}")

    return new_vertices, new_lines, new_areas, new_items, new_holes


# ─────────────────────────────────────────────────────────────────────────────
# EXTERIOR CULLING
# ─────────────────────────────────────────────────────────────────────────────

def _cull_exterior(
    layer_id: str,
    vertices: dict,
    lines: dict,
    areas: dict,
    items: dict,
    holes: dict,
    log_fn,
) -> Tuple[dict, dict, dict, dict, dict]:
    """
    EXTERIOR MODE — camera is completely outside all rooms.

    Keep:
      • ALL walls (building shell visible from outside)
      • ALL areas (floor/ceiling geometry needed for structural mesh)
      • ALL holes (doors/windows) — marked with is_exterior_black=true
        so the renderer fills openings with BLACK (no interior visible)
      • Exterior-named items (roof, grill, balcony, etc.) — by keyword match
      • Items physically OUTSIDE all room polygons (garden, trees, etc.)
      • Floor assets always

    Remove:
      • ALL interior items that sit INSIDE any room polygon
        (sofas, fridges, lights, frames, pictures, mirrors, etc.)
      • Wall fittings are NOT preserved — in exterior mode, nothing inside
        the building should be visible through the black windows

    All holes (doors/windows/openings) are flagged is_exterior_black=true
    so the Godot renderer places a black blocker in the opening. This is
    purely geometry-based — no name matching is used for holes.
    """
    log_fn(f"  ── Exterior Culling for {layer_id} ──")

    # Keep all architectural geometry
    new_areas    = dict(areas)
    new_lines    = dict(lines)
    new_vertices = dict(vertices)

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

    # ── Filter items ─────────────────────────────────────────────────────────
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
        ix, iy    = item.get("x", 0), item.get("y", 0)

        # Always keep floor/slab assets
        if any(kw in item_type or kw in item_name for kw in FLOOR_ITEM_KEYWORDS):
            new_items[iid] = item
            log_fn(f"  🛡️ Preserving Floor Asset: '{item.get('name')}' ({iid})")
            continue

        # Keep exterior-named assets (roof, grill, balcony, etc.) — by keyword
        if _is_exterior_asset(item):
            new_items[iid] = item
            kept_exterior_items.append(f"{item.get('name', 'Unknown')} ({iid}) [exterior keyword]")
            log_fn(
                f"  🏠 Keeping exterior-named asset: "
                f"'{item.get('name', 'Unknown')}' ({iid})"
            )
            continue

        # Item outside all room polygons → external landscaping → keep
        is_outside = not _point_in_any_area(ix, iy, areas, vertices)
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
        log_fn(f"\n  🗑️  Culled {len(culled_items)} interior items from {layer_id}:")
        for ci in culled_items:
            log_fn(f"     - {ci}")

    return new_vertices, new_lines, new_areas, new_items, new_holes


# ─────────────────────────────────────────────────────────────────────────────
# TOP-VIEW CULLING  (show_all=False — preserve interior assets)
# ─────────────────────────────────────────────────────────────────────────────

def _cull_top_view_keep_interior(
    layer_id: str,
    vertices: dict,
    lines: dict,
    areas: dict,
    items: dict,
    holes: dict,
    log_fn,
) -> Tuple[dict, dict, dict, dict, dict]:
    """
    TOP VIEW MODE with show_all=False.

    Keep:
      • ALL walls, areas, vertices, holes  (full building shell from above)
      • ALL interior items — sofas, fridges, furniture, etc. are NOT removed
      • Exterior-named items (roof, grill, balcony, etc.) are also kept
      • Floor assets always kept

    This mode is used when a top-down / bird's-eye render is requested but
    the caller explicitly wants interior furnishings to remain visible (e.g.
    a floorplan overview render).  No culling of items is performed.
    """
    log_fn(f"  ── Top-View (show_all=False) Culling for {layer_id} ──")

    # Keep all architectural geometry unchanged
    new_areas    = dict(areas)
    new_lines    = dict(lines)
    new_vertices = dict(vertices)

    log_fn(f"  ✓ Keeping ALL {len(new_areas)} areas  (structural mesh)")
    log_fn(f"  ✓ Keeping ALL {len(new_lines)} walls  (building shell)")

    # Keep all holes as-is (no black-blocker flag needed — camera is above)
    new_holes = dict(holes)
    log_fn(f"  ✓ Keeping ALL {len(new_holes)} holes  (no is_exterior_black flag)")

    # Keep ALL items — do not remove interior assets
    new_items = dict(items)
    log_fn(f"  ✓ Keeping ALL {len(new_items)} items  (show_all=False: interior assets preserved)")

    for iid, item in new_items.items():
        log_fn(f"     ✓ Item: '{item.get('name', 'Unknown')}' ({iid})")

    return new_vertices, new_lines, new_areas, new_items, new_holes


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────────────────────────────────────

class SceneOptimizer:
    def __init__(self, log_path: Optional[str] = None):
        """
        Initialize SceneOptimizer.

        Args:
            log_path: Full path for the plain-text culling log (.txt).
                      A matching *_details.json structured log is saved
                      alongside it automatically.
                      If None, uses default path inside culling_logs/.
        """
        if log_path is None:
            log_path = os.path.join(CULLING_LOGS_DIR, "culling_log.txt")

        self.log_path      = log_path
        self.json_log_path = os.path.splitext(log_path)[0] + "_details.json"

        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.write("--- Scene Culling Log ---\n")
        except Exception:
            pass

        self._log_data: Dict[str, Any] = {
            "timestamp":         time.strftime("%Y-%m-%dT%H:%M:%S"),
            "log_txt_file":      self.log_path,
            "log_json_file":     self.json_log_path,
            "camera":            {},
            "render_mode":       "UNKNOWN",
            "show_all_floors":   None,
            "ceiling_decisions": [],
            "layers":            {},
            "summary":           {},
            "errors":            [],
        }

    # ── Logging ───────────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        print(f"[SceneOptimizer] {msg}")
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{msg}\n")
        except Exception:
            pass

    def _save_json_log(self) -> None:
        try:
            with open(self.json_log_path, "w", encoding="utf-8") as f:
                json.dump(self._log_data, f, indent=2, ensure_ascii=False)
            self.log(f"📄 Structured log saved → {self.json_log_path}")
        except Exception as exc:
            self.log(f"⚠️  Could not save JSON log: {exc}")

    # ── Ceiling Visibility ────────────────────────────────────────────────────

    def _apply_ceiling_visibility(
        self,
        fp_data: Dict[str, Any],
        cam_height_m: float,
        show_all_floors: bool,
    ) -> None:
        """
        Tags per-area ceiling visibility in fp_data (in-place) by setting
        ceiling_properties.isvisible.

        showAllFloors=true  → all ceilings stay visible.
        showAllFloors=false → hide ceilings where camera is above them
                              (bird's-eye / exterior renders).
        """
        if show_all_floors:
            self.log("🏠 showAllFloors=true → all ceilings remain visible.")
            return

        self.log(
            f"🏠 showAllFloors=false | Camera height = {cam_height_m:.3f} m → "
            "evaluating per-area ceiling visibility ..."
        )

        for layer_id, layer in fp_data.get("layers", {}).items():
            raw_alt    = layer.get("altitude", 0)
            layer_alt_m = (
                float(raw_alt.get("length", 0)) / 100.0
                if isinstance(raw_alt, dict)
                else float(raw_alt) / 100.0
            )

            for area_id, area in layer.get("areas", {}).items():
                cp        = area.get("ceiling_properties")
                area_name = area.get("name", area_id)

                if cp is None:
                    area["ceiling_properties"] = {"isvisible": False}
                    self._log_data["ceiling_decisions"].append({
                        "layer_id": layer_id, "area_id": area_id,
                        "area_name": area_name, "isvisible": False,
                        "reason": "no ceiling_properties — injected hidden default",
                    })
                    self.log(f"  📐 Area '{area_name}' ({layer_id}) — no ceiling_properties → isvisible=false")
                    continue

                raw_ceil_h = cp.get("height", 0)
                ceil_h_m   = (
                    float(raw_ceil_h.get("length", 0)) / 100.0
                    if isinstance(raw_ceil_h, dict)
                    else float(raw_ceil_h) / 100.0
                )

                fallback_used = False
                if ceil_h_m < 0.1:
                    max_wh = 0.0
                    for line in layer.get("lines", {}).values():
                        wh_raw = line.get("properties", {}).get("height", {})
                        wh = (
                            float(wh_raw.get("length", 0)) / 100.0
                            if isinstance(wh_raw, dict) else float(wh_raw) / 100.0
                        )
                        if wh > max_wh:
                            max_wh = wh
                    ceil_h_m      = max_wh if max_wh > 0.1 else 2.8
                    fallback_used = True

                ceiling_world_y = layer_alt_m + ceil_h_m
                visible         = cam_height_m < ceiling_world_y
                cp["isvisible"] = visible

                reason = (
                    f"camera Y {cam_height_m:.3f} m {'<' if visible else '>='} "
                    f"ceiling Y {ceiling_world_y:.3f} m → {'VISIBLE' if visible else 'HIDDEN'}"
                )
                if fallback_used:
                    reason += "  [ceiling height derived from wall height fallback]"

                self._log_data["ceiling_decisions"].append({
                    "layer_id": layer_id, "area_id": area_id,
                    "area_name": area_name, "isvisible": visible, "reason": reason,
                })
                self.log(f"  {'✅' if visible else '🚫'} Area '{area_name}' ({layer_id}) — {reason}")

    # ── Main Entry Point ──────────────────────────────────────────────────────

    def cull_scene(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Optimise the render payload using three-mode culling:

        INTERIOR (camera inside a room)
        ─────────────────────────────────
        • Keep only the active room + rooms reachable via openings / hidden walls
        • Remove all exterior walls, areas, and items outside kept rooms
        • Textures: interior materials applied normally

        EXTERIOR (camera outside all rooms)
        ─────────────────────────────────────
        • Keep all walls, areas, and holes (building shell)
        • Window/door openings render BLACK — no interior geometry behind them
        • Remove ALL interior furniture items (sofa, fridge, decorations, etc.)
        • Keep only: items outside room polygons + architectural wall fittings
        • Textures: exterior-facing materials applied (handled by renderer)

        TOP VIEW (payload contains is_top_view=true)
        ─────────────────────────────────────────────
        • Camera XY is overridden to the centroid of all home rooms (directly above)
        • All walls, areas, and holes are kept (building shell from above)
        • If show_all=false → interior assets are NOT removed (kept as-is)
        • If show_all=true  → interior items inside rooms are culled (exterior mode)

        Returns a new payload with optimised floor_plan_data.
        Also saves a plain-text log and a structured *_details.json log.
        """
        try:
            # ── 1. Parse floor_plan_data ──────────────────────────────────────
            fp_str = payload.get("floor_plan_data")
            if not fp_str:
                self.log("⚠️ No floor_plan_data found.")
                return payload

            fp_data = json.loads(fp_str) if isinstance(fp_str, str) else fp_str

            # ── 1b. Detect top_view mode ──────────────────────────────────────
            is_top_view = bool(payload.get("is_top_view", False))
            show_all    = bool(payload.get("show_all", False))  # False = isolate camera layer; True = render all layers (top-view override)
            self._log_data["is_top_view"] = is_top_view
            self._log_data["show_all"]    = show_all

            if is_top_view:
                self.log(f"🔭 TOP VIEW mode detected | show_all={show_all}")

            # ── 2. Extract camera position ────────────────────────────────────
            cam_x = cam_y = cam_height_m = 0.0
            cam_source = "none"

            if "blender_camera" in payload and "location" in payload["blender_camera"]:
                loc          = payload["blender_camera"]["location"]
                cam_x        = loc[0] * SCALE_METERS_TO_CM
                cam_y        = -loc[1] * SCALE_METERS_TO_CM   # Blender Y → plan Y (flip)
                cam_height_m = float(loc[2])
                cam_source   = "blender_camera"
                self.log(
                    f"📍 Camera (Blender): {loc} → "
                    f"Plan XZ: ({cam_x:.1f}, {cam_y:.1f}) cm | Height: {cam_height_m:.3f} m"
                )

            elif "threejs_camera" in payload and "position" in payload["threejs_camera"]:
                pos          = payload["threejs_camera"]["position"]
                cam_x        = pos.get("x", 0) * SCALE_METERS_TO_CM
                cam_y        = pos.get("z", 0) * SCALE_METERS_TO_CM   # ThreeJS z → plan y direct
                cam_height_m = float(pos.get("y", 0))
                cam_source   = "threejs_camera"
                self.log(
                    f"📍 Camera (ThreeJS): {pos} → "
                    f"Plan XZ: ({cam_x:.1f}, {cam_y:.1f}) cm | Height: {cam_height_m:.3f} m"
                )

            else:
                self.log("⚠️ No camera data found. Skipping optimization.")
                return payload

            # ── 2b. Extract camera forward direction ──────────────────────────
            # Priority: blender_target.location → threejs_camera.target → rz fallback
            cam_dir_x, cam_dir_y = 1.0, 0.0
            dir_source = "fallback"

            bt = payload.get("blender_target")
            if isinstance(bt, dict):
                bt = bt.get("location")
            if bt and len(bt) >= 2:
                tx = float(bt[0]) * SCALE_METERS_TO_CM
                ty = -float(bt[1]) * SCALE_METERS_TO_CM
                fdx, fdy = tx - cam_x, ty - cam_y
                mag = math.hypot(fdx, fdy)
                if mag > 1e-6:
                    cam_dir_x, cam_dir_y = fdx / mag, fdy / mag
                    dir_source = "blender_target"
            else:
                tj = payload.get("threejs_camera", {})
                tgt = tj.get("target")
                if isinstance(tgt, dict) and "x" in tgt and "z" in tgt:
                    tx = float(tgt["x"]) * SCALE_METERS_TO_CM
                    ty = float(tgt["z"]) * SCALE_METERS_TO_CM
                    fdx, fdy = tx - cam_x, ty - cam_y
                    mag = math.hypot(fdx, fdy)
                    if mag > 1e-6:
                        cam_dir_x, cam_dir_y = fdx / mag, fdy / mag
                        dir_source = "threejs_target"
                else:
                    bc = payload.get("blender_camera", {})
                    rot = bc.get("rotation_euler")
                    if rot and len(rot) >= 3:
                        rz = float(rot[2])
                        fdx, fdy = math.sin(rz), -math.cos(rz)
                        mag = math.hypot(fdx, fdy)
                        if mag > 1e-6:
                            cam_dir_x, cam_dir_y = fdx / mag, fdy / mag
                            dir_source = "rotation_euler"

            self.log(
                f"📐 Camera dir: ({cam_dir_x:.3f},{cam_dir_y:.3f})  "
                f"angle={math.degrees(math.atan2(cam_dir_y, cam_dir_x)):.1f}°  "
                f"source={dir_source}"
            )

            # ── 2c. Derive FOV half-angle ─────────────────────────────────────
            if "interior_fov_half_deg" in payload:
                cam_fov_half_deg = float(payload["interior_fov_half_deg"])
                self.log(f"📐 FOV: payload override ±{cam_fov_half_deg:.0f}°")
            else:
                bc = payload.get("blender_camera", {})
                if bc.get("lens_unit") == "FOV" and bc.get("lens"):
                    cam_fov_half_deg = float(bc["lens"]) / 2.0
                    self.log(f"📐 FOV: blender lens={bc['lens']}° → ±{cam_fov_half_deg:.0f}°")
                else:
                    tj_fov = payload.get("threejs_camera", {}).get("fov")
                    if tj_fov:
                        cam_fov_half_deg = float(tj_fov) / 2.0
                        self.log(f"📐 FOV: threejs fov={tj_fov}° → ±{cam_fov_half_deg:.0f}°")
                    else:
                        cam_fov_half_deg = DEFAULT_FOV_HALF_DEG
                        self.log(f"📐 FOV: default ±{cam_fov_half_deg:.0f}°")

            cam_view_dist_cm = float(payload.get("interior_view_dist_cm", DEFAULT_VIEW_DISTANCE_CM))

            # ── 2b. Top-view: override camera XY to home centroid ─────────────
            if is_top_view:
                all_xs: List[float] = []
                all_ys: List[float] = []
                for layer in fp_data.get("layers", {}).values():
                    _verts = layer.get("vertices", {})
                    for _area in layer.get("areas", {}).values():
                        for _vid in _area.get("vertices", []):
                            _v = _verts.get(_vid)
                            if _v:
                                all_xs.append(_v["x"])
                                all_ys.append(_v["y"])
                if all_xs and all_ys:
                    orig_cam_x, orig_cam_y = cam_x, cam_y
                    cam_x = (min(all_xs) + max(all_xs)) / 2.0
                    cam_y = (min(all_ys) + max(all_ys)) / 2.0
                    self.log(
                        f"🔭 TOP VIEW: Camera XY overridden from "
                        f"({orig_cam_x:.1f}, {orig_cam_y:.1f}) → centroid "
                        f"({cam_x:.1f}, {cam_y:.1f}) cm  [directly above home]"
                    )
                else:
                    self.log("⚠️ TOP VIEW: No vertices found to compute centroid — camera XY unchanged.")

            self._log_data["camera"] = {
                "source":         cam_source,
                "dir_source":     dir_source,
                "plan_x_cm":      round(cam_x, 2),
                "plan_y_cm":      round(cam_y, 2),
                "height_m":       round(cam_height_m, 4),
                "dir_x":          round(cam_dir_x, 4),
                "dir_y":          round(cam_dir_y, 4),
                "fov_half_deg":   cam_fov_half_deg,
                "view_dist_cm":   cam_view_dist_cm,
                "raw_blender":    payload.get("blender_camera", {}).get("location"),
                "raw_threejs":    payload.get("threejs_camera", {}).get("position"),
                "is_top_view":    is_top_view,
                "show_all":       show_all,
            }

            # ── 3. showAllFloors / ceiling pass ───────────────────────────────
            show_all_floors = fp_data.get("showAllFloors", True)
            self._log_data["show_all_floors"] = show_all_floors
            self.log(f"🏗️  showAllFloors = {show_all_floors}")
            self._apply_ceiling_visibility(fp_data, cam_height_m, show_all_floors)
            keep_all_layers = show_all or show_all_floors

            # ── 4. Per-layer culling ──────────────────────────────────────────
            layers = fp_data.get("layers", {})
            if not layers:
                self.log("⚠️ No layers found in floor plan.")
                new_payload = payload.copy()
                new_payload["floor_plan_data"] = (
                    json.dumps(fp_data) if isinstance(fp_str, str) else fp_data
                )
                self._save_json_log()
                return new_payload

            # ── 4a. Pre-scan: find which layer the camera is in ───────────────
            # If the camera is inside a room in any layer (INTERIOR mode) and
            # show_all is False, we ONLY render that single layer and completely
            # skip all others.  This avoids generating geometry for floors the
            # camera cannot see at all.
            # When show_all=True (TOP_VIEW or explicit override) all layers are
            # processed as usual.
            #
            # PRIORITY: if selectedLayer is present in fp_data and matches an
            # actual layer key (exact or prefix), use it directly — do NOT rely
            # on the spatial scan.  The frontend already knows which layer is
            # active; IDs like "layer-1-1769245501306" are the authoritative
            # selected layer even if the spatial scan would map back to "layer-1".
            camera_layer_id: Optional[str] = None
            _selected_layer_raw: str = str(fp_data.get("selectedLayer", "")).strip()
            if show_all_floors:
                self.log("  🏗️ showAllFloors=true: skipping selectedLayer isolation and spatial pre-scan.")
            elif _selected_layer_raw and not is_top_view:
                # Exact match first
                if _selected_layer_raw in layers:
                    camera_layer_id = _selected_layer_raw
                    self.log(
                        f"  ✅ selectedLayer exact match → camera_layer='{camera_layer_id}'"
                    )
                else:
                    # Prefix match: real key is a prefix of selectedLayer
                    # e.g. selectedLayer="layer-1-1769245501306", key="layer-1"
                    for _lid in layers:
                        if _selected_layer_raw.startswith(str(_lid)):
                            camera_layer_id = _lid
                            self.log(
                                f"  ✅ selectedLayer prefix match: "
                                f"'{_selected_layer_raw}' → camera_layer='{camera_layer_id}'"
                            )
                            break
                    # Reverse prefix: key starts with selectedLayer
                    if not camera_layer_id:
                        for _lid in layers:
                            if str(_lid).startswith(_selected_layer_raw):
                                camera_layer_id = _lid
                                self.log(
                                    f"  ✅ selectedLayer reverse-prefix match: "
                                    f"'{_selected_layer_raw}' → camera_layer='{camera_layer_id}'"
                                )
                                break

            if camera_layer_id:
                self.log(
                    f"  📌 Using selectedLayer='{_selected_layer_raw}' "
                    f"(resolved to '{camera_layer_id}') — skipping spatial pre-scan."
                )

            if not camera_layer_id and not is_top_view and not show_all_floors:
                for _lid, _layer in layers.items():
                    # ── Height check: camera must sit within this layer's
                    # vertical range [altitude .. altitude + wall_height].
                    # This prevents a layer on a different floor from being
                    # matched purely because its 2-D polygon overlaps the
                    # camera's XY position.
                    _raw_alt = _layer.get("altitude", 0)
                    _layer_alt_m = (
                        float(_raw_alt.get("length", 0)) / 100.0
                        if isinstance(_raw_alt, dict)
                        else float(_raw_alt) / 100.0
                    )
                    # Derive floor-to-ceiling height from wall heights
                    _wall_heights = []
                    for _wl in _layer.get("lines", {}).values():
                        _wh = _wl.get("properties", {}).get("height", {})
                        _wh_m = (
                            float(_wh.get("length", 0)) / 100.0
                            if isinstance(_wh, dict)
                            else float(_wh) / 100.0
                        )
                        if _wh_m > 0.1:
                            _wall_heights.append(_wh_m)
                    _floor_height_m = max(_wall_heights) if _wall_heights else 3.0
                    _layer_top_m    = _layer_alt_m + _floor_height_m

                    # Camera height must fall within [layer_alt .. layer_top]
                    if not (_layer_alt_m <= cam_height_m < _layer_top_m):
                        self.log(
                            f"  ↩ Pre-scan skip '{_lid}': cam_height={cam_height_m:.3f}m "
                            f"not in layer Z range [{_layer_alt_m:.3f}m .. {_layer_top_m:.3f}m]"
                        )
                        continue

                    # ── 2-D polygon test within the height-valid layer ────────
                    _verts = _layer.get("vertices", {})
                    for _area in _layer.get("areas", {}).values():
                        _poly = []
                        for _vid in _area.get("vertices", []):
                            _v = _verts.get(_vid)
                            if _v:
                                _poly.append((_v["x"], _v["y"]))
                        if not _poly:
                            continue
                        _xs = [p[0] for p in _poly]
                        _ys = [p[1] for p in _poly]
                        if min(_xs) <= cam_x <= max(_xs) and min(_ys) <= cam_y <= max(_ys):
                            if _point_in_polygon(cam_x, cam_y, _poly):
                                camera_layer_id = _lid
                                self.log(
                                    f"  ✅ Pre-scan: camera height={cam_height_m:.3f}m "
                                    f"∈ layer '{_lid}' Z [{_layer_alt_m:.3f}m .. {_layer_top_m:.3f}m] "
                                    f"AND inside 2-D polygon → camera_layer='{_lid}'"
                                )
                                break
                    if camera_layer_id:
                        break

            if camera_layer_id and not keep_all_layers and not show_all_floors:
                self.log(
                    f"\n🎯 LAYER ISOLATION: Camera is inside layer '{camera_layer_id}' "
                    f"— only this layer will be rendered. All other layers are SKIPPED."
                )
            elif is_top_view:
                self.log(f"\n🔭 TOP VIEW: Processing all {len(layers)} layers.")
            elif show_all:
                self.log(f"\n🌍 show_all=True: Processing all {len(layers)} layers.")
            else:
                self.log(
                    f"\n🌍 EXTERIOR: Camera not found inside any layer — "
                    f"processing all {len(layers)} layers."
                )

            totals = dict(
                areas_before=0, areas_after=0,
                items_before=0, items_after=0,
                lines_before=0, lines_after=0,
                holes_before=0, holes_after=0,
            )
            render_mode = "UNKNOWN"

            for layer_id, layer in layers.items():
                self.log(f"\n{'─'*60}")
                self.log(f"🔍 Inspecting Layer: {layer_id}")
                self.log(f"{'─'*60}")

                # ── LAYER SKIP: camera is in a different layer ────────────────
                # When the camera is placed inside a specific layer (INTERIOR),
                # every other layer is irrelevant — skip it entirely so its
                # geometry is not sent to the renderer at all.
                if camera_layer_id and not keep_all_layers and layer_id != camera_layer_id:
                    self.log(
                        f"  ⏭️  SKIPPED — camera is in layer '{camera_layer_id}', "
                        f"not '{layer_id}'. Removing entire layer from output."
                    )
                    # Blank the layer so the renderer ignores it completely
                    layer["vertices"] = {}
                    layer["lines"]    = {}
                    layer["areas"]    = {}
                    layer["items"]    = {}
                    layer["holes"]    = {}
                    layer["render_mode"] = "SKIPPED"
                    self._log_data["layers"][layer_id] = {
                        "render_mode":  "SKIPPED",
                        "active_room":  None,
                        "reason":       f"camera is in layer '{camera_layer_id}'",
                        "stats_before": {"areas": 0, "lines": 0, "holes": 0, "items": 0},
                        "stats_after":  {"areas": 0, "lines": 0, "holes": 0, "items": 0},
                    }
                    continue

                vertices = layer.get("vertices", {})
                lines    = layer.get("lines",    {})
                areas    = layer.get("areas",    {})
                items    = layer.get("items",    {})
                holes    = layer.get("holes",    {})

                totals["areas_before"] += len(areas)
                totals["items_before"] += len(items)
                totals["lines_before"] += len(lines)
                totals["holes_before"] += len(holes)

                self.log(f"  📊 Layer Statistics:")
                self.log(f"     Areas:    {len(areas)}")
                self.log(f"     Lines:    {len(lines)}")
                self.log(f"     Items:    {len(items)}")
                self.log(f"     Holes:    {len(holes)}")
                self.log(f"     Vertices: {len(vertices)}")

                # ── Determine active room ─────────────────────────────────────
                active_area_id = None

                for area_id, area in areas.items():
                    poly_verts = []
                    for v_id in area.get("vertices", []):
                        v = vertices.get(v_id)
                        if v:
                            poly_verts.append((v["x"], v["y"]))
                    if not poly_verts:
                        continue

                    xs = [p[0] for p in poly_verts]
                    ys = [p[1] for p in poly_verts]
                    if min(xs) <= cam_x <= max(xs) and min(ys) <= cam_y <= max(ys):
                        if _point_in_polygon(cam_x, cam_y, poly_verts):
                            active_area_id = area_id
                            self.log(
                                f"  ✅ Camera ({cam_x:.1f}, {cam_y:.1f}) in Active Room: "
                                f"'{area.get('name')}' ({area_id})"
                            )
                            break
                        else:
                            self.log(
                                f"  ❓ Camera inside bbox of '{area.get('name')}' "
                                "but failed polygon test."
                            )

                # ── Branch: Top-view / Interior / Exterior ────────────────────
                if is_top_view:
                    render_mode = "TOP_VIEW"
                    self.log(f"\n  🔭 MODE: TOP VIEW RENDER (show_all={show_all})")
                    if not show_all:
                        # show_all=False → keep ALL interior assets, only strip
                        # exterior-named items that shouldn't appear in a top-down plan
                        self.log("  📦 show_all=False → interior assets preserved (no culling of interior items)")
                        new_vertices, new_lines, new_areas, new_items, new_holes = \
                            _cull_top_view_keep_interior(
                                layer_id,
                                vertices, lines, areas, items, holes,
                                self.log,
                            )
                    else:
                        # show_all=True → behave like exterior (cull interior items)
                        self.log("  🗑️  show_all=True → interior items culled (exterior-style)")
                        new_vertices, new_lines, new_areas, new_items, new_holes = _cull_exterior(
                            layer_id,
                            vertices, lines, areas, items, holes,
                            self.log,
                        )
                elif active_area_id:
                    render_mode = "INTERIOR"
                    self.log(f"\n  🏠 MODE: INTERIOR RENDER (Camera inside room '{areas[active_area_id].get('name')}')")
                    new_vertices, new_lines, new_areas, new_items, new_holes = _cull_interior(
                        layer_id, active_area_id,
                        vertices, lines, areas, items, holes,
                        self.log,
                        cam_x=cam_x,
                        cam_y=cam_y,
                        cam_dir_x=cam_dir_x,
                        cam_dir_y=cam_dir_y,
                        fov_half_deg=cam_fov_half_deg,
                        max_view_dist_cm=cam_view_dist_cm,
                    )
                else:
                    render_mode = "EXTERIOR"
                    self.log(f"\n  🌍 MODE: EXTERIOR RENDER (Camera outside all rooms — windows will render BLACK)")
                    new_vertices, new_lines, new_areas, new_items, new_holes = _cull_exterior(
                        layer_id,
                        vertices, lines, areas, items, holes,
                        self.log,
                    )

                self.log(f"\n  ✂️  Optimisation Results for {layer_id}:")
                self.log(f"     Areas:    {len(areas)} → {len(new_areas)}  (removed {len(areas) - len(new_areas)})")
                self.log(f"     Lines:    {len(lines)} → {len(new_lines)}  (removed {len(lines) - len(new_lines)})")
                self.log(f"     Holes:    {len(holes)} → {len(new_holes)}  (removed {len(holes) - len(new_holes)})")
                self.log(f"     Items:    {len(items)} → {len(new_items)}  (removed {len(items) - len(new_items)})")
                self.log(f"     Vertices: {len(vertices)} → {len(new_vertices)}")

                totals["areas_after"] += len(new_areas)
                totals["items_after"] += len(new_items)
                totals["lines_after"] += len(new_lines)
                totals["holes_after"] += len(new_holes)

                # Reconstruct layer
                layer["vertices"]    = new_vertices
                layer["lines"]       = new_lines
                layer["areas"]       = new_areas
                layer["items"]       = new_items
                layer["holes"]       = new_holes
                layer["render_mode"] = render_mode  # Pass to Godot renderer

                self._log_data["layers"][layer_id] = {
                    "render_mode":   render_mode,
                    "active_room":   active_area_id,
                    "cam_dir":       (round(cam_dir_x, 4), round(cam_dir_y, 4)) if render_mode == "INTERIOR" else None,
                    "fov_half_deg":  cam_fov_half_deg if render_mode == "INTERIOR" else None,
                    "view_dist_cm":  cam_view_dist_cm if render_mode == "INTERIOR" else None,
                    "stats_before":  {
                        "areas": len(areas), "lines": len(lines),
                        "holes": len(holes), "items": len(items),
                    },
                    "stats_after":   {
                        "areas": len(new_areas), "lines": len(new_lines),
                        "holes": len(new_holes), "items": len(new_items),
                    },
                }

            # ── Summary ───────────────────────────────────────────────────────
            self._log_data["render_mode"] = render_mode
            self._log_data["summary"]     = totals

            self.log(f"\n{'='*60}")
            self.log(f"📊 CULLING SUMMARY  [{render_mode}]")
            self.log(f"{'='*60}")
            self.log(f"  Render Mode: {render_mode}")
            self.log(f"  Camera Plan: ({cam_x:.1f}, {cam_y:.1f}) cm")
            self.log(f"  Areas:  {totals['areas_before']} → {totals['areas_after']}  (removed {totals['areas_before'] - totals['areas_after']})")
            self.log(f"  Items:  {totals['items_before']} → {totals['items_after']}  (removed {totals['items_before'] - totals['items_after']})")
            self.log(f"  Lines:  {totals['lines_before']} → {totals['lines_after']}  (removed {totals['lines_before'] - totals['lines_after']})")
            self.log(f"  Holes:  {totals['holes_before']} → {totals['holes_after']}  (removed {totals['holes_before'] - totals['holes_after']})")
            if render_mode == "EXTERIOR":
                self.log("  ⚫ Window/door openings will render as BLACK (no interior backdrop)")
            if render_mode == "TOP_VIEW":
                self.log(f"  🔭 Top-view render | show_all={show_all} | Camera placed above home centroid")
                if not show_all:
                    self.log("  📦 Interior assets preserved (show_all=False)")
            self.log(f"{'='*60}")

            new_payload = payload.copy()
            new_payload["floor_plan_data"] = (
                json.dumps(fp_data) if isinstance(fp_str, str) else fp_data
            )

            self._save_json_log()
            return new_payload

        except Exception as exc:
            self.log(f"❌ Error during scene culling: {exc}")
            self._log_data["errors"].append(str(exc))
            import traceback
            traceback.print_exc()
            self._save_json_log()
            return payload  # Fail-safe: return original


# ─────────────────────────────────────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Running optimization test...")
    pass
