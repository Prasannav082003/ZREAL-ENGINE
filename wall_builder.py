# wall_builder.py
#
# This module contains the complete, advanced logic for generating architectural geometry.
#
# VERSION 3: Corrected the hole-cutting logic by replacing the manual bmesh cube
# creation with the standard bpy.ops operator. This resolves the "expected Image,
# got Mesh" TypeError and subsequent GLTF export failures.

import bpy
import bmesh
import math
from mathutils import Vector

# --- GEOMETRY HELPER FUNCTIONS (Unchanged) ---

def get_corner_extension(v_curr, v_prev, v_next, thickness):
    """Calculates the extension length needed for a mitered corner joint."""
    try:
        v1 = Vector((v_prev['x'] - v_curr['x'], v_prev['y'] - v_curr['y']))
        v2 = Vector((v_next['x'] - v_curr['x'], v_next['y'] - v_curr['y']))

        if v1.length == 0 or v2.length == 0:
            return 0.0

        v1.normalize()
        v2.normalize()

        dot = max(-1.0, min(1.0, v1.dot(v2)))
        angle = math.acos(dot)

        if abs(angle - math.pi / 2) < 0.001:
            return thickness / 2.0

        corner_angle = angle / 2.0
        if math.tan(corner_angle) < 0.0001:
            return thickness / 2.0

        extension = (thickness / 2.0) / math.tan(corner_angle)
        min_extension = thickness / 10.0
        final_extension = max(min_extension, abs(extension))
        return final_extension + (thickness * 0.0001)
    except Exception:
        return 0.0

def is_point_in_polygon(point, polygon_verts):
    """Determines if a point is inside a polygon using the ray-casting algorithm."""
    x, y = point.x, point.y
    inside = False
    p1 = polygon_verts[-1]
    for i in range(len(polygon_verts)):
        p2 = polygon_verts[i]
        if y > min(p1.y, p2.y):
            if y <= max(p1.y, p2.y):
                if x <= max(p1.x, p2.x):
                    if p1.y != p2.y:
                        xinters = (y - p1.y) * (p2.x - p1.x) / (p2.y - p1.y) + p1.x
                    if p1.x == p2.x or x <= xinters:
                        inside = not inside
        p1 = p2
    return inside

def get_wall_facing_direction(line, vertices_map, areas):
    """Determines if the 'inner' side of a wall is to its geometric left or right."""
    v_ids = line.get('vertices', [])
    if len(v_ids) < 2: return "inner-right"
    vA_data, vB_data = vertices_map.get(v_ids[0]), vertices_map.get(v_ids[1])
    if not vA_data or not vB_data: return "inner-right"

    vA = Vector((vA_data['x'], vA_data['y']))
    vB = Vector((vB_data['x'], vB_data['y']))

    wall_area = next((a for a in areas.values() if v_ids[0] in a.get('vertices', []) and v_ids[1] in a.get('vertices', [])), None)
    if not wall_area: return "inner-right"

    polygon = [Vector((vertices_map[vId]['x'], vertices_map[vId]['y'])) for vId in wall_area['vertices'] if vId in vertices_map]
    if len(polygon) < 3: return "inner-right"

    try:
        wall_dir = (vB - vA).normalized()
    except Exception:
        return "inner-right"

    left_normal = Vector((-wall_dir.y, wall_dir.x))
    sample_point = ((vA + vB) / 2.0) + left_normal * 5.0

    return "inner-left" if is_point_in_polygon(sample_point, polygon) else "inner-right"

# --- CORE WALL AND HOLE CREATION FUNCTIONS ---

def _create_single_wall_obj(v1, v2, height, thickness, mat_inner, mat_outer, wall_facing, ext1, ext2, scale, base_altitude=0.0):
    """Creates a single, correctly scaled and oriented wall object before holes are cut."""
    dx, dy = v2['x'] - v1['x'], v2['y'] - v1['y']
    base_length_scaled = math.hypot(dx, dy) * scale
    total_length = base_length_scaled + ext1 + ext2
    angle = math.atan2(dy, dx)

    shift_dist = (ext1 - ext2) / 2.0
    shift_vec = Vector((dx, dy, 0)).normalized() * shift_dist if Vector((dx, dy, 0)).length > 0 else Vector()

    cx = ((v1['x'] + v2['x']) / 2.0) * scale + shift_vec.x
    cy = ((v1['y'] + v2['y']) / 2.0) * scale + shift_vec.y

    # Apply base_altitude to Z position
    bpy.ops.mesh.primitive_cube_add(
        size=1, 
        location=(cx, cy, base_altitude + height / 2.0), 
        scale=(total_length, thickness, height), 
        rotation=(0, 0, angle)
    )
    wall_obj = bpy.context.active_object
    if mat_inner: wall_obj.data.materials.append(mat_inner)
    if mat_outer: wall_obj.data.materials.append(mat_outer)

    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(wall_obj.data)
    bm.faces.ensure_lookup_table()
    side_faces = sorted([f for f in bm.faces if abs(f.normal.y) > 0.9], key=lambda f: f.normal.y)
    if len(side_faces) >= 2:
        face_plus_y, face_minus_y = side_faces[-1], side_faces[0]
        if wall_facing == "inner-left":
            face_plus_y.material_index, face_minus_y.material_index = 0, 1
        else:
            face_minus_y.material_index, face_plus_y.material_index = 0, 1
    bmesh.update_edit_mesh(wall_obj.data); bm.free()
    bpy.ops.object.mode_set(mode='OBJECT')
    
    Z_FIGHT_OFFSET, Z_AXIS_OFFSET = 0.0008, 0.0008
    local_normal = Vector((0, 1, 0)) if wall_facing == "inner-left" else Vector((0, -1, 0))
    world_normal = wall_obj.rotation_euler.to_matrix() @ local_normal
    wall_obj.location += world_normal * Z_FIGHT_OFFSET
    wall_obj.location.z += Z_AXIS_OFFSET
    
    return wall_obj

def build_architecture(lines, vertices, holes, areas, materials_map, scale, base_altitude=0.0):
    """Main entry point to build all walls and cut all holes."""
    print(f"--- Building Architecture with Advanced Geometry (Base Altitude: {base_altitude}) ---")
    
    vertices_map = {v_id: v_data for v_id, v_data in vertices.items()}
    vertex_connectivity = {v_id: [] for v_id in vertices_map}
    
    for line_id, line in lines.items():
        v1_id, v2_id = line.get('vertices', [None, None])
        if v1_id in vertices_map and v2_id in vertices_map:
            vertex_connectivity[v1_id].append(line_id)
            vertex_connectivity[v2_id].append(line_id)
        else:
            print(f"  ⚠ WARNING: Skipping line '{line_id}' because it references a missing vertex ID.")

    # 1. Create all physical wall objects first
    wall_objects = {}
    for line_id, line in lines.items():
        try:
            v1_id, v2_id = line.get('vertices', [None, None])
            if v1_id not in vertices_map or v2_id not in vertices_map:
                continue
            v1, v2 = vertices_map[v1_id], vertices_map[v2_id]

            v1_adj_lines = [l for l in vertex_connectivity.get(v1_id, []) if l != line_id]
            prev_vertex = vertices_map.get(next((vid for vid in lines[v1_adj_lines[0]]['vertices'] if vid != v1_id), None)) if v1_adj_lines else None
            
            v2_adj_lines = [l for l in vertex_connectivity.get(v2_id, []) if l != line_id]
            next_vertex = vertices_map.get(next((vid for vid in lines[v2_adj_lines[0]]['vertices'] if vid != v2_id), None)) if v2_adj_lines else None

            height = line['properties']['height']['length'] * scale
            thickness = line['properties']['thickness']['length'] * scale
            ext1 = get_corner_extension(v1, prev_vertex, v2, thickness) if prev_vertex else 0
            ext2 = get_corner_extension(v2, v1, next_vertex, thickness) if next_vertex else 0
            
            mat_inner = materials_map.get(f"mat_{line_id}_inner")
            mat_outer = materials_map.get(f"mat_{line_id}_outer")
            wall_facing = get_wall_facing_direction(line, vertices_map, areas)

            wall_obj = _create_single_wall_obj(v1, v2, height, thickness, mat_inner, mat_outer, wall_facing, ext1, ext2, scale, base_altitude)
            wall_obj.name = f"Wall_{line_id}"
            wall_objects[line_id] = wall_obj
        except Exception as e:
            print(f"  ⚠ Error creating wall mesh for '{line_id}': {e}")
            
    # 2. After all walls exist, cut holes
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    scene = bpy.context.scene
    
    for hole_id, hole in holes.items():
        try:
            line_id = hole.get('line')
            if line_id not in wall_objects: continue
            
            primary_wall = wall_objects[line_id]
            line = lines[line_id]
            props = hole.get('properties', {})
            v1, v2 = vertices[line['vertices'][0]], vertices[line['vertices'][1]]
            
            width = props.get('width', {}).get('length', 0) * scale
            height = props.get('height', {}).get('length', 0) * scale
            altitude = props.get('altitude', {}).get('length', 0) * scale
            offset = hole.get('offset', 0.5)

            if width <= 0 or height <= 0:
                print(f"  ⚠ Skipping hole '{hole_id}' due to zero width or height.")
                continue

            line_vec = Vector((v2['x'] - v1['x'], v2['y'] - v1['y']))
            mid_point_2d = Vector((v1['x'], v1['y'])) + (line_vec * offset)
            wall_angle = math.atan2(line_vec.y, line_vec.x)
            
            # Apply base_altitude to hole Z position
            # hole center = base_altitude + hole_altitude + (hole_height / 2)
            hole_center_3d = Vector((mid_point_2d.x * scale, mid_point_2d.y * scale, base_altitude + altitude + height / 2))
            
            walls_to_cut = {primary_wall}
            wall_facing = get_wall_facing_direction(line, vertices_map, areas)
            local_normal = Vector((0, 1, 0)) if wall_facing == "inner-left" else Vector((0, -1, 0))
            world_normal = primary_wall.matrix_world.to_3x3() @ local_normal.normalized()
            
            for direction in [world_normal, -world_normal]:
                hit, loc, _, _, hit_obj, _ = scene.ray_cast(depsgraph, hole_center_3d, direction, distance=5.0)
                if hit and hit_obj and hit_obj.name.startswith("Wall_"):
                    walls_to_cut.add(hit_obj)

            # --- START: ROBUST CUTTER CREATION FIX ---
            # Replace the manual bmesh method with the standard, safer bpy.ops operator.
            bpy.ops.mesh.primitive_cube_add(size=1.0)
            cutter_obj = bpy.context.active_object
            cutter_obj.name = f"Cutter_{hole_id}"
            # --- END: ROBUST CUTTER CREATION FIX ---

            cutter_obj.location = hole_center_3d
            cutter_obj.scale = (width, 5.0, height) # Make it very thick to ensure it cuts
            cutter_obj.rotation_euler = (0, 0, wall_angle)

            for wall in walls_to_cut:
                bool_mod = wall.modifiers.new(name=f"Bool_{hole_id}", type='BOOLEAN')
                bool_mod.operation = 'DIFFERENCE'
                bool_mod.object = cutter_obj
                bpy.context.view_layer.objects.active = wall
                bpy.ops.object.modifier_apply(modifier=bool_mod.name)
            
            # --- START: ROBUST CUTTER DELETION FIX ---
            # Get the mesh data from the cutter object before deleting the object itself.
            cutter_mesh = cutter_obj.data
            bpy.data.objects.remove(cutter_obj, do_unlink=True)
            bpy.data.meshes.remove(cutter_mesh)
            # --- END: ROBUST CUTTER DELETION FIX ---

        except Exception as e:
            print(f"  ⚠ Error cutting hole '{hole_id}': {e}")
            
    print(f"--- Finished building {len(wall_objects)} walls and cutting {len(holes)} holes. ---")
    return wall_objects