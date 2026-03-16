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
EXTERIOR_ITEM_KEYWORDS = ["roof", "grill", "balcony", "exterior", "facade", "chimney", "antenna", "gutter", "downspout"]

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
# BFS FLOOD-FILL  (shared by interior helpers)
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
) -> Tuple[dict, dict, dict, dict, dict]:
    """
    INTERIOR MODE — camera is inside a room.

    Keep:
      • The active room + all rooms reachable via openings/hidden walls (BFS)
      • All walls (lines) whose BOTH endpoints belong to kept rooms
      • All holes (doors/windows) on kept walls
      • Items inside kept rooms (+ 85 cm rescue for wall-hugging items)
      • Floor assets always

    Remove:
      • All rooms not reachable from the active room
      • Walls, holes, items belonging to removed rooms
      • Exterior-only items (roof, grill, balcony, etc.) — even if geometrically inside
      • Items outside all kept room polygons
    """
    log_fn(f"  ── Interior Culling for {layer_id} ──")

    # BFS to find all visible rooms
    kept_area_ids = _flood_fill_connected_rooms(
        {active_area_id}, areas, lines, vertices, log_fn
    )

    log_fn(f"  🎯 Total kept rooms on {layer_id}: {len(kept_area_ids)}")
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
            kept_positions.add((round(v["x"], 0), round(v["y"], 0)))

    def vertex_is_near_kept(vid: str) -> bool:
        if vid in kept_vertex_ids:
            return True
        v = vertices.get(vid)
        if v:
            return (round(v["x"], 0), round(v["y"], 0)) in kept_positions
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

    # ── Holes ────────────────────────────────────────────────────────────────
    new_holes = {
        hid: hole
        for hid, hole in holes.items()
        if hole.get("line") in new_lines
    }

    # ── Items ────────────────────────────────────────────────────────────────
    new_items: Dict[str, Any] = {}
    culled_items: List[str] = []

    for iid, item in items.items():
        item_type = str(item.get("type", "")).lower()
        item_name = str(item.get("name", "")).lower()

        # Always keep floor assets
        if any(kw in item_type or kw in item_name for kw in FLOOR_ITEM_KEYWORDS):
            new_items[iid] = item
            log_fn(f"  🛡️ Preserving Floor Asset: '{item.get('name')}' ({iid})")
            continue

        # Remove exterior-only assets (roof, grill, balcony) in interior render
        if _is_exterior_asset(item):
            culled_items.append(f"{item.get('name', 'Unknown')} ({iid}) [exterior-only asset]")
            log_fn(f"  🗑️ Removing exterior-only asset in interior render: '{item.get('name', 'Unknown')}' ({iid})")
            continue

        ix, iy = item.get("x", 0), item.get("y", 0)

        # Pass 1 – strict polygon inclusion
        is_kept = False
        for aid in kept_area_ids:
            area = areas.get(aid)
            if not area:
                continue
            poly_verts = []
            for v_id in area.get("vertices", []):
                v = vertices.get(v_id)
                if v:
                    poly_verts.append((v["x"], v["y"]))
            if poly_verts and _point_in_polygon(ix, iy, poly_verts):
                is_kept = True
                break

        if is_kept:
            new_items[iid] = item
            log_fn(f"  ✓ Kept item (inside room): '{item.get('name', 'Unknown')}' ({iid})")
            continue

        # Pass 2 – proximity rescue (items clipped into walls)
        rescued = False
        for aid in kept_area_ids:
            area = areas.get(aid)
            if not area:
                continue
            poly_verts = []
            for v_id in area.get("vertices", []):
                v = vertices.get(v_id)
                if v:
                    poly_verts.append((v["x"], v["y"]))
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
        log_fn(f"\n  🗑️  Culled {len(culled_items)} exterior/out-of-room items from {layer_id}:")
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
            show_all    = bool(payload.get("show_all", True))
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
                cam_y        = -loc[1] * SCALE_METERS_TO_CM
                cam_height_m = float(loc[2])
                cam_source   = "blender_camera"
                self.log(
                    f"📍 Camera (Blender): {loc} → "
                    f"Plan XZ: ({cam_x:.1f}, {cam_y:.1f}) cm | Height: {cam_height_m:.3f} m"
                )

            elif "threejs_camera" in payload and "position" in payload["threejs_camera"]:
                pos          = payload["threejs_camera"]["position"]
                cam_x        = pos.get("x", 0) * SCALE_METERS_TO_CM
                cam_y        = pos.get("z", 0) * SCALE_METERS_TO_CM
                cam_height_m = float(pos.get("y", 0))
                cam_source   = "threejs_camera"
                self.log(
                    f"📍 Camera (ThreeJS): {pos} → "
                    f"Plan XZ: ({cam_x:.1f}, {cam_y:.1f}) cm | Height: {cam_height_m:.3f} m"
                )

            else:
                self.log("⚠️ No camera data found. Skipping optimization.")
                return payload

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
                "source":      cam_source,
                "plan_x_cm":   round(cam_x, 2),
                "plan_y_cm":   round(cam_y, 2),
                "height_m":    round(cam_height_m, 4),
                "raw_blender": payload.get("blender_camera", {}).get("location"),
                "raw_threejs": payload.get("threejs_camera", {}).get("position"),
                "is_top_view": is_top_view,
                "show_all":    show_all,
            }

            # ── 3. showAllFloors / ceiling pass ───────────────────────────────
            show_all_floors = fp_data.get("showAllFloors", True)
            self._log_data["show_all_floors"] = show_all_floors
            self.log(f"🏗️  showAllFloors = {show_all_floors}")
            self._apply_ceiling_visibility(fp_data, cam_height_m, show_all_floors)

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
                    "render_mode":  render_mode,
                    "active_room":  active_area_id,
                    "stats_before": {
                        "areas": len(areas), "lines": len(lines),
                        "holes": len(holes), "items": len(items),
                    },
                    "stats_after":  {
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