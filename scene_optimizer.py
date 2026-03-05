import json
import math
import os
import logging
from typing import Dict, List, Any, Set, Tuple, Optional

# Configuration
# Assuming: Plan coordinates are in CM. Blender/Camera coordinates are in Meters.
SCALE_METERS_TO_CM = 100.0

# Culling logs folder (sibling of godot_project)
CULLING_LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "culling_logs")
os.makedirs(CULLING_LOGS_DIR, exist_ok=True)

def _point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    """
    Ray-casting algorithm to check if point (x,y) is inside polygon.
    Polygon is a list of (x, y) tuples.
    """
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

class SceneOptimizer:
    def __init__(self, log_path: Optional[str] = None):
        """
        Initialize SceneOptimizer.
        
        Args:
            log_path: Full path for the culling log file. If None, uses default.
                      Pass the output render filename base to name the log after the render.
        """
        if log_path is None:
            log_path = os.path.join(CULLING_LOGS_DIR, "culling_log.txt")
        self.log_path = log_path
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        # Clear previous log
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.write(f"--- Scene Culling Log ---\n")
        except:
            pass

    def log(self, msg):
        print(f"[SceneOptimizer] {msg}")
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{msg}\n")
        except:
            pass

    def cull_scene(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main function to optimize the payload by culling invisible rooms.
        Returns a NEW payload dict with optimized floor_plan_data.
        Iterates through ALL layers to ensure multi-floor support.
        """
        try:
            # 1. Parse floor_plan_data
            fp_str = payload.get("floor_plan_data")
            if not fp_str:
                self.log("⚠️ No floor_plan_data found.")
                return payload
            
            fp_data = json.loads(fp_str) if isinstance(fp_str, str) else fp_str

            # 2. Get Camera Position (Meters) -> Convert to CM
            # priority: blender_camera > threejs_camera
            cam_x, cam_y = 0.0, 0.0
            
            if "blender_camera" in payload and "location" in payload["blender_camera"]:
                loc = payload["blender_camera"]["location"]
                cam_x = loc[0] * SCALE_METERS_TO_CM
                cam_y = -loc[1] * SCALE_METERS_TO_CM
                self.log(f"📍 Camera (Blender): {loc} -> Plan: ({cam_x:.1f}, {cam_y:.1f})")
            elif "threejs_camera" in payload and "position" in payload["threejs_camera"]:
                pos = payload["threejs_camera"]["position"]
                cam_x = pos.get("x", 0) * SCALE_METERS_TO_CM
                cam_y = pos.get("z", 0) * SCALE_METERS_TO_CM 
                self.log(f"📍 Camera (ThreeJS): {pos} -> Plan: ({cam_x:.1f}, {cam_y:.1f})")
            else:
                self.log("⚠️ No camera data found. Skipping optimization.")
                return payload

            # 3. Process Layers (Support Multi-Floor Optimization)
            layers = fp_data.get("layers", {})
            if not layers:
                self.log("⚠️ No layers found in floor plan.")
                return payload

            total_areas_before = 0
            total_areas_after = 0
            total_items_before = 0
            total_items_after = 0
            total_lines_before = 0
            total_lines_after = 0

            # Iterate over ALL layers to optimize them individually
            for layer_id, layer in layers.items():
                self.log(f"🔍 Inspecting Layer: {layer_id}")
                
                vertices = layer.get("vertices", {})
                lines = layer.get("lines", {})
                areas = layer.get("areas", {})
                items = layer.get("items", {})
                holes = layer.get("holes", {})

                total_areas_before += len(areas)
                total_items_before += len(items)
                total_lines_before += len(lines)
                
                # 4. Find Active Room in this Layer
                active_area_id = None
                
                for area_id, area in areas.items():
                    # Construct polygon
                    poly_verts = []
                    for v_id in area.get("vertices", []):
                        v = vertices.get(v_id)
                        if v:
                            poly_verts.append((v["x"], v["y"]))
                    
                    if not poly_verts:
                        continue

                    # Debug: Log bounds of the room
                    xs = [p[0] for p in poly_verts]
                    ys = [p[1] for p in poly_verts]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    
                    # Check inside bound box first, then polygon
                    if min_x <= cam_x <= max_x and min_y <= cam_y <= max_y:
                        if _point_in_polygon(cam_x, cam_y, poly_verts):
                            active_area_id = area_id
                            self.log(f"  ✅ Camera ({cam_x:.1f}, {cam_y:.1f}) found in Active Room: {area.get('name')} ({area_id})")
                            break
                        else:
                            self.log(f"  ❓ Camera inside bounding box of {area.get('name')} but failed poly check.")
                
                if not active_area_id:
                    self.log(f"  ⚠️ Camera NOT inside any room on {layer_id}. Keeping layer INTACT (No Culling).")
                    total_areas_after += len(areas)
                    total_items_after += len(items)
                    total_lines_after += len(lines)
                    continue # Skip optimization for this layer, keep it full

                # 5. Find ALL connected neighbors (BFS flood-fill)
                #    IMPORTANT: Rooms often use DIFFERENT vertex IDs for the same physical
                #    position, so we must match by position proximity, not just ID.
                POSITION_TOLERANCE = 1.0  # cm — vertices within 1cm are "same position"
                
                # Pre-compute area vertex positions for fast lookup
                area_positions = {}  # area_id -> list of (x, y)
                for a_id, area in areas.items():
                    positions = []
                    for v_id in area.get("vertices", []):
                        v = vertices.get(v_id)
                        if v:
                            positions.append((v["x"], v["y"]))
                    area_positions[a_id] = positions
                
                def areas_are_neighbors(aid1, aid2):
                    """Check if two areas share a vertex at the same position (within tolerance)."""
                    for (x1, y1) in area_positions.get(aid1, []):
                        for (x2, y2) in area_positions.get(aid2, []):
                            if abs(x1 - x2) < POSITION_TOLERANCE and abs(y1 - y2) < POSITION_TOLERANCE:
                                return True
                    return False
                
                kept_area_ids = {active_area_id}
                changed = True
                
                while changed:
                    changed = False
                    
                    # a) Position-based proximity: rooms with vertices at the same position
                    for a_id in list(areas.keys()):
                        if a_id in kept_area_ids:
                            continue
                        for kept_id in list(kept_area_ids):
                            if areas_are_neighbors(kept_id, a_id):
                                kept_area_ids.add(a_id)
                                changed = True
                                self.log(f"  ➕ Adding Neighbor Room (Position Match): {areas[a_id].get('name')} ({a_id})")
                                break
                    
                    # b) Hole/Hidden-wall connections
                    all_kept_verts = set()
                    for aid in kept_area_ids:
                        all_kept_verts.update(areas[aid].get("vertices", []))
                    
                    for lid, line in lines.items():
                        line_holes = line.get("holes", [])
                        line_hidden = (line.get("visible") == False)
                        if not line_holes and not line_hidden:
                            continue
                        
                        v_ids = line.get("vertices", [])
                        if len(v_ids) < 2:
                            continue
                        v1, v2 = v_ids[0], v_ids[1]
                        
                        # Check if wall vertices are near any kept area vertex
                        v1_pos = (vertices[v1]["x"], vertices[v1]["y"]) if v1 in vertices else None
                        v2_pos = (vertices[v2]["x"], vertices[v2]["y"]) if v2 in vertices else None
                        
                        touches_kept = False
                        if v1 in all_kept_verts or v2 in all_kept_verts:
                            touches_kept = True
                        else:
                            # Also check by position proximity
                            for aid in kept_area_ids:
                                for (px, py) in area_positions.get(aid, []):
                                    if v1_pos and abs(v1_pos[0]-px) < POSITION_TOLERANCE and abs(v1_pos[1]-py) < POSITION_TOLERANCE:
                                        touches_kept = True
                                        break
                                    if v2_pos and abs(v2_pos[0]-px) < POSITION_TOLERANCE and abs(v2_pos[1]-py) < POSITION_TOLERANCE:
                                        touches_kept = True
                                        break
                                if touches_kept:
                                    break
                        
                        if touches_kept:
                            for a_id, area in areas.items():
                                if a_id in kept_area_ids:
                                    continue
                                area_verts = set(area.get("vertices", []))
                                if v1 in area_verts or v2 in area_verts:
                                    kept_area_ids.add(a_id)
                                    changed = True
                                    reason = "Hole-Connected" if line_holes else "Hidden-Wall-Connected"
                                    self.log(f"  ➕ Adding Neighbor Room ({reason}): {area.get('name')} ({a_id})")

                self.log(f"  🎯 Total kept rooms on {layer_id}: {len(kept_area_ids)}")

                # 6. Filter Data
                
                # --- Robust Filter: Keep Architecture based on Kept Rooms ---
                
                # 1. Areas: Keep identified rooms
                new_areas = {aid: areas[aid] for aid in kept_area_ids}
                
                # 2. Vertices: Keep ALL vertices used by these areas
                kept_vertex_ids = set()
                for area in new_areas.values():
                    kept_vertex_ids.update(area.get("vertices", []))
                
                # Build set of kept positions for position-based matching
                kept_positions = set()
                for vid in kept_vertex_ids:
                    v = vertices.get(vid)
                    if v:
                        # Round to nearest cm for matching
                        kept_positions.add((round(v["x"], 0), round(v["y"], 0)))
                
                def vertex_is_near_kept(vid):
                    """Check if a vertex is in the kept set by ID or by position proximity."""
                    if vid in kept_vertex_ids:
                        return True
                    v = vertices.get(vid)
                    if v:
                        pos = (round(v["x"], 0), round(v["y"], 0))
                        return pos in kept_positions
                    return False
                
                new_vertices = {}
                for vid in kept_vertex_ids:
                    if vid in vertices:
                        new_vertices[vid] = vertices[vid]

                # 3. Lines: Keep lines if BOTH their vertices are in or near the kept set
                new_lines = {}
                for lid, line in lines.items():
                    v_ids = line.get("vertices", [])
                    if len(v_ids) >= 2:
                        v1, v2 = v_ids[0], v_ids[1]
                        if vertex_is_near_kept(v1) and vertex_is_near_kept(v2):
                            new_lines[lid] = line
                            # Also add these vertices to kept set (they may have different IDs)
                            if v1 not in new_vertices and v1 in vertices:
                                new_vertices[v1] = vertices[v1]
                                kept_vertex_ids.add(v1)
                            if v2 not in new_vertices and v2 in vertices:
                                new_vertices[v2] = vertices[v2]
                                kept_vertex_ids.add(v2)
                
                # 4. Holes: Keep holes associated with kept lines
                new_holes = {}
                for hid, hole in holes.items():
                    if hole.get("line") in new_lines:
                        new_holes[hid] = hole
                
                # 5. Filter Items
                # Only keep items that are inside the Active Room or Neighbors
                new_items = {}
                culled_items = []
                for iid, item in items.items():
                    # EXEMPTION: Keep floor assets (e.g. floor tiles, floor managers, carpets)
                    item_type = item.get("type", "").lower()
                    item_name = item.get("name", "").lower()
                    
                    if any(kw in item_type or kw in item_name for kw in ['floor', 'slab', 'ground', 'terrain', 'level']):
                        new_items[iid] = item
                        self.log(f"  🛡️ Preserving 'Floor' Asset: {item.get('name')} ({iid})")
                        continue

                    ix, iy = item.get("x", 0), item.get("y", 0)
                    
                    # Check inclusion in any kept area (Active + Neighbors)
                    is_kept = False
                    for aid in kept_area_ids:
                        poly_verts = []
                        area = areas.get(aid)
                        if not area: continue
                        
                        for v_id in area.get("vertices", []):
                            v = vertices.get(v_id)
                            if v:
                                poly_verts.append((v["x"], v["y"]))
                        
                        if poly_verts and _point_in_polygon(ix, iy, poly_verts):
                            is_kept = True
                            break
                    
                    if is_kept:
                        new_items[iid] = item
                    else:
                        # RETRY with tolerance: Check if item is CLOSE to any kept area
                        TOLERANCE_CM = 85.0
                        
                        rescued = False
                        for aid in kept_area_ids:
                            area = areas.get(aid)
                            if not area: continue
                            
                            poly_verts = []
                            for v_id in area.get("vertices", []):
                                v = vertices.get(v_id)
                                if v:
                                    poly_verts.append((v["x"], v["y"]))
                            
                            if not poly_verts: continue
                            
                            # Calculate min distance to polygon edges
                            min_dist = float('inf')
                            n_poly = len(poly_verts)
                            for i in range(n_poly):
                                p1 = poly_verts[i]
                                p2 = poly_verts[(i + 1) % n_poly]
                                
                                # Point to Line Segment distance
                                x1, y1 = p1
                                x2, y2 = p2
                                dx, dy = x2 - x1, y2 - y1
                                
                                if dx == 0 and dy == 0:
                                    dist = math.hypot(ix - x1, iy - y1)
                                else:
                                    t = ((ix - x1) * dx + (iy - y1) * dy) / (dx*dx + dy*dy)
                                    t = max(0, min(1, t))
                                    cx, cy = x1 + t * dx, y1 + t * dy
                                    dist = math.hypot(ix - cx, iy - cy)
                                
                                if dist < min_dist:
                                    min_dist = dist
                            
                            if min_dist <= TOLERANCE_CM:
                                self.log(f"  ✨ Rescuing item '{item.get('name', 'Unknown')}' (ID: {iid}) - Dist: {min_dist:.1f}cm <= {TOLERANCE_CM}cm")
                                new_items[iid] = item
                                rescued = True
                                break
                        
                        if not rescued:
                            culled_items.append(f"{item.get('name', 'Unknown')} ({iid})")
                
                # Log culled items
                if culled_items:
                    self.log(f"\n  🗑️  Culled {len(culled_items)} items from {layer_id}:")
                    for ci in culled_items:
                        self.log(f"     - {ci}")

                self.log(f"  ✂️  Optimization Results for {layer_id}:")
                self.log(f"     Areas: {len(areas)} -> {len(new_areas)}")
                self.log(f"     Lines: {len(lines)} -> {len(new_lines)}")
                self.log(f"     Items: {len(items)} -> {len(new_items)}")

                total_areas_after += len(new_areas)
                total_items_after += len(new_items)
                total_lines_after += len(new_lines)

                # 7. Reconstruct Layer
                layer["vertices"] = new_vertices
                layer["lines"] = new_lines
                layer["areas"] = new_areas
                layer["items"] = new_items
                layer["holes"] = new_holes

            # Summary
            self.log(f"\n{'='*60}")
            self.log(f"📊 CULLING SUMMARY")
            self.log(f"{'='*60}")
            self.log(f"  Areas:  {total_areas_before} -> {total_areas_after}  (removed {total_areas_before - total_areas_after})")
            self.log(f"  Items:  {total_items_before} -> {total_items_after}  (removed {total_items_before - total_items_after})")
            self.log(f"  Lines:  {total_lines_before} -> {total_lines_after}  (removed {total_lines_before - total_lines_after})")
            self.log(f"{'='*60}")
            
            # 8. Update JSON
            new_payload = payload.copy()
            new_payload["floor_plan_data"] = json.dumps(fp_data) if isinstance(fp_str, str) else fp_data
            
            return new_payload

        except Exception as e:
            self.log(f"❌ Error during scene culling: {e}")
            import traceback
            traceback.print_exc()
            return payload # Fail safe: return original

# Standalone test
if __name__ == "__main__":
    print("Running optimization test...")
    # Load sample file manually if needed, or mock data
    pass
