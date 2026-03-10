extends Node3D

# image_glb_creation.gd
# Handles the construction of the scene: Architecture (Walls, Floors, Ceilings) and Asset Loading.
var _tracked_assets = []
var day_render = true

func build_scene(data):
	if data.has("day_render"):
		day_render = data["day_render"]
	
	var geom_data = data
	if data.has("floor_plan_data"):
		var fp = data["floor_plan_data"]
		if typeof(fp) == TYPE_STRING:
			var json = JSON.new()
			var err = json.parse(fp)
			if err == OK:
				geom_data = json.data
				print("Parsed floor_plan_data from string.")
			else:
				print("Error parsing floor_plan_data string: ", json.get_error_message())
		else:
			geom_data = fp
	
	setup_lighting(data, geom_data)
		
	build_architecture(geom_data)
	load_assets(geom_data)
	# setup_camera(data) - Moved to render_image.gd

func setup_lighting(data, geom_data = {}):
	# Window sunlight simulation (stable alternative to RectLight)
	var cam_pose = _extract_camera_pose(data)
	var cam_pos: Vector3 = cam_pose["position"]
	var cam_target: Vector3 = cam_pose["target"]
	var cam_forward: Vector3 = (cam_target - cam_pos).normalized()
	if cam_forward.length() < 0.001:
		cam_forward = Vector3(0, 0, -1)
	var cam_right = cam_forward.cross(Vector3.UP).normalized()
	if cam_right.length() < 0.001:
		cam_right = Vector3.RIGHT
	var light_anchor = cam_target
	var room_info = _extract_room_lighting_bounds(geom_data)
	var room_center: Vector3 = room_info.get("center", light_anchor)
	var room_extent: Vector2 = room_info.get("extent", Vector2(6.0, 6.0))
	var room_ceiling_y: float = room_info.get("ceiling_y", room_center.y + 2.8)

	var window_light = OmniLight3D.new()
	window_light.position = Vector3(room_center.x, room_ceiling_y - 1.25, room_center.z)
	window_light.light_energy = 0.14 if day_render else 0.07
	window_light.omni_range = max(room_extent.x, room_extent.y) * 2.1 + 6.0
	window_light.light_color = Color(1.0, 0.95, 0.85)
	window_light.light_specular = 0.0
	window_light.shadow_enabled = false

	add_child(window_light)
	# ── Primary Directional Light (Sun / Moon) ─────────────────────────────
	var dir_light = DirectionalLight3D.new()
	dir_light.name = "Sun"
	# Permanent seam fix: disable directional shadow maps (source of half-surface splits).
	dir_light.shadow_enabled = false

	# High-quality shadow settings for photorealistic renders
	dir_light.light_angular_distance = 0.6
	dir_light.shadow_bias = 0.01
	dir_light.shadow_normal_bias = 0.2
	# Keep these values harmless even with shadows off.
	dir_light.directional_shadow_mode = DirectionalLight3D.SHADOW_ORTHOGONAL
	dir_light.directional_shadow_max_distance = 120.0
	dir_light.directional_shadow_blend_splits = true
	dir_light.shadow_transmittance_bias = 0.05
	dir_light.shadow_blur = 1.5
	dir_light.light_angular_distance = 0.5

	if data.has("directional_light"):
		var l = data["directional_light"]
		if day_render:
			dir_light.light_energy = l.get("intensity", 2.8)
		else:
			dir_light.light_energy = l.get("intensity", 0.14)
		if l.has("position"):
			dir_light.position = parse_vec3(l["position"])
			if l.has("target"):
				dir_light.look_at(parse_vec3(l["target"]))
			else:
				dir_light.look_at(light_anchor)
	else:
		# Camera-aware defaults: keep key light roughly behind/above the camera direction.
		if day_render:
			dir_light.light_energy = 2.8
			dir_light.light_color  = Color(1.0, 0.96, 0.88)  # warm sunlight
			dir_light.position     = light_anchor - cam_forward * 12.0 + cam_right * 6.0 + Vector3.UP * 16.0
			dir_light.look_at(light_anchor)
		else:
			dir_light.light_energy = 0.14
			dir_light.light_color  = Color(0.55, 0.62, 0.90)  # cool moonlight
			dir_light.position     = light_anchor - cam_forward * 10.0 - cam_right * 4.0 + Vector3.UP * 18.0
			dir_light.look_at(light_anchor)

	add_child(dir_light)

	# ── Sky / Environment ──────────────────────────────────────────────────
	var env = Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = Sky.new()

	var exr_path = ""
	if day_render:
		exr_path = ProjectSettings.globalize_path("res://day.exr")
	else:
		exr_path = ProjectSettings.globalize_path("res://night.exr")

	var sky_material_set = false
	if FileAccess.file_exists(exr_path):
		var exr_image = Image.new()
		var load_err = exr_image.load(exr_path)
		if load_err == OK:
			var sky_tex     = ImageTexture.create_from_image(exr_image)
			var panorama_mat = PanoramaSkyMaterial.new()
			panorama_mat.panorama = sky_tex
			env.sky.sky_material  = panorama_mat
			sky_material_set = true
			print("Loaded EXR sky background: ", exr_path)
		else:
			print("Warning: Failed to load EXR: ", exr_path, " Error: ", load_err)
	else:
		print("Warning: EXR sky file not found: ", exr_path)

	if not sky_material_set:
		var sky_mat = ProceduralSkyMaterial.new()
		if day_render:
			sky_mat.sky_top_color      = Color(0.25, 0.40, 0.78)   # deep blue zenith
			sky_mat.sky_horizon_color  = Color(0.75, 0.80, 0.90)   # pale horizon
			sky_mat.ground_bottom_color   = Color(0.08, 0.06, 0.04)
			sky_mat.ground_horizon_color  = Color(0.30, 0.28, 0.22)
			sky_mat.sun_angle_max         = 30.0
			sky_mat.sun_curve             = 0.15
		else:
			sky_mat.sky_top_color      = Color(0.01, 0.02, 0.05)
			sky_mat.sky_horizon_color  = Color(0.04, 0.06, 0.10)
			sky_mat.ground_bottom_color   = Color(0.005, 0.005, 0.01)
			sky_mat.ground_horizon_color  = Color(0.03, 0.05, 0.08)
		env.sky.sky_material = sky_mat
		print("Using fallback procedural sky")

	# ── Ambient Light ──────────────────────────────────────────────────────
	# Use color ambient for enclosed rooms to avoid sky-light leaking at wall/ceiling corners.
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	if day_render:
		env.ambient_light_color = Color(0.76, 0.77, 0.78)
		env.ambient_light_energy = 0.42
	else:
		env.ambient_light_color = Color(0.20, 0.24, 0.34)
		env.ambient_light_energy = 0.10

	# ── Tone Mapping ──────────────────────────────────────────────────────
	# CRITICAL: exposure must be set high enough that headless render is NOT black.
	# CameraAttributesPhysical is intentionally NOT used (causes black in headless).
	env.tonemap_mode     = Environment.TONE_MAPPER_FILMIC
	env.tonemap_exposure = 0.85   # slightly darker global exposure
	env.tonemap_white    = 5.0

	# ── SDFGI: DISABLED — crashes / produces black in headless Godot export ─
	# Use SSAO + SSIL for safe indirect-light approximation instead.
	env.sdfgi_enabled = false

	# ── Screen-Space Indirect Light (safe GI replacement for headless) ────
	env.ssil_enabled   = false

	# ── Screen-Space Reflections: DISABLED in headless (depth buffer missing) ─
	env.ssr_enabled = false

	# ── Screen-Space Ambient Occlusion ─────────────────────────────────────
	env.ssao_enabled   = true
	env.ssao_radius    = 1.0
	env.ssao_intensity = 0.45
	env.ssao_power     = 0.9
	env.ssao_detail    = 0.25
	env.ssao_horizon   = 0.03
	env.ssao_sharpness = 0.98

	# ── Glow (subtle bloom for realism) ───────────────────────────────────
	env.glow_enabled       = true
	env.glow_intensity     = 0.10
	env.glow_bloom         = 0.03
	env.glow_hdr_threshold = 2.2
	env.glow_hdr_scale     = 1.4
	env.glow_blend_mode    = Environment.GLOW_BLEND_MODE_SOFTLIGHT

	# ── Colour Grading ────────────────────────────────────────────────────
	env.adjustment_enabled    = true
	env.adjustment_brightness = 1.0
	env.adjustment_contrast   = 1.06
	env.adjustment_saturation = 1.10

	var world_env = WorldEnvironment.new()

	# Reflection Probe for realistic reflections
	# Keep the probe aligned to the detected room bounds to avoid probe-volume seams.
	var room_height = max(2.8, (room_ceiling_y - room_center.y) * 2.0)
	var probe = ReflectionProbe.new()
	probe.size = Vector3(max(room_extent.x + 4.0, 8.0), room_height + 2.0, max(room_extent.y + 4.0, 8.0))
	probe.update_mode = ReflectionProbe.UPDATE_ONCE
	probe.intensity = 1.0
	probe.max_distance = max(room_extent.x, room_extent.y) * 1.5 + 12.0
	probe.position = room_center
	add_child(probe)
	
	world_env.environment = env
	add_child(world_env)

	# ── Scene Fill Lights ─────────────────────────────────────────────────
	# These guarantee the scene is NEVER black regardless of GI or sky state.
	# They are kept intentionally low so the directional sun dominates.
	if day_render:
		# Warm overhead fill — simulates ceiling/sky bounce
		var fill = OmniLight3D.new()
		fill.name           = "FillLight"
		fill.position       = Vector3(room_center.x, room_ceiling_y - 0.55, room_center.z)
		fill.light_energy   = 0.18
		fill.omni_range     = max(room_extent.x, room_extent.y) * 2.5 + 10.0
		fill.light_color    = Color(1.0, 0.97, 0.90)
		fill.light_specular = 0.0
		fill.shadow_enabled = false
		add_child(fill)

		# Symmetric front/back fill avoids camera-direction brightness splitting.
		var fill_offset = max(room_extent.x, room_extent.y) * 0.28
		var fill_front = OmniLight3D.new()
		fill_front.name           = "FrontFillLight"
		fill_front.position       = Vector3(room_center.x, room_ceiling_y - 0.9, room_center.z) + cam_forward * fill_offset
		fill_front.light_energy   = 0.055
		fill_front.omni_range     = max(room_extent.x, room_extent.y) * 1.6 + 4.0
		fill_front.light_color    = Color(0.99, 0.97, 0.93)
		fill_front.light_specular = 0.0
		fill_front.shadow_enabled = false
		add_child(fill_front)

		var fill_back = OmniLight3D.new()
		fill_back.name           = "BackFillLight"
		fill_back.position       = Vector3(room_center.x, room_ceiling_y - 0.9, room_center.z) - cam_forward * fill_offset
		fill_back.light_energy   = 0.055
		fill_back.omni_range     = max(room_extent.x, room_extent.y) * 1.6 + 4.0
		fill_back.light_color    = Color(0.99, 0.97, 0.93)
		fill_back.light_specular = 0.0
		fill_back.shadow_enabled = false
		add_child(fill_back)
	else:
		var fill = OmniLight3D.new()
		fill.name           = "FillLight"
		fill.position       = Vector3(room_center.x, room_ceiling_y - 0.55, room_center.z)
		fill.light_energy   = 0.10
		fill.omni_range     = max(room_extent.x, room_extent.y) * 2.2 + 7.0
		fill.light_color    = Color(0.5, 0.55, 0.8)
		fill.light_specular = 0.0
		fill.shadow_enabled = false
		add_child(fill)

	print("Photorealistic lighting setup complete. day_render=", day_render)

func parse_vec3(d):
	if typeof(d) == TYPE_DICTIONARY:
		return Vector3(d.get("x", 0), d.get("y", 0), d.get("z", 0))
	return Vector3.ZERO

func _extract_camera_pose(data: Dictionary) -> Dictionary:
	var cam_pos = Vector3(0, 1.6, 6)
	var cam_target = Vector3.ZERO
	var found = false

	if data.has("threejs_camera") and typeof(data["threejs_camera"]) == TYPE_DICTIONARY:
		var tc = data["threejs_camera"]
		if tc.has("position") and typeof(tc["position"]) == TYPE_DICTIONARY:
			cam_pos = parse_vec3(tc["position"])
			found = true
		if tc.has("target") and typeof(tc["target"]) == TYPE_DICTIONARY:
			cam_target = parse_vec3(tc["target"])
	elif data.has("blender_camera") and typeof(data["blender_camera"]) == TYPE_DICTIONARY:
		var bc = data["blender_camera"]
		if bc.has("location") and typeof(bc["location"]) == TYPE_ARRAY and bc["location"].size() >= 3:
			# Blender (x,y,z) -> Godot (x,z,-y)
			cam_pos = Vector3(float(bc["location"][0]), float(bc["location"][2]), -float(bc["location"][1]))
			found = true
		if data.has("blender_target") and typeof(data["blender_target"]) == TYPE_DICTIONARY:
			var bt = data["blender_target"]
			if bt.has("location") and typeof(bt["location"]) == TYPE_ARRAY and bt["location"].size() >= 3:
				cam_target = Vector3(float(bt["location"][0]), float(bt["location"][2]), -float(bt["location"][1]))

	if not found:
		cam_pos = Vector3(0, 1.6, 6)
	if cam_target.distance_to(cam_pos) < 0.001:
		cam_target = cam_pos + Vector3(0, 0, -1)

	return {"position": cam_pos, "target": cam_target}

func _extract_room_lighting_bounds(geom_data: Dictionary) -> Dictionary:
	var out = {
		"center": Vector3.ZERO,
		"extent": Vector2(6.0, 6.0),
		"ceiling_y": 2.8
	}
	if typeof(geom_data) != TYPE_DICTIONARY:
		return out

	var work = geom_data
	if geom_data.has("layers") and typeof(geom_data["layers"]) == TYPE_DICTIONARY:
		var selected = str(geom_data.get("selectedLayer", ""))
		var layers = geom_data["layers"]
		if selected != "" and layers.has(selected):
			work = layers[selected]
		elif layers.size() > 0:
			work = layers[layers.keys()[0]]

	if typeof(work) != TYPE_DICTIONARY:
		return out
	if not work.has("areas") or not work.has("vertices"):
		return out

	var areas = work["areas"]
	var vertices = work["vertices"]
	if typeof(areas) != TYPE_DICTIONARY or typeof(vertices) != TYPE_DICTIONARY or areas.size() == 0:
		return out

	var min_x = 1e20
	var max_x = -1e20
	var min_z = 1e20
	var max_z = -1e20
	var got_any = false

	for aid in areas:
		var area = areas[aid]
		if typeof(area) != TYPE_DICTIONARY or not area.has("vertices"):
			continue
		for vid in area["vertices"]:
			var key = str(vid)
			if not vertices.has(key):
				continue
			var v = vertices[key]
			var x = float(v.get("x", 0.0)) * 0.01
			var z = float(v.get("y", 0.0)) * 0.01
			min_x = min(min_x, x)
			max_x = max(max_x, x)
			min_z = min(min_z, z)
			max_z = max(max_z, z)
			got_any = true

	if not got_any:
		return out

	var layer_alt = 0.0
	if work.has("altitude"):
		var alt = work["altitude"]
		if typeof(alt) == TYPE_DICTIONARY and alt.has("length"):
			layer_alt = float(alt["length"]) * 0.01
		else:
			layer_alt = float(alt) * 0.01

	var ceil_h = 2.8
	for aid in areas:
		var area = areas[aid]
		if typeof(area) != TYPE_DICTIONARY:
			continue
		if area.has("ceiling_properties"):
			var cp = area["ceiling_properties"]
			if typeof(cp) == TYPE_DICTIONARY and cp.has("height"):
				var h = float(cp["height"]) * 0.01
				if h > 0.1:
					ceil_h = h
				break

	out["center"] = Vector3((min_x + max_x) * 0.5, layer_alt + ceil_h * 0.5, (min_z + max_z) * 0.5)
	out["extent"] = Vector2(max(2.0, max_x - min_x), max(2.0, max_z - min_z))
	out["ceiling_y"] = layer_alt + ceil_h
	return out

func build_architecture(data):
	print("Building Architecture...")

	if data.has("layers"):
		print("Found layers in architecture data.")
		
		# Determine which layers to process based on showAllFloors / selectedLayer
		var show_all_floors = data.get("showAllFloors", true)
		var selected_layer = data.get("selectedLayer", "")
		
		if show_all_floors:
			print("showAllFloors is true — building ALL layers.")
		else:
			print("showAllFloors is false — building only selected layer: ", selected_layer)
		
		for layer_id in data["layers"]:
			# If showAllFloors is false, skip layers that don't match selectedLayer
			if not show_all_floors and selected_layer != "" and str(layer_id) != str(selected_layer):
				print("Skipping layer: ", layer_id, " (not selected)")
				continue
			
			var layer = data["layers"][layer_id]
			print("Processing layer: ", layer_id)
			var layer_alt = 0.0
			# When rendering a single layer (showAllFloors=false), place it at
			# ground level so it doesn't float at its original altitude.
			if show_all_floors:
				if layer.has("altitude"):
					var alt_val = layer["altitude"]
					if typeof(alt_val) == TYPE_DICTIONARY and alt_val.has("length"):
						layer_alt = float(alt_val["length"]) * 0.01
					else:
						layer_alt = float(alt_val) * 0.01
			else:
				print("Single-layer mode: placing layer '", layer_id, "' at ground level (altitude 0)")
					
			if layer.has("lines") and layer.has("vertices"):
				_build_layer_geometry(layer, layer_alt)
	elif data.has("lines") and data.has("vertices"):
		print("Found lines/vertices in root data.")
		_build_layer_geometry(data, 0.0)
	else:
		print("Warning: No lines or vertices found in floor plan data.")

func _build_layer_geometry(layer_data, layer_altitude):
	var csg = CSGCombiner3D.new()
	csg.use_collision = true
	add_child(csg)
	
	var lines = layer_data["lines"]
	var vertices = layer_data["vertices"]
	var scale_factor = 0.01 # Convert cm to meters
	
	# Helper map for hole definitions
	var all_holes = {}
	if layer_data.has("holes"):
		all_holes = layer_data["holes"]
	
	# ── Compute room centroid from area vertices ────────────────────────────
	# Used to determine which side of each wall faces the room interior.
	# Without this, vertex winding differences between layers cause inner/outer
	# textures to swap on layers > 1.
	var room_centroid = Vector3.ZERO
	var centroid_count = 0
	if layer_data.has("areas"):
		for area_id in layer_data["areas"]:
			var area = layer_data["areas"][area_id]
			if not area.has("vertices"): continue
			for v_id in area["vertices"]:
				var vs = str(v_id)
				if vertices.has(vs):
					var v = vertices[vs]
					room_centroid += Vector3(float(v["x"]), 0.0, float(v["y"])) * scale_factor
					centroid_count += 1
	if centroid_count > 0:
		room_centroid /= centroid_count
	var has_centroid = centroid_count > 0
	
	print("Layer has ", lines.size(), " lines. Room centroid: ", room_centroid)
	
	# 1. Build Walls
	for line_id in lines:
		var line = lines[line_id]
		# Skip walls marked as hidden in the floor plan
		if line.has("visible") and line["visible"] == false: continue
		# Check if vertices exist
		if not line.has("vertices") or line["vertices"].size() < 2: continue
		
		var v1_id = str(line["vertices"][0])
		var v2_id = str(line["vertices"][1])
		
		# Check if vertices exist in the vertex list
		if not vertices.has(v1_id) or not vertices.has(v2_id): continue
			
		var v1_data = vertices[v1_id]
		var v2_data = vertices[v2_id]
		
		# Swapping Y and Z is common for 2D plans -> 3D
		var p1 = Vector3(float(v1_data["x"]), 0.0, float(v1_data["y"])) * scale_factor
		var p2 = Vector3(float(v2_data["x"]), 0.0, float(v2_data["y"])) * scale_factor
		
		var diff = p2 - p1
		var length = diff.length()
		var center = (p1 + p2) / 2.0
		var height = 240.0 * scale_factor # Default height
		
		if line.has("properties") and line["properties"].has("height"):
			var h_prop = line["properties"]["height"]
			if typeof(h_prop) == TYPE_DICTIONARY and h_prop.has("length"):
				height = float(h_prop["length"]) * scale_factor
			else:
				height = float(h_prop) * scale_factor
				
		var wall_thickness = 0.2
		if line.has("properties") and line["properties"].has("thickness"):
			var t_prop = line["properties"]["thickness"]
			wall_thickness = get_dimension_value(t_prop, 20.0) * scale_factor
		
		var wall_angle = -atan2(diff.z, diff.x)
		var wall_pos  = Vector3(center.x, layer_altitude + height / 2.0, center.z)
		
		# ── Determine which side of the wall faces the room interior ──────────
		# The wall normal in +Z local space (after rotation by wall_angle) may point
		# toward or away from the room centroid depending on vertex winding.
		# We compute the perpendicular (normal) of the wall segment, check if 
		# the centroid is on that side, and flip if needed.
		var wall_basis = Basis(Vector3.UP, wall_angle)
		var wall_normal_world = wall_basis * Vector3(0, 0, 1)  # +Z local = default "inner" direction
		var inner_sign = 1.0  # +1 means +Z is inner, -1 means -Z is inner
		if has_centroid:
			var to_centroid = room_centroid - center
			to_centroid.y = 0.0  # only care about XZ plane
			var dot = to_centroid.dot(wall_normal_world)
			if dot < 0:
				inner_sign = -1.0  # centroid is on -Z side, so flip inner direction
		
		# ── Extend wall length at both ends to fill corner gaps ────────────────
		# Without this, walls meeting at 90° leave a gap equal to wall_thickness×wall_thickness
		# at each corner. Extending by wall_thickness/2 at each end makes the CSG union
		# fill the corners into clean solid joints (like a proper wall miter).
		var corner_length = length + wall_thickness


		# ── Structural wall in CSGCombiner gets OUTER material ─────────────────
		# The structural wall is inside the CSGCombiner so holes (doors/windows)
		# get cut through it correctly. From outside the building, the exterior
		# face of this wall is what's visible → apply outer_properties here.
		var wall = CSGBox3D.new()
		wall.size = Vector3(corner_length, height, wall_thickness)  # extended to fill corners
		wall.position = wall_pos
		wall.rotation.y = wall_angle
		
		# Outer material on the structural wall (exterior face)
		if line.has("outer_properties") and line["outer_properties"].has("material"):
			wall.material = create_material(line["outer_properties"]["material"])
		elif line.has("inner_properties") and line["inner_properties"].has("material"):
			wall.material = create_material(line["inner_properties"]["material"])
		else:
			var def_mat = StandardMaterial3D.new()
			def_mat.albedo_color = Color(0.82,0.82,0.82)
			def_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
			wall.material = def_mat
		
		csg.add_child(wall)
		
		# ── Inner face panel via a per-wall CSGCombiner3D ──────────────────────
		# Using a dedicated CSG combiner lets us punch the same door/window holes
		# through the inner face panel, so openings are not covered by the solid
		# inner surface (which would make the exterior invisible through the hole).
		var inner_mat_data = null
		if line.has("inner_properties") and line["inner_properties"].has("material"):
			inner_mat_data = line["inner_properties"]["material"]
		elif line.has("outer_properties") and line["outer_properties"].has("material"):
			inner_mat_data = line["outer_properties"]["material"]
		
		# inner_csg is created only when there is a material to show.
		# Holes added further below will subtract from this combiner too.
		var inner_csg: CSGCombiner3D = null
		if inner_mat_data != null:
			var face_t = 0.001  # 1 mm — purely visual overlay
			var inner_offset = wall_thickness / 2.0 + face_t / 2.0
			# Use inner_sign to place the panel on the correct (room-facing) side
			var inner_world  = wall_pos + wall_basis * Vector3(0, 0, inner_offset * inner_sign)
			
			inner_csg = CSGCombiner3D.new()
			inner_csg.position = inner_world
			inner_csg.rotation.y = wall_angle
			add_child(inner_csg)  # at scene root so CSG operates independently
			
			# Main panel (UNION — the default operation)
			var inner_box = CSGBox3D.new()
			inner_box.size = Vector3(corner_length, height, face_t)
			inner_box.material = create_material(inner_mat_data)
			inner_csg.add_child(inner_box)


		# 1b. Build Holes (Doors/Windows) AND INSTANTIATE ASSETS
		if line.has("holes"):
			for hole_id in line["holes"]:
				var hid = str(hole_id)
				if not all_holes.has(hid): continue
				var hole = all_holes[hid]
				
				var h_width = get_dimension_value(hole.get("width", 0)) * scale_factor
				var h_height = get_dimension_value(hole.get("height", 0)) * scale_factor
				var h_alt = get_dimension_value(hole.get("altitude", 0)) * scale_factor
				
				# Get properties if available (override)
				if hole.has("properties"):
					var props = hole["properties"]
					if props.has("width"): 
						h_width = get_dimension_value(props["width"]) * scale_factor
					if props.has("height"): 
						h_height = get_dimension_value(props["height"]) * scale_factor
					if props.has("altitude"): 
						h_alt = get_dimension_value(props["altitude"]) * scale_factor

				# Determine offset (normalized 0-1)
				var offset_ratio = 0.5
				if hole.has("offset"):
					offset_ratio = float(hole["offset"])
				elif hole.has("properties") and hole["properties"].has("offset"):
					offset_ratio = float(hole["properties"]["offset"])
					
				# Calculate Position — linear interpolation between p1 and p2
				var hole_pos_xz = p1.lerp(p2, offset_ratio)
				
				var h_center_pos = hole_pos_xz
				h_center_pos.y = layer_altitude + h_alt + h_height / 2.0
				
				# Ensure valid dimensions
				if h_width < 0.01 or h_height < 0.01: continue
				
				# ── 1. Cut through the structural wall (CSGCombiner csg) ───────────
				var hole_csg = CSGBox3D.new()
				hole_csg.operation = CSGBox3D.OPERATION_SUBTRACTION
				hole_csg.size = Vector3(h_width, h_height, wall_thickness + 0.2) # Thicker than wall to ensure cut
				hole_csg.position = h_center_pos
				hole_csg.rotation.y = wall_angle
				csg.add_child(hole_csg)
				print("Added hole cutter at: ", h_center_pos)
				
				# ── 2. Cut through the inner face panel (inner_csg) ───────────────
				# Without this the inner overlay panel covers the opening and makes
				# the exterior invisible when looking through the hole.
				# NOTE: we cannot use inner_csg.to_local() here because global_transform
				# is not propagated until after the first physics frame. Instead we
				# manually invert inner_csg's transform:
				#   step 1 – translate: subtract inner_csg origin (= inner_world)
				#   step 2 – inverse-rotate: multiply by transposed rotation basis
				if inner_csg != null:
					var inner_hole = CSGBox3D.new()
					inner_hole.operation = CSGBox3D.OPERATION_SUBTRACTION
					# Generous depth – punches cleanly through the 1 mm panel in both directions
					inner_hole.size = Vector3(h_width, h_height, 0.5)
					# Manual inverse transform (world → inner_csg local)
					var delta_w   = h_center_pos - inner_csg.position
					var local_pos = Basis(Vector3.UP, wall_angle).transposed() * delta_w
					inner_hole.position = local_pos
					inner_csg.add_child(inner_hole)
				
				# ── 3. Visibility Blocker for EXTERIOR render ─────────────────────
				# If marked, add a black plane in the middle of the wall to hide interior
				if hole.get("is_exterior_black", false):
					var blocker = CSGBox3D.new()
					blocker.size = Vector3(h_width + 0.02, h_height + 0.02, 0.01) # Slightly larger to avoid gaps
					blocker.position = h_center_pos
					blocker.rotation.y = wall_angle
					var black_mat = StandardMaterial3D.new()
					black_mat.albedo_color = Color.BLACK
					blocker.material = black_mat
					add_child(blocker)
					print("Added black visibility blocker for exterior hole: ", hid)
				
				# Instantiate Asset (if available)
				if hole.has("asset_urls"):
					var urls = hole["asset_urls"]
					var model_path = ""
					if urls.has("GLB_File_URL") and urls["GLB_File_URL"] != null:
						model_path = str(urls["GLB_File_URL"])
					elif urls.has("glb_Url") and urls["glb_Url"] != null:
						model_path = str(urls["glb_Url"])
						
					if model_path != "":
						# Resolve path same as load_assets
						if not FileAccess.file_exists(model_path):
							# Try basic heuristics
							if FileAccess.file_exists("res://" + model_path): model_path = "res://" + model_path
							elif FileAccess.file_exists("./" + model_path): model_path = "./" + model_path
						
						if FileAccess.file_exists(model_path):
							var glTF = GLTFDocument.new()
							var glTFState = GLTFState.new()
							var error = glTF.append_from_file(model_path, glTFState)
							if error == OK:
								var asset_node = glTF.generate_scene(glTFState)
								if asset_node:
									add_child(asset_node)
									
									var aabb = _get_hierarchy_aabb(asset_node)
									var dims = aabb.size
									if dims.x == 0: dims.x = 1.0
									if dims.y == 0: dims.y = 1.0
									if dims.z == 0: dims.z = 1.0
									
									var target_w = h_width
									var target_h = h_height
									var target_d = wall_thickness
									
									var scale_x = target_w / dims.x
									var scale_y = target_h / dims.y
									var scale_z = target_d / dims.z
									
									var bounds_y_min = aabb.position.y * scale_y
									
									asset_node.position = h_center_pos
									asset_node.position.y = (layer_altitude + h_alt) - bounds_y_min
									
									asset_node.rotation.y = wall_angle
									
									# Check for flip
									var flip_x = 1.0
									var flip_z = 1.0
									if hole.get("flipX", false):
										flip_x = -1.0
									if hole.get("flipZ", false):
										flip_z = -1.0
										
									asset_node.scale = Vector3(scale_x * flip_x, scale_y, scale_z * flip_z)
									
									print("Loaded hole asset: ", model_path, " final scale: ", asset_node.scale)
							else:
								print("Failed to load hole GLTF: ", model_path, " Error: ", error)
						
						if model_path != "":
							_tracked_assets.append({
								"type": "hole_asset",
								"id": hole.get("id", "unknown"),
								"name": hole.get("name", "unknown"),
								"path": model_path
							})


	# 2. Build Floors and Ceilings from Areas
	# First, determine the max wall height across all lines in this layer
	# so ceilings don't float above the walls (ceiling_properties.height may differ)
	var max_wall_height = 0.0
	var max_wall_thickness = 0.2 * scale_factor  # fallback default
	for lid in lines:
		var l = lines[lid]
		if l.has("visible") and l["visible"] == false: continue
		if l.has("properties") and l["properties"].has("height"):
			var h_prop = l["properties"]["height"]
			var wh = 0.0
			if typeof(h_prop) == TYPE_DICTIONARY and h_prop.has("length"):
				wh = float(h_prop["length"]) * scale_factor
			else:
				wh = float(h_prop) * scale_factor
			if wh > max_wall_height:
				max_wall_height = wh
		if l.has("properties") and l["properties"].has("thickness"):
			var wt = get_dimension_value(l["properties"]["thickness"], 20.0) * scale_factor
			if wt > max_wall_thickness:
				max_wall_thickness = wt
	if max_wall_height < 0.1:
		max_wall_height = 280.0 * scale_factor  # fallback default

	# Use one ceiling height across all areas by default to prevent visible
	# brightness seams at area boundaries.
	var force_uniform_ceiling_height = true
	if layer_data.has("force_uniform_ceiling_height"):
		force_uniform_ceiling_height = bool(layer_data["force_uniform_ceiling_height"])
	var global_ceil_height = max_wall_height
	
	if layer_data.has("areas"):
		print("Building Floors/Ceilings for ", layer_data["areas"].size(), " areas.")
		for area_id in layer_data["areas"]:
			var area = layer_data["areas"][area_id]
			if not area.has("vertices") or area["vertices"].size() < 3: continue
			
			var polygon = PackedVector2Array()
			
			# Collect vertices for the polygon
			for v_id in area["vertices"]:
				var vs = str(v_id)
				if vertices.has(vs):
					var v = vertices[vs]
					var x = float(v["x"]) * scale_factor
					var y = float(v["y"]) * scale_factor # 2D Y maps to 3D Z
					polygon.append(Vector2(x, y))
			
			if polygon.size() < 3: continue
			
			# ── Enforce consistent polygon winding (CCW) ──────────────────────
			# CSGPolygon3D extrudes along +Y in its local space. After rotating
			# 90° around X, +Y becomes +Z. The "front" face of the polygon
			# depends on winding order. We enforce counter-clockwise winding
			# so the geometry faces the same direction for all layers regardless
			# of how vertices were ordered in the JSON.
			var signed_area = 0.0
			for pi in range(polygon.size()):
				var pa = polygon[pi]
				var pb = polygon[(pi + 1) % polygon.size()]
				signed_area += (pa.x * pb.y - pb.x * pa.y)
			signed_area *= 0.5
			# If signed_area < 0, polygon is clockwise → reverse to make CCW
			if signed_area < 0:
				polygon.reverse()

			# Keep pre-expanded polygon bounds to drive world-size UV scaling for
			# floor/ceiling (so repeat behaves like wall mapping).
			var uv_polygon = polygon.duplicate()

			
			# ── Expand polygon outward to cover full wall thickness ──────────
			# The area vertices sit at the wall centerline. Walls extend outward
			# by wall_thickness/2 on each side, plus corner_length adds another
			# wall_thickness/2 at each end. Expand the floor/ceiling polygon so
			# it reaches the outer edges of the walls (no gaps at corners).
			polygon = _expand_polygon_outward(polygon, max_wall_thickness / 2.0)
			
			# Create Floor — enforce minimum depth to prevent zero-volume CSG glitches
			var floor_depth = 0.1
			if area.has("floor_properties") and area["floor_properties"].has("thickness"):
				var t = float(area["floor_properties"]["thickness"]) * scale_factor
				if t > 0.001:
					floor_depth = t
			if floor_depth < 0.05:
				floor_depth = 0.05
			
			var floor_poly = CSGPolygon3D.new()
			floor_poly.polygon = polygon
			floor_poly.mode = CSGPolygon3D.MODE_DEPTH
			floor_poly.depth = floor_depth
			floor_poly.rotation.x = PI / 2 # Rotate to lie flat on X-Z plane, align +Y(2D) to +Z(3D)
			floor_poly.position.y = layer_altitude - floor_depth # Floor is below altitude (surface at 0)
			_apply_polygon_uv_world_scale(floor_poly, uv_polygon)
			
			# Material for Floor
			if area.has("floor_properties") and area["floor_properties"].has("material"):
				floor_poly.material = create_material(area["floor_properties"]["material"])
			else:
				var floor_mat = StandardMaterial3D.new()
				floor_mat.albedo_color = Color(0.8, 0.8, 0.8) # Light Grey
				floor_poly.material = floor_mat
			
			csg.add_child(floor_poly)
			
			# Create Ceiling
			var ceil_height = global_ceil_height
			if not force_uniform_ceiling_height:
				# Per-area ceiling height (legacy behavior)
				ceil_height = max_wall_height
				if area.has("ceiling_properties"):
					var cp = area["ceiling_properties"]
					if cp.has("height"):
						var ch = float(cp["height"]) * scale_factor
						# Use the smaller of ceiling_properties.height and wall height
						# to prevent the ceiling from floating above the walls
						if ch > 0.1:
							ceil_height = min(ch, max_wall_height)
				elif area.has("properties") and area["properties"].has("height"):
					var h_prop = area["properties"]["height"]
					var ch = 0.0
					if typeof(h_prop) == TYPE_DICTIONARY and h_prop.has("length"):
						ch = float(h_prop["length"]) * scale_factor
					else:
						ch = float(h_prop) * scale_factor
					if ch > 0.1:
						ceil_height = min(ch, max_wall_height)
			
			var ceil_poly = CSGPolygon3D.new()
			ceil_poly.polygon = polygon
			ceil_poly.mode = CSGPolygon3D.MODE_DEPTH
			
			var ceil_depth = 0.1
			if area.has("ceiling_properties") and area["ceiling_properties"].has("thickness"):
				var t = float(area["ceiling_properties"]["thickness"]) * scale_factor
				if t > 0.001:
					ceil_depth = t
			if ceil_depth < 0.05:
				ceil_depth = 0.05

			ceil_poly.depth = ceil_depth
			ceil_poly.rotation.x = PI / 2 
			ceil_poly.position.y = layer_altitude + ceil_height
			_apply_polygon_uv_world_scale(ceil_poly, uv_polygon)
			
			# Ceiling Material
			if area.has("ceiling_properties") and area["ceiling_properties"].has("material"):
				var c_mat = create_material(area["ceiling_properties"]["material"])
				ceil_poly.material = c_mat
			else:
				var ceil_mat = StandardMaterial3D.new()
				ceil_mat.albedo_color = Color(0.82,0.82,0.82)
				ceil_poly.material = ceil_mat
			
			csg.add_child(ceil_poly)
			
			print("Added floor/ceiling for area: ", area_id, " Height: ", ceil_height)

func load_assets(data):
	if data.has("layers"):
		var layers_dict = data["layers"]
		
		# Determine which layers to process based on showAllFloors / selectedLayer
		var show_all_floors = data.get("showAllFloors", true)
		var selected_layer = data.get("selectedLayer", "")
		
		if show_all_floors:
			print("load_assets: showAllFloors is true — loading assets for ALL layers.")
		else:
			print("load_assets: showAllFloors is false — loading assets only for layer: ", selected_layer)
		
		for layer_id in layers_dict:
			# If showAllFloors is false, skip layers that don't match selectedLayer
			if not show_all_floors and selected_layer != "" and str(layer_id) != str(selected_layer):
				print("load_assets: Skipping layer: ", layer_id, " (not selected)")
				continue
			
			var layer = layers_dict[layer_id]
			var layer_alt = 0.0
			
			# When rendering a single layer (showAllFloors=false), place it at
			# ground level so assets don't float at the original altitude.
			if show_all_floors:
				if layer.has("altitude"):
					var alt_val = layer["altitude"]
					if typeof(alt_val) == TYPE_DICTIONARY and alt_val.has("length"):
						layer_alt = float(alt_val["length"]) * 0.01
					else:
						layer_alt = float(alt_val) * 0.01
			else:
				print("load_assets: Single-layer mode — placing assets at ground level")

			# Some payloads include both keys; don't use `elif` or one set gets skipped.
			if layer.has("items"):
				_load_layer_items(layer["items"], layer_alt, str(layer_id))
			if layer.has("assets"):
				_load_layer_items(layer["assets"], layer_alt, str(layer_id))
	elif data.has("assets"):
		_load_layer_items(data["assets"], 0.0, "root")
	elif data.has("items"):
		_load_layer_items(data["items"], 0.0, "root")

func _resolve_local_model_path(path: String) -> String:
	if path == "" or path == "null" or path == "None":
		return ""

	# Normalize Windows backslashes to forward slashes
	var normalized = str(path).replace("\\", "/")

	# Direct path hit
	if FileAccess.file_exists(normalized):
		return normalized

	# Common relative variants
	if FileAccess.file_exists("res://" + normalized):
		return "res://" + normalized
	if FileAccess.file_exists("./" + normalized):
		return "./" + normalized

	# Filename-based fallback lookup (works for URL/relative inputs)
	var filename = normalized.get_file()
	if filename == "":
		return ""
	var filename_base = filename.get_basename()
	var parent_folder_hint = normalized.get_base_dir().get_file()

	var project_root = ProjectSettings.globalize_path("res://")
	var parent_dir = project_root.get_base_dir()  # one level up from godot_project/
	var asset_downloads = parent_dir.path_join("asset_downloads")
	var sibling_asset_downloads = ""
	if parent_dir.ends_with(" - staging"):
		var non_staging_root = parent_dir.substr(0, parent_dir.length() - " - staging".length())
		sibling_asset_downloads = non_staging_root.path_join("asset_downloads")

	var search_dirs = [
		asset_downloads,
		sibling_asset_downloads,
		"glb-assets",
		"res://glb-assets",
		"./glb-assets",
		"assets",
		"res://assets",
		"."
	]

	for dir in search_dirs:
		if dir == "":
			continue
		var candidate = dir + "/" + filename
		if FileAccess.file_exists(candidate):
			return candidate

	# Fuzzy fallback:
	# Many downloaded GLBs are renamed like "<hash>_Country_Sofa_HP.glb" while
	# JSON still references "glb-assets/.../Country_Sofa_LP.glb". Match by hints.
	var hints = []
	if parent_folder_hint != "":
		hints.append(parent_folder_hint.to_lower())

	var base_l = filename_base.to_lower()
	if base_l != "":
		hints.append(base_l)
		if base_l.ends_with("_lp"):
			hints.append(base_l.substr(0, base_l.length() - 3))
		if base_l.ends_with("_hp"):
			hints.append(base_l.substr(0, base_l.length() - 3))
		if base_l.ends_with("_low"):
			hints.append(base_l.substr(0, base_l.length() - 4))

	for dir in search_dirs:
		if dir == "":
			continue
		var da = DirAccess.open(dir)
		if da == null:
			continue

		da.list_dir_begin()
		var entry = da.get_next()
		while entry != "":
			if not da.current_is_dir() and entry.to_lower().ends_with(".glb"):
				var el = entry.to_lower()
				for h in hints:
					if h != "" and h in el:
						da.list_dir_end()
						return dir + "/" + entry
			entry = da.get_next()
		da.list_dir_end()

	return ""

func _load_layer_items(items, layer_altitude, layer_id := ""):
	for item_id in items:
		# Handle both Dictionary (item_id is key) and Array (item_id IS the item) formats
		var item
		if typeof(items) == TYPE_DICTIONARY:
			item = items[item_id]
		else:
			item = item_id  # When iterating an Array, item_id is the item itself
		if typeof(item) != TYPE_DICTIONARY:
			continue
		var model_path = ""
		
		# Prioritize local paths
		if item.has("local_glb_path"):
			model_path = item["local_glb_path"]
		elif item.has("local_path"):
			model_path = item["local_path"]
		elif item.has("asset_urls"):
			var urls = item["asset_urls"]
			if urls.has("GLB_File_URL") and urls["GLB_File_URL"] != null and str(urls["GLB_File_URL"]) != "":
				model_path = str(urls["GLB_File_URL"])
			elif urls.has("glb_Url") and urls["glb_Url"] != null and str(urls["glb_Url"]) != "":
				model_path = str(urls["glb_Url"])
			
		var resolved_model_path = _resolve_local_model_path(model_path)
		var load_status = "failed"
		var load_reason = ""
		var used_path = resolved_model_path

		if model_path == "":
			load_reason = "missing_model_path"
			_tracked_assets.append({
				"type": "item_asset",
				"id": item.get("id", "unknown"),
				"name": item.get("name", "unknown"),
				"layer_id": layer_id,
				"requested_path": model_path,
				"path": "",
				"status": load_status,
				"reason": load_reason,
				"position": item.get("x", 0),
			})
			continue

		if resolved_model_path == "":
			load_reason = "file_not_found"
			_tracked_assets.append({
				"type": "item_asset",
				"id": item.get("id", "unknown"),
				"name": item.get("name", "unknown"),
				"layer_id": layer_id,
				"requested_path": model_path,
				"path": model_path,
				"status": load_status,
				"reason": load_reason,
				"position": item.get("x", 0),
			})
			continue

		if resolved_model_path != "":
			var glTF = GLTFDocument.new()
			var glTFState = GLTFState.new()
			var error = glTF.append_from_file(resolved_model_path, glTFState)
			if error == OK:
				var node = glTF.generate_scene(glTFState)
				if node:
					add_child(node)
					
					# Transform
					var px = float(item.get("x", 0)) * 0.01
					var py = float(item.get("y", 0)) * 0.01 # This is Z in 3D
					var pz = 0.0
					
					# Check altitude
					if item.has("altitude"):
						var alt = item["altitude"]
						if typeof(alt) == TYPE_DICTIONARY and alt.has("length"):
							pz = float(alt["length"]) * 0.01
						else:
							pz = float(alt) * 0.01
					elif item.has("properties") and item["properties"].has("altitude"):
						var alt = item["properties"]["altitude"]
						if typeof(alt) == TYPE_DICTIONARY and alt.has("length"):
							pz = float(alt["length"]) * 0.01
						else:
							pz = float(alt) * 0.01
						
					pz += layer_altitude
					node.position = Vector3(px, pz, py)
					
					var rot = 0.0
					if item.has("rotation"):
						rot = float(item["rotation"])
					elif item.has("properties") and item["properties"].has("rotation"):
						rot = float(item["properties"]["rotation"])
						
					node.rotation.y = -deg_to_rad(rot)
					
					# Asset Size Creation Logic
					var props = {}
					if item.has("properties") and typeof(item["properties"]) == TYPE_DICTIONARY:
						props = item["properties"]
						
					var raw_width = props.get("width", 100)
					var raw_depth = props.get("depth", 100)
					var raw_height = props.get("height", 100)
					
					var target_w = get_dimension_value(raw_width) * 0.01
					var target_d = get_dimension_value(raw_depth) * 0.01
					var target_h = get_dimension_value(raw_height) * 0.01
					
					var default_w = get_dimension_value(100) * 0.01
					var default_h = get_dimension_value(100) * 0.01
					var default_d = get_dimension_value(100) * 0.01
					
					var is_user_resized = (abs(target_w - default_w) > 0.001 or abs(target_h - default_h) > 0.001 or abs(target_d - default_d) > 0.001)
					
					var aabb = _get_hierarchy_aabb(node)
					var dims = aabb.size
					if dims.x == 0: dims.x = 1.0
					if dims.y == 0: dims.y = 1.0
					if dims.z == 0: dims.z = 1.0
					
					# Node scale
					var scale_x = target_w / dims.x
					var scale_y = target_h / dims.y # Godot Y is Blender Z (height)
					var scale_z = target_d / dims.z # Godot Z is Blender Y (depth)
					
					var scale_x_swapped = target_w / dims.z
					var scale_z_swapped = target_d / dims.x
					
					var diff_normal = abs(scale_x - scale_z)
					var diff_swapped = abs(scale_x_swapped - scale_z_swapped)
					
					var final_scale = Vector3.ONE
					
					if diff_swapped < diff_normal and diff_swapped < 0.3:
						final_scale = Vector3(scale_x_swapped, scale_y, scale_z_swapped)
					else:
						var avg_xz_scale = (scale_x + scale_z) / 2.0
						
						var item_type = str(item.get("type", "")).to_lower()
						var item_name = str(item.get("name", "")).to_lower()
						var full_desc = item_type + " " + item_name
						
						var is_ceiling_light = ("hanging" in full_desc or "chandelier" in full_desc or "ceiling_light" in full_desc or "ceilinglight" in full_desc or "lamp" in full_desc or "light" in full_desc) and str(item.get("mounting_type", "")).to_lower() == "ceiling_mount"
						
						var is_thin_wall_item = ("frame" in full_desc or "picture" in full_desc or "art" in full_desc or "painting" in full_desc or "mirror" in full_desc or "curtain" in full_desc or "drape" in full_desc or "tv" in full_desc or "television" in full_desc or "monitor" in full_desc or "screen" in full_desc)
						
						var is_tall_appliance = ("fridge" in full_desc or "refrigerat" in full_desc or "freezer" in full_desc or "shower" in full_desc or "bathtub" in full_desc or "bath_tub" in full_desc)
						
						if not is_user_resized and not is_ceiling_light and not is_thin_wall_item and not is_tall_appliance:
							var height_diff_ratio = 0.0
							if avg_xz_scale > 0:
								height_diff_ratio = abs(scale_y - avg_xz_scale) / avg_xz_scale
							
							if height_diff_ratio > 0.2 or scale_y > 1.5:
								scale_y = avg_xz_scale
								
						final_scale = Vector3(scale_x, scale_y, scale_z)
					
					node.scale = final_scale
					# Keep the asset standing on the target floor altitude (instead of
					# centering pivot at floor), which prevents sinking below rooms.
					var bounds_y_min = aabb.position.y * final_scale.y
					node.position.y = pz - bounds_y_min
					
					# Force black for exterior windows if no materials mapping exists
					if item.get("is_exterior_black", false):
						var meshes = _get_all_meshes(node)
						var black_mat = StandardMaterial3D.new()
						black_mat.albedo_color = Color.BLACK
						black_mat.transparency = BaseMaterial3D.TRANSPARENCY_DISABLED
						for m in meshes:
							if not m.mesh: continue
							for i in range(m.mesh.get_surface_count()):
								m.set_surface_override_material(i, black_mat)
						print("Force-overrode all materials to BLACK for exterior item: ", item_id)

					# Asset Material Override (Color and Scale/Repeat)
					if item.has("materials") and typeof(item["materials"]) == TYPE_DICTIONARY:
						var item_mats = item["materials"]
						var meshes = _get_all_meshes(node)
						for m in meshes:
							if not m.mesh: continue
							for i in range(m.mesh.get_surface_count()):
								var mat = m.get_active_material(i)
								if mat and mat is StandardMaterial3D:
									var mat_name = str(mat.resource_name)
									var surface_name = str(m.mesh.surface_get_name(i))
									var mat_name_l = mat_name.to_lower()
									var surface_name_l = surface_name.to_lower()

									var matched_key = ""
									for key in item_mats:
										var key_l = str(key).to_lower()
										if key_l == "":
											continue
										if (mat_name_l != "" and (key_l in mat_name_l or mat_name_l in key_l)) \
										or (surface_name_l != "" and (key_l in surface_name_l or surface_name_l in key_l)):
											matched_key = key
											break

									# Fallback for assets where glTF strips names: if only one material override
									# is provided, apply it to this surface.
									if matched_key == "" and item_mats.size() == 1:
										matched_key = item_mats.keys()[0]

									if matched_key != "":
										var mat_opt = item_mats[matched_key]
										if typeof(mat_opt) == TYPE_DICTIONARY:
											var new_mat = mat.duplicate()
											var modified = false

											var is_color_edited = mat_opt.get("isColorEdited", false) or item.get("is_exterior_black", false)
											if is_color_edited:
												var c_str = str(mat_opt.get("color", "ffffff")).strip_edges()
												if item.get("is_exterior_black", false):
													c_str = "#000000"
												if c_str.length() >= 3 and not c_str.begins_with("#"):
													c_str = "#" + c_str
												if c_str.is_valid_html_color():
													new_mat.albedo_color = Color(c_str)
												new_mat.albedo_texture = null
												modified = true

											# Apply texture maps when color is not explicitly overriding texture.
											if not is_color_edited:
												var map_url = ""
												if mat_opt.has("mapUrl") and mat_opt["mapUrl"] != null and str(mat_opt["mapUrl"]) != "":
													map_url = str(mat_opt["mapUrl"])
												elif mat_opt.has("texture_urls") and typeof(mat_opt["texture_urls"]) == TYPE_ARRAY and mat_opt["texture_urls"].size() > 0:
													map_url = str(mat_opt["texture_urls"][0])
												if map_url != "":
													var albedo_tex = load_texture_from_path(map_url)
													if albedo_tex:
														new_mat.albedo_texture = albedo_tex
														modified = true

												if mat_opt.has("normalUrl") and mat_opt["normalUrl"] != null and str(mat_opt["normalUrl"]) != "":
													var normal_tex = load_texture_from_path(str(mat_opt["normalUrl"]))
													if normal_tex:
														new_mat.normal_enabled = true
														new_mat.normal_texture = normal_tex
														modified = true

												if mat_opt.has("roughnessUrl") and mat_opt["roughnessUrl"] != null and str(mat_opt["roughnessUrl"]) != "":
													var rough_tex = load_texture_from_path(str(mat_opt["roughnessUrl"]))
													if rough_tex:
														new_mat.roughness_texture = rough_tex
														new_mat.roughness_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_GREEN
														modified = true

											# If exterior black, we also want to make it non-transparent if it was glass.
											if item.get("is_exterior_black", false):
												new_mat.transparency = BaseMaterial3D.TRANSPARENCY_DISABLED

											var scale_u = new_mat.uv1_scale.x
											var scale_v = new_mat.uv1_scale.y
											var changed_scale = false

											if mat_opt.has("repeat"):
												var r = mat_opt["repeat"]
												if typeof(r) == TYPE_ARRAY and r.size() >= 2:
													scale_u = float(r[0]) if float(r[0]) > 0 else 1.0
													scale_v = float(r[1]) if float(r[1]) > 0 else 1.0
													changed_scale = true

											if mat_opt.has("scale"):
												var s = mat_opt["scale"]
												if typeof(s) == TYPE_ARRAY and s.size() >= 2:
													scale_u = float(s[0]) if float(s[0]) > 0 else scale_u
													scale_v = float(s[1]) if float(s[1]) > 0 else scale_v
													changed_scale = true
												elif typeof(s) == TYPE_FLOAT or typeof(s) == TYPE_INT:
													var sf = float(s)
													if sf > 0:
														scale_u = sf
														scale_v = sf
														changed_scale = true
												elif typeof(s) == TYPE_STRING and s.is_valid_float():
													var sf = float(s)
													if sf > 0:
														scale_u = sf
														scale_v = sf
														changed_scale = true

											if changed_scale:
												new_mat.uv1_scale = Vector3(scale_u, scale_v, 1.0)
												modified = true

											if modified:
												m.set_surface_override_material(i, new_mat)
					
					print("Loaded asset: ", resolved_model_path, " final scale: ", final_scale)
					load_status = "loaded"
					load_reason = ""
				else:
					load_reason = "generate_scene_null"
			else:
				print("Failed to load GLTF: ", resolved_model_path, " Error: ", error)
				load_reason = "append_from_file_error_" + str(error)
		
		_tracked_assets.append({
			"type": "item_asset",
			"id": item.get("id", "unknown"),
			"name": item.get("name", "unknown"),
			"layer_id": layer_id,
			"requested_path": model_path,
			"path": used_path if used_path != "" else model_path,
			"status": load_status,
			"reason": load_reason,
			"position": item.get("x", 0), # Store original coords
		})

func _expand_polygon_outward(polygon: PackedVector2Array, offset: float) -> PackedVector2Array:
	# Expands a CCW polygon outward by 'offset' distance.
	# For each vertex, computes the bisector of the two adjacent outward edge normals,
	# then moves the vertex along the bisector by offset / cos(half_angle) so that
	# each edge is displaced exactly 'offset' units outward.
	var n = polygon.size()
	if n < 3 or offset < 0.0001:
		return polygon
	
	var expanded = PackedVector2Array()
	
	for i in range(n):
		var prev_pt = polygon[(i - 1 + n) % n]
		var curr_pt = polygon[i]
		var next_pt = polygon[(i + 1) % n]
		
		# Edge vectors
		var e1 = curr_pt - prev_pt  # incoming edge direction
		var e2 = next_pt - curr_pt  # outgoing edge direction
		
		# Outward normals for a CCW polygon: right-hand normal (dy, -dx)
		var n1 = Vector2(e1.y, -e1.x).normalized()
		var n2 = Vector2(e2.y, -e2.x).normalized()
		
		# Bisector direction
		var bisector = (n1 + n2)
		if bisector.length() < 0.0001:
			# Edges are nearly parallel (180° turn) — just use one normal
			bisector = n1
		else:
			bisector = bisector.normalized()
		
		# The perpendicular offset along the bisector to achieve 'offset' distance
		# from each edge: offset / cos(angle_between_normal_and_bisector)
		var cos_half = n1.dot(bisector)
		if abs(cos_half) < 0.1:
			cos_half = 0.1  # clamp to prevent extreme offsets at acute angles
		
		var move_dist = offset / cos_half
		expanded.append(curr_pt + bisector * move_dist)
	
	return expanded

func _apply_polygon_uv_world_scale(poly: CSGPolygon3D, polygon: PackedVector2Array) -> void:
	# Normalize polygon UV space to [0,1] so material repeat behaves the same
	# as walls (CSGBox3D per-face UVs).
	# Without this, CSGPolygon3D uses raw vertex coords as UVs (meter-space),
	# so repeat=2 becomes "2 tiles per meter" instead of "2 tiles per face".
	if polygon.size() < 3:
		return

	var min_x = 1e20
	var max_x = -1e20
	var min_y = 1e20
	var max_y = -1e20
	for p in polygon:
		min_x = min(min_x, p.x)
		max_x = max(max_x, p.x)
		min_y = min(min_y, p.y)
		max_y = max(max_y, p.y)

	var span_u = max(0.001, max_x - min_x)
	var span_v = max(0.001, max_y - min_y)

	var uv = Transform2D.IDENTITY
	uv = uv.scaled(Vector2(1.0 / span_u, 1.0 / span_v))
	uv = uv.translated(-Vector2(min_x, min_y) / Vector2(span_u, span_v))
	poly.uv_xform = uv



func get_dimension_value(prop_value, default_val = 100.0) -> float:
	if typeof(prop_value) == TYPE_ARRAY and prop_value.size() > 0:
		return float(prop_value[0])
	elif typeof(prop_value) == TYPE_DICTIONARY:
		return float(prop_value.get("length", default_val))
	elif typeof(prop_value) == TYPE_INT or typeof(prop_value) == TYPE_FLOAT:
		return float(prop_value)
	elif typeof(prop_value) == TYPE_STRING and prop_value.is_valid_float():
		return float(prop_value)
	else:
		return float(default_val)

func _get_hierarchy_aabb(node: Node3D) -> AABB:
	var aabb := AABB()
	var first = true
	var meshes = _get_all_meshes(node)
	if meshes.size() == 0:
		return AABB(Vector3.ZERO, Vector3.ONE)
	
	for mesh_inst in meshes:
		var transform = node.global_transform.affine_inverse() * mesh_inst.global_transform
		var mesh_aabb = transform * mesh_inst.get_aabb()
		if first:
			aabb = mesh_aabb
			first = false
		else:
			aabb = aabb.merge(mesh_aabb)
			
	return aabb

func _get_all_meshes(node: Node) -> Array:
	var meshes = []
	if node is MeshInstance3D:
		meshes.append(node)
	for child in node.get_children():
		meshes.append_array(_get_all_meshes(child))
	return meshes

func create_material(mat_data):
	var mat = StandardMaterial3D.new()

	# Always disable backface culling
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED

	# ── PBR defaults for photorealistic look ───────────────────────────────
	mat.roughness = mat_data.get("roughness", 0.65)	
	mat.metallic = mat_data.get("metallic", 0.0)
	mat.metallic_specular = 0.5
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	mat.specular_mode = BaseMaterial3D.SPECULAR_SCHLICK_GGX

	# --- Parse Color ---
	var base_color = Color(0.82,0.82,0.82)
	if mat_data.has("color"):
		var c_str = str(mat_data["color"]).strip_edges()
		# Add # if missing
		if c_str.length() >= 3 and not c_str.begins_with("#"):
			c_str = "#" + c_str
			
		if c_str.is_valid_html_color():
			base_color = Color(c_str)
			# print("  Material Color Parsed: ", c_str, " -> ", base_color)
		else:
			print("  Warning: Invalid color string: ", c_str)
		
	mat.albedo_color = base_color
	
	# --- Textures ---
	# If the user has explicitly set a color (isColorEdited), skip loading the texture
	# so the custom color is preserved and not overridden by the texture map.
	var is_color_edited = mat_data.get("isColorEdited", false)
	
	if not is_color_edited:
		var map_url = ""
		if mat_data.has("mapUrl") and mat_data["mapUrl"] != null and str(mat_data["mapUrl"]) != "":
			map_url = str(mat_data["mapUrl"])
		elif mat_data.has("texture_urls") and typeof(mat_data["texture_urls"]) == TYPE_ARRAY and mat_data["texture_urls"].size() > 0:
			map_url = str(mat_data["texture_urls"][0])
		
		if map_url != "":
			var tex = load_texture_from_path(map_url)
			if tex:
				mat.albedo_texture = tex
				# DO NOT use triplanar. CSG geometry (CSGBox3D for walls, CSGPolygon3D
				# for floors/ceilings) generates built-in UV coordinates per face [0,1].
				# Triplanar projects textures in world-space per meter, which causes
				# extreme tiling on walls that are 5-10+ meters long (moiré patterns).
				# The frontend (Three.js) also uses per-face UVs, so raw repeat values
				# applied to built-in UVs will match the frontend exactly.
			else:
				pass
		
		if mat_data.has("normalUrl") and mat_data["normalUrl"] != null and str(mat_data["normalUrl"]) != "":
			var tex = load_texture_from_path(str(mat_data["normalUrl"]))
			if tex:
				mat.normal_enabled = true
				mat.normal_texture = tex
				
		if mat_data.has("roughnessUrl") and mat_data["roughnessUrl"] != null and str(mat_data["roughnessUrl"]) != "":
			var tex = load_texture_from_path(str(mat_data["roughnessUrl"]))
			if tex:
				mat.roughness_texture = tex
				mat.roughness_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_GREEN
	
	# --- UV Scale / Repeat ---
	# Use raw repeat values directly — NO multiplier.
	# CSGBox3D (walls): has per-face UVs [0,1], so repeat=2 means 2 tiles per face.
	# CSGPolygon3D (floors/ceilings): UVs based on polygon vertex coords (meters).
	# This matches Three.js frontend behavior where texture.repeat.set(u, v) controls
	# the number of tiles across each face's UV range.
	var scale_u = 1.0
	var scale_v = 1.0
	
	if mat_data.has("repeat"):
		var r = mat_data["repeat"]
		if typeof(r) == TYPE_ARRAY and r.size() >= 2:
			scale_u = float(r[0]) if float(r[0]) > 0 else 1.0
			scale_v = float(r[1]) if float(r[1]) > 0 else 1.0
			
	if mat_data.has("scale"):
		var s = mat_data["scale"]
		if typeof(s) == TYPE_ARRAY and s.size() >= 2:
			scale_u = float(s[0]) if float(s[0]) > 0 else scale_u
			scale_v = float(s[1]) if float(s[1]) > 0 else scale_v
		elif typeof(s) == TYPE_FLOAT or typeof(s) == TYPE_INT:
			var sf = float(s)
			if sf > 0:
				scale_u = sf
				scale_v = sf
		elif typeof(s) == TYPE_STRING and s.is_valid_float():
			var sf = float(s)
			if sf > 0:
				scale_u = sf
				scale_v = sf

	mat.uv1_scale = Vector3(scale_u, scale_v, 1.0)
		
	return mat

func _resolve_local_texture(url: String) -> String:
	# Try to find a locally downloaded texture corresponding to a URL.
	if url == "" or url == "null": return ""
	
	# Normalize Windows backslashes to forward slashes (Godot FileAccess requires /)
	var normalized = url.replace("\\", "/")
	
	# If already a local path and exists - use it directly
	if FileAccess.file_exists(normalized): return normalized
	
	# Extract filename from URL
	var filename = normalized.get_file()
	if filename == "": return ""
	
	# Search in common texture directories
	# asset_downloads is the primary location for locally cached textures
	var project_root = ProjectSettings.globalize_path("res://")
	var parent_dir = project_root.get_base_dir()  # one level up from godot_project/
	var asset_downloads = parent_dir.path_join("asset_downloads")
	
	var search_dirs = [
		asset_downloads,
		"textures",
		"res://textures",
		"./textures",
		"downloaded_textures",
		"assets/textures",
		"res://assets",
		"."
	]
	
	for dir in search_dirs:
		var candidate = dir + "/" + filename
		if FileAccess.file_exists(candidate):
			return candidate
	
	return ""

func load_texture_from_path(path):
	if path == "" or path == "null" or path == "None": return null
	
	# Normalize Windows backslashes to forward slashes (Godot FileAccess requires /)
	var norm_path = str(path).replace("\\", "/")
	
	# Direct file check with normalized path
	if FileAccess.file_exists(norm_path):
		return load_image_texture(norm_path)
	
	# Resolve via helper (URL -> local filename search / asset_downloads lookup)
	var local = _resolve_local_texture(norm_path)
	if local != "":
		return load_image_texture(local)
		
	# If it's an HTTP URL that isn't downloaded yet, skip silently (color fallback is already set)
	if norm_path.begins_with("http"):
		return null
		
	print("Texture not found (skipped): ", norm_path)
	return null

func load_image_texture(path):
	var img = Image.load_from_file(path)
	if img:
		return ImageTexture.create_from_image(img)
	return null
