import json
import math
import os
import logging
from typing import Dict, List, Any, Tuple, Optional

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

class VideoSceneOptimizer:
    def __init__(self, log_path: Optional[str] = None):
        """
        Initialize VideoSceneOptimizer.
        
        Args:
            log_path: Full path for the culling log file. If None, uses default.
                      Pass the output render filename base to name the log after the render.
        """
        if log_path is None:
            log_path = os.path.join(CULLING_LOGS_DIR, "video_culling_log.txt")
        self.log_path = log_path
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        # Clear previous log
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                f.write(f"--- Video Scene Culling Log ---\n")
        except:
            pass

    def log(self, msg):
        print(f"[VideoSceneOptimizer] {msg}")
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{msg}\n")
        except:
            pass

    def cull_scene(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main function to optimize the payload by culling invisible rooms for VIDEO path.
        Analyzes the full camera path across all keyframes to determine which rooms are visible.
        Returns a NEW payload dict with optimized floor_plan_data.
        """
        try:
            # 1. Parse floor_plan_data
            fp_str = payload.get("floor_plan_data")
            if not fp_str:
                self.log("⚠️ No floor_plan_data found.")
                return payload
            
            # Robust parsing for Video Pipeline (often Dict)
            if isinstance(fp_str, dict):
                fp_data = fp_str
            else:
                try:
                    fp_data = json.loads(fp_str)
                except Exception as e:
                     self.log(f"⚠️ Failed to parse floor_plan_data string: {e}")
                     return payload
            
            # 2. Get Camera Points (List of (x, y) in Plan CM)
            camera_points = []
            
            # Case A: Video Animation (Multiple Frames)
            if "video_animation" in payload and payload["video_animation"] is not None:
                anim = payload["video_animation"]
                keyframes = []
                if isinstance(anim, dict):
                    keyframes = anim.get("keyframes", [])
                
                if keyframes:
                    self.log("🎬 Video Animation detected. Analyzing full camera path...")
                    for kf in keyframes:
                        # Check for threejs_camera_data format first
                        tjs = kf.get("threejs_camera_data")
                        if tjs and isinstance(tjs, dict) and "position" in tjs:
                            pos = tjs["position"]
                            px = pos.get("x", 0.0)
                            py = pos.get("z", 0.0)
                            camera_points.append((px, py))
                        elif "position" in kf:
                            pos = kf["position"]
                            px = pos.get("x", 0.0)
                            py = pos.get("z", 0.0)
                            camera_points.append((px, py))
                    self.log(f"   Collected {len(camera_points)} points from video path.")
                else:
                    self.log("ℹ️ video_animation has no keyframes. Checking for camera position...")

            # Case B: Fallback to static camera position
            if not camera_points:
                if "threejs_camera" in payload and payload["threejs_camera"] is not None:
                    pos = payload["threejs_camera"].get("position", {})
                    cam_x = pos.get("x", 0) * SCALE_METERS_TO_CM
                    cam_y = pos.get("z", 0) * SCALE_METERS_TO_CM
                    camera_points.append((cam_x, cam_y))
                    self.log(f"📍 Using static ThreeJS camera: ({cam_x:.1f}, {cam_y:.1f})")
                elif "blender_camera" in payload and payload["blender_camera"] is not None:
                    loc = payload["blender_camera"].get("location", [0, 0, 0])
                    cam_x = loc[0] * SCALE_METERS_TO_CM
                    cam_y = -loc[1] * SCALE_METERS_TO_CM
                    camera_points.append((cam_x, cam_y))
                    self.log(f"📍 Using static Blender camera: ({cam_x:.1f}, {cam_y:.1f})")
                else:
                    self.log("⚠️ No camera data found. Skipping optimization.")
                    return payload

            # 3. Helpers to access layers/data
            layers = fp_data.get("layers", {})
            
            if layers:
                target_layer_id = fp_data.get("selectedLayer", "layer-1")
                layer = layers.get(target_layer_id)
                if not layer:
                    target_layer_id = list(layers.keys())[0]
                    layer = layers[target_layer_id]
                
                vertices = layer.get("vertices", {})
                lines = layer.get("lines", {})
                areas = layer.get("areas", {})
                items = layer.get("items", {})
                holes = layer.get("holes", {})
            else:
                # Flat structure
                self.log("ℹ️ No 'layers' found. Checking for flat structure...")
                
                def list_to_dict(data_obj):
                    if isinstance(data_obj, list):
                        return {item.get("id"): item for item in data_obj if item.get("id")}
                    return data_obj if isinstance(data_obj, dict) else {}

                vertices = list_to_dict(fp_data.get("vertices", {}))
                lines = list_to_dict(fp_data.get("lines", {}))
                areas = list_to_dict(fp_data.get("areas", {}))
                items = list_to_dict(fp_data.get("items", {}))
                holes = list_to_dict(fp_data.get("holes", {}))
                
                if not vertices and not areas:
                     self.log("⚠️ No vertices or areas found in flat structure either.")
                     return payload

            # 4. Find Active Rooms (Iterate all camera points)
            active_area_ids = set()
            
            # Optimization: Pre-calculate bounding boxes
            area_bboxes = {}
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
            
            self.log(f"   Checking {len(camera_points)} path points against {len(area_bboxes)} rooms...")
            
            # Check each point
            for i, (cx, cy) in enumerate(camera_points):
                point_found = False
                for area_id, (min_x, max_x, min_y, max_y, poly_verts) in area_bboxes.items():
                    # Quick BBox Check
                    if min_x <= cx <= max_x and min_y <= cy <= max_y:
                        if _point_in_polygon(cx, cy, poly_verts):
                            if area_id not in active_area_ids:
                                active_area_ids.add(area_id)
                                self.log(f"   ✅ Path Point {i} inside: {areas[area_id].get('name')} ({area_id})")
                            point_found = True
                            break # Found the room for this point
                
                if not point_found and i == 0:
                     self.log(f"   ⚠️ Start Point ({cx:.1f}, {cy:.1f}) NOT inside any room.")

            if not active_area_ids:
                self.log("⚠️ Camera path completely outside all known rooms. Falling back to FULL SCENE (No Culling applied).")
                return payload
            
            self.log(f"✅ Identified {len(active_area_ids)} unique active rooms along path.")

            # 5. Find ALL connected neighbors (BFS flood-fill)
            #    IMPORTANT: Rooms often use DIFFERENT vertex IDs for the same physical
            #    position, so we must match by position proximity, not just ID.
            POSITION_TOLERANCE = 1.0  # cm
            
            # Pre-compute area vertex positions
            area_positions = {}  # area_id -> list of (x, y)
            for a_id, area in areas.items():
                positions = []
                for v_id in area.get("vertices", []):
                    v = vertices.get(v_id)
                    if v:
                        positions.append((v.get("x", 0), v.get("y", 0)))
                area_positions[a_id] = positions
            
            def areas_are_neighbors(aid1, aid2):
                """Check if two areas share a vertex at the same position."""
                for (x1, y1) in area_positions.get(aid1, []):
                    for (x2, y2) in area_positions.get(aid2, []):
                        if abs(x1 - x2) < POSITION_TOLERANCE and abs(y1 - y2) < POSITION_TOLERANCE:
                            return True
                return False
            
            kept_area_ids = set(active_area_ids)
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
                    
                    # Check by ID or position proximity
                    v1_pos = (vertices[v1].get("x",0), vertices[v1].get("y",0)) if v1 in vertices else None
                    v2_pos = (vertices[v2].get("x",0), vertices[v2].get("y",0)) if v2 in vertices else None
                    
                    touches_kept = v1 in all_kept_verts or v2 in all_kept_verts
                    if not touches_kept:
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

            self.log(f"🎯 Total kept rooms: {len(kept_area_ids)}")

            # 6. Filter Data
            new_areas = {aid: areas[aid] for aid in kept_area_ids}
            
            kept_vertex_ids = set()
            for area in new_areas.values():
                kept_vertex_ids.update(area.get("vertices", []))
            
            # Build set of kept positions for position-based line matching
            kept_positions = set()
            for vid in kept_vertex_ids:
                v = vertices.get(vid)
                if v:
                    kept_positions.add((round(v.get("x",0), 0), round(v.get("y",0), 0)))
            
            def vertex_is_near_kept(vid):
                if vid in kept_vertex_ids:
                    return True
                v = vertices.get(vid)
                if v:
                    pos = (round(v.get("x",0), 0), round(v.get("y",0), 0))
                    return pos in kept_positions
                return False
            
            new_vertices = {}
            for vid in kept_vertex_ids:
                if vid in vertices:
                    new_vertices[vid] = vertices[vid]

            new_lines = {}
            for lid, line in lines.items():
                v_ids = line.get("vertices", [])
                if len(v_ids) >= 2:
                    v1, v2 = v_ids[0], v_ids[1]
                    if vertex_is_near_kept(v1) and vertex_is_near_kept(v2):
                        new_lines[lid] = line
                        if v1 not in new_vertices and v1 in vertices:
                            new_vertices[v1] = vertices[v1]
                            kept_vertex_ids.add(v1)
                        if v2 not in new_vertices and v2 in vertices:
                            new_vertices[v2] = vertices[v2]
                            kept_vertex_ids.add(v2)
            
            new_holes = {}
            for hid, hole in holes.items():
                if hole.get("line") in new_lines:
                    new_holes[hid] = hole
            
            new_items = {}
            culled_items = []
            for iid, item in items.items():
                ix, iy = item.get("x", 0), item.get("y", 0)
                is_kept = False
                for aid in kept_area_ids:
                    poly_verts = []
                    area = areas.get(aid)
                    if not area: continue
                    for v_id in area.get("vertices", []):
                        v = vertices.get(v_id)
                        if v:
                            poly_verts.append((v.get("x", 0), v.get("y", 0)))
                    if poly_verts and _point_in_polygon(ix, iy, poly_verts):
                        is_kept = True
                        break
                if is_kept:
                    new_items[iid] = item
                else:
                    # RETRY with tolerance
                    TOLERANCE_CM = 85.0
                    
                    rescued = False
                    for aid in kept_area_ids:
                        area = areas.get(aid)
                        if not area: continue
                        
                        poly_verts = []
                        for v_id in area.get("vertices", []):
                            v = vertices.get(v_id)
                            if v:
                                poly_verts.append((v.get("x", 0), v.get("y", 0)))
                        
                        if not poly_verts: continue
                        
                        # Calculate min distance to polygon edges
                        min_dist = float('inf')
                        n_poly = len(poly_verts)
                        for i in range(n_poly):
                            p1 = poly_verts[i]
                            p2 = poly_verts[(i + 1) % n_poly]
                            
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
                            self.log(f"  ✨ Video: Rescuing item '{item.get('name', 'Unknown')}' (ID: {iid}) - Dist: {min_dist:.1f}cm <= {TOLERANCE_CM}cm")
                            new_items[iid] = item
                            rescued = True
                            break
                    
                    if not rescued:
                        culled_items.append(f"{item.get('name', 'Unknown')} ({iid})")

            # Log culled items
            if culled_items:
                self.log(f"\n🗑️  Culled {len(culled_items)} items:")
                for ci in culled_items:
                    self.log(f"   - {ci}")
            
            self.log(f"\n{'='*60}")
            self.log(f"📊 VIDEO CULLING SUMMARY")
            self.log(f"{'='*60}")
            self.log(f"  Camera path points analyzed: {len(camera_points)}")
            self.log(f"  Active rooms (camera enters): {len(active_area_ids)}")
            self.log(f"  Kept rooms (+ neighbors):     {len(kept_area_ids)}")
            self.log(f"  Areas:  {len(areas)} -> {len(new_areas)}  (removed {len(areas) - len(new_areas)})")
            self.log(f"  Items:  {len(items)} -> {len(new_items)}  (removed {len(items) - len(new_items)})")
            self.log(f"  Lines:  {len(lines)} -> {len(new_lines)}  (removed {len(lines) - len(new_lines)})")
            self.log(f"{'='*60}")

            # 7. Reconstruct Data (Handle both Layer and Flat structures)
            if layers:
                layer["vertices"] = new_vertices
                layer["lines"] = new_lines
                layer["areas"] = new_areas
                layer["items"] = new_items
                layer["holes"] = new_holes
            else:
                # Flat structure: Convert back to list if original was list-based
                def dict_to_list_if_was_list(key, new_dict):
                    orig = fp_data.get(key)
                    if isinstance(orig, list):
                        return list(new_dict.values())
                    return new_dict

                fp_data["vertices"] = dict_to_list_if_was_list("vertices", new_vertices)
                fp_data["lines"] = dict_to_list_if_was_list("lines", new_lines)
                fp_data["areas"] = dict_to_list_if_was_list("areas", new_areas)
                fp_data["items"] = dict_to_list_if_was_list("items", new_items)
                fp_data["holes"] = dict_to_list_if_was_list("holes", new_holes)

            # 8. Update JSON
            new_payload = payload.copy()
            new_payload["floor_plan_data"] = fp_data if isinstance(fp_str, dict) else json.dumps(fp_data)
            
            return new_payload

        except Exception as e:
            self.log(f"❌ Error during video scene culling: {e}")
            import traceback
            traceback.print_exc()
            return payload # Fail safe: return original
