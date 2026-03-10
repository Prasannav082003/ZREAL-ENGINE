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

# Item proximity rescue tolerance (cm) — catches items slightly outside polygon
ITEM_RESCUE_TOLERANCE_CM = 85.0

# Tolerance for matching vertex positions between rooms (cm)
POSITION_TOLERANCE_CM = 1.0


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

def _cull_interior_video(
    active_area_ids: Set[str],
    vertices: dict,
    lines: dict,
    areas: dict,
    items: dict,
    holes: dict,
    log_fn,
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
    """
    log_fn(f"  ── Interior Culling (Video) ──")

    kept_area_ids = _flood_fill_connected_rooms(
        active_area_ids, areas, lines, vertices, log_fn
    )

    log_fn(f"  🎯 Total kept rooms: {len(kept_area_ids)}")
    for aid in kept_area_ids:
        log_fn(f"     ✓ {areas[aid].get('name', 'Unknown')} ({aid})")

    # ── Areas ────────────────────────────────────────────────────────────────
    new_areas = {aid: areas[aid] for aid in kept_area_ids}

    # ── Vertices ─────────────────────────────────────────────────────────────
    kept_vertex_ids: Set[str] = set()
    for area in new_areas.values():
        kept_vertex_ids.update(area.get("vertices", []))

    kept_positions: Set[Tuple] = set()
    for vid in kept_vertex_ids:
        v = vertices.get(vid)
        if v:
            kept_positions.add((round(v.get("x", 0), 0), round(v.get("y", 0), 0)))

    def vertex_is_near_kept(vid: str) -> bool:
        if vid in kept_vertex_ids:
            return True
        v = vertices.get(vid)
        if v:
            return (round(v.get("x", 0), 0), round(v.get("y", 0), 0)) in kept_positions
        return False

    new_vertices = {vid: vertices[vid] for vid in kept_vertex_ids if vid in vertices}

    # ── Lines ─────────────────────────────────────────────────────────────────
    new_lines: Dict[str, Any] = {}
    for lid, line in lines.items():
        v_ids = line.get("vertices", [])
        if len(v_ids) >= 2:
            v1, v2 = v_ids[0], v_ids[1]
            if vertex_is_near_kept(v1) and vertex_is_near_kept(v2):
                new_lines[lid] = line
                for vid in (v1, v2):
                    if vid not in new_vertices and vid in vertices:
                        new_vertices[vid] = vertices[vid]
                        kept_vertex_ids.add(vid)
            else:
                log_fn(f"  🗑️ Culled wall (exterior): {lid}")

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

        ix, iy = item.get("x", 0), item.get("y", 0)

        # Pass 1 – polygon inclusion
        is_kept = False
        for aid in kept_area_ids:
            area = areas.get(aid)
            if not area:
                continue
            poly_verts = []
            for v_id in area.get("vertices", []):
                v = vertices.get(v_id)
                if v:
                    poly_verts.append((v.get("x", 0), v.get("y", 0)))
            if poly_verts and _point_in_polygon(ix, iy, poly_verts):
                is_kept = True
                break

        if is_kept:
            new_items[iid] = item
            continue

        # Pass 2 – proximity rescue
        rescued = False
        for aid in kept_area_ids:
            area = areas.get(aid)
            if not area:
                continue
            poly_verts = []
            for v_id in area.get("vertices", []):
                v = vertices.get(v_id)
                if v:
                    poly_verts.append((v.get("x", 0), v.get("y", 0)))
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
    log_fn,
) -> Tuple[dict, dict, dict, dict, dict]:
    """
    EXTERIOR MODE — entire camera path is outside all rooms.

    Keep:
      • ALL walls, areas, holes (building shell + openings)
      • Window/door openings appear BLACK — no interior geometry behind them
      • Items physically outside all room polygons (garden, trees, etc.)
      • Floor assets always

    Remove:
      • ALL interior furniture/items inside room polygons
      • Only exception: architectural wall fittings with explicit line/hole refs
    """
    log_fn(f"  ── Exterior Culling (Video) ──")

    # Keep all architectural geometry intact
    new_areas    = dict(areas)
    new_lines    = dict(lines)
    new_holes    = dict(holes)
    new_vertices = dict(vertices)

    log_fn(f"  ✓ Keeping ALL {len(new_areas)} areas  (structural mesh)")
    log_fn(f"  ✓ Keeping ALL {len(new_lines)} walls  (building shell)")
    log_fn(f"  ✓ Keeping ALL {len(new_holes)} holes  (openings → black from outside)")

    for aid, area in new_areas.items():
        log_fn(f"     ✓ Area: {area.get('name', 'Unknown')} ({aid})")
    for hid, hole in new_holes.items():
        log_fn(f"     ✓ Hole ({hole.get('type','?')}): {hole.get('name', hid)}")

    # ── Filter items ──────────────────────────────────────────────────────────
    new_items:           Dict[str, Any] = {}
    culled_items:        List[str]      = []
    kept_exterior_items: List[str]      = []
    kept_wall_items:     List[str]      = []

    for iid, item in items.items():
        item_type = str(item.get("type", "")).lower()
        item_name = str(item.get("name", "")).lower()
        ix, iy    = item.get("x", 0), item.get("y", 0)

        # Always keep floor/slab assets
        if any(kw in item_type or kw in item_name for kw in FLOOR_ITEM_KEYWORDS):
            new_items[iid] = item
            log_fn(f"  🛡️ Preserving Floor Asset: '{item.get('name')}' ({iid})")
            continue

        # Item outside all room polygons → external → keep
        is_outside = not _point_in_any_area(ix, iy, areas, vertices)
        if is_outside:
            new_items[iid] = item
            kept_exterior_items.append(f"{item.get('name', 'Unknown')} ({iid})")
            log_fn(
                f"  🌳 Keeping external item (outside all rooms): "
                f"'{item.get('name', 'Unknown')}' ({iid}) at ({ix:.1f}, {iy:.1f})"
            )
            continue

        # Item is inside a room — only keep if it is an architectural wall fitting
        is_wall_fitting = bool(item.get("line") or item.get("hole"))

        if not is_wall_fitting:
            WALL_FITTING_PROXIMITY_CM = 30.0
            for lid, line in lines.items():
                if not line.get("holes"):
                    continue
                v_ids = line.get("vertices", [])
                if len(v_ids) < 2:
                    continue
                v1d = vertices.get(v_ids[0])
                v2d = vertices.get(v_ids[1])
                if not v1d or not v2d:
                    continue
                x1 = v1d.get("x", 0); y1 = v1d.get("y", 0)
                x2 = v2d.get("x", 0); y2 = v2d.get("y", 0)
                dx, dy = x2 - x1, y2 - y1
                if dx == 0 and dy == 0:
                    dist = math.hypot(ix - x1, iy - y1)
                else:
                    t    = ((ix - x1) * dx + (iy - y1) * dy) / (dx * dx + dy * dy)
                    t    = max(0.0, min(1.0, t))
                    cx_p = x1 + t * dx
                    cy_p = y1 + t * dy
                    dist = math.hypot(ix - cx_p, iy - cy_p)
                if dist <= WALL_FITTING_PROXIMITY_CM:
                    is_wall_fitting = True
                    break

        if is_wall_fitting:
            new_items[iid] = item
            kept_wall_items.append(f"{item.get('name', 'Unknown')} ({iid})")
            log_fn(f"  🚪 Keeping wall/door/window fitting: '{item.get('name', 'Unknown')}' ({iid})")
            continue

        # Interior furniture → cull
        culled_items.append(f"{item.get('name', 'Unknown')} ({iid})")
        log_fn(f"  🗑️ Culling interior item: '{item.get('name', 'Unknown')}' ({iid})")

    if kept_exterior_items:
        log_fn(f"\n  🌳 Kept {len(kept_exterior_items)} external items:")
        for ei in kept_exterior_items:
            log_fn(f"     + {ei}")

    if kept_wall_items:
        log_fn(f"\n  🚪 Kept {len(kept_wall_items)} wall/door/window fittings:")
        for wi in kept_wall_items:
            log_fn(f"     + {wi}")

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

            # ── 2. Collect camera points from the full animation path ─────────
            camera_points: List[Tuple[float, float]] = []

            if "video_animation" in payload and payload["video_animation"] is not None:
                anim      = payload["video_animation"]
                keyframes = anim.get("keyframes", []) if isinstance(anim, dict) else []

                if keyframes:
                    self.log("🎬 Video Animation detected. Analysing full camera path...")
                    for kf in keyframes:
                        tjs = kf.get("threejs_camera_data")
                        if tjs and isinstance(tjs, dict) and "position" in tjs:
                            pos = tjs["position"]
                            camera_points.append((pos.get("x", 0.0), pos.get("z", 0.0)))
                        elif "position" in kf:
                            pos = kf["position"]
                            camera_points.append((pos.get("x", 0.0), pos.get("z", 0.0)))
                    self.log(f"   Collected {len(camera_points)} points from video path.")
                else:
                    self.log("ℹ️ video_animation has no keyframes. Checking static camera ...")

            # Fallback: static camera position
            if not camera_points:
                if "threejs_camera" in payload and payload["threejs_camera"] is not None:
                    pos   = payload["threejs_camera"].get("position", {})
                    cam_x = pos.get("x", 0) * SCALE_METERS_TO_CM
                    cam_y = pos.get("z", 0) * SCALE_METERS_TO_CM
                    camera_points.append((cam_x, cam_y))
                    self.log(f"📍 Using static ThreeJS camera: ({cam_x:.1f}, {cam_y:.1f})")
                elif "blender_camera" in payload and payload["blender_camera"] is not None:
                    loc   = payload["blender_camera"].get("location", [0, 0, 0])
                    cam_x = loc[0] * SCALE_METERS_TO_CM
                    cam_y = -loc[1] * SCALE_METERS_TO_CM
                    camera_points.append((cam_x, cam_y))
                    self.log(f"📍 Using static Blender camera: ({cam_x:.1f}, {cam_y:.1f})")
                else:
                    self.log("⚠️ No camera data found. Skipping optimization.")
                    return payload

            # ── 3. Resolve layer/flat structure ───────────────────────────────
            layers           = fp_data.get("layers", {})
            is_flat          = False
            target_layer_id  = None
            layer            = None

            if layers:
                target_layer_id = fp_data.get("selectedLayer", "layer-1")
                layer = layers.get(target_layer_id)
                if not layer:
                    target_layer_id = list(layers.keys())[0]
                    layer = layers[target_layer_id]

                vertices = layer.get("vertices", {})
                lines    = layer.get("lines",    {})
                areas    = layer.get("areas",    {})
                items    = layer.get("items",    {})
                holes    = layer.get("holes",    {})
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
            self.log(f"  Camera path points: {len(camera_points)}")

            # ── 4. Pre-compute room bounding boxes ────────────────────────────
            area_bboxes: Dict[str, Any] = {}
            for area_id, area in areas.items():
                poly_verts = []
                for v_id in area.get("vertices", []):
                    v = vertices.get(v_id)
                    if v:
                        poly_verts.append((v.get("x", 0), v.get("y", 0)))
                if poly_verts:
                    xs = [p[0] for p in poly_verts]
                    ys = [p[1] for p in poly_verts]
                    area_bboxes[area_id] = (min(xs), max(xs), min(ys), max(ys), poly_verts)

            # ── 5. Determine active rooms from camera path ────────────────────
            active_area_ids: Set[str] = set()
            self.log(f"\n   Checking {len(camera_points)} path points against {len(area_bboxes)} rooms...")

            for i, (cx, cy) in enumerate(camera_points):
                for area_id, (min_x, max_x, min_y, max_y, poly_verts) in area_bboxes.items():
                    if min_x <= cx <= max_x and min_y <= cy <= max_y:
                        if _point_in_polygon(cx, cy, poly_verts):
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
                    active_area_ids, vertices, lines, areas, items, holes, self.log
                )
            else:
                render_mode = "EXTERIOR"
                self.log(
                    f"\n  🌍 MODE: EXTERIOR VIDEO RENDER "
                    f"(Camera entirely outside building — windows will render BLACK)"
                )
                new_vertices, new_lines, new_areas, new_items, new_holes = _cull_exterior_video(
                    vertices, lines, areas, items, holes, self.log
                )

            # ── 7. Summary ────────────────────────────────────────────────────
            self.log(f"\n{'='*60}")
            self.log(f"📊 VIDEO CULLING SUMMARY  [{render_mode}]")
            self.log(f"{'='*60}")
            self.log(f"  Render Mode: {render_mode}")
            self.log(f"  Camera path points analysed: {len(camera_points)}")
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
                layer["vertices"] = new_vertices
                layer["lines"]    = new_lines
                layer["areas"]    = new_areas
                layer["items"]    = new_items
                layer["holes"]    = new_holes
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