extends Node3D

# image_glb_creation.gd
# Handles the construction of the scene: Architecture (Walls, Floors, Ceilings) and Asset Loading.
var _tracked_assets = []
var day_render = true
var lighting_profile = "day"

func build_scene(data):
	lighting_profile = _resolve_lighting_profile(data)
	day_render = lighting_profile != "night"
	
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
	build_structures(geom_data)
	load_assets(geom_data)
	# setup_camera(data) - Moved to render_image.gd

func setup_lighting(data, geom_data = {}):
	var lighting = _get_lighting_profile_settings()

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
	window_light.light_energy = lighting["window_energy"]
	window_light.omni_range = max(room_extent.x, room_extent.y) * 2.1 + 6.0
	window_light.light_color = lighting["window_color"]
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
		dir_light.light_energy = l.get("intensity", lighting["dir_energy"])
		if l.has("color") and typeof(l["color"]) == TYPE_DICTIONARY:
			var c = l["color"]
			dir_light.light_color = Color(
				float(c.get("r", lighting["dir_color"].r)),
				float(c.get("g", lighting["dir_color"].g)),
				float(c.get("b", lighting["dir_color"].b))
			)
		else:
			dir_light.light_color = lighting["dir_color"]
		if l.has("position"):
			dir_light.position = parse_vec3(l["position"])
			if l.has("target"):
				dir_light.look_at(parse_vec3(l["target"]))
			else:
				dir_light.look_at(light_anchor)
	else:
		# Camera-aware defaults: keep key light roughly behind/above the camera direction.
		if lighting_profile == "day":
			dir_light.light_energy = lighting["dir_energy"]
			dir_light.light_color  = lighting["dir_color"]
			dir_light.position     = light_anchor - cam_forward * 12.0 + cam_right * 6.0 + Vector3.UP * 16.0
			dir_light.look_at(light_anchor)
		elif lighting_profile == "sunset":
			dir_light.light_energy = lighting["dir_energy"]
			dir_light.light_color  = lighting["dir_color"]
			dir_light.position     = light_anchor - cam_forward * 9.0 + cam_right * 9.0 + Vector3.UP * 8.5
			dir_light.look_at(light_anchor + Vector3(0, -1.4, 0))
		else:
			dir_light.light_energy = lighting["dir_energy"]
			dir_light.light_color  = lighting["dir_color"]
			dir_light.position     = light_anchor - cam_forward * 10.0 - cam_right * 4.0 + Vector3.UP * 18.0
			dir_light.look_at(light_anchor)

	add_child(dir_light)

	# ── Sky / Environment ──────────────────────────────────────────────────
	var env = Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = Sky.new()

	var exr_path = ProjectSettings.globalize_path(lighting["sky_exr"])

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
		sky_mat.sky_top_color         = lighting["sky_top_color"]
		sky_mat.sky_horizon_color     = lighting["sky_horizon_color"]
		sky_mat.ground_bottom_color   = lighting["ground_bottom_color"]
		sky_mat.ground_horizon_color  = lighting["ground_horizon_color"]
		sky_mat.sun_angle_max         = lighting["sun_angle_max"]
		sky_mat.sun_curve             = lighting["sun_curve"]
		env.sky.sky_material = sky_mat
		print("Using fallback procedural sky")

	# ── Ambient Light ──────────────────────────────────────────────────────
	# Use color ambient for enclosed rooms to avoid sky-light leaking at wall/ceiling corners.
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = lighting["ambient_color"]
	env.ambient_light_energy = lighting["ambient_energy"]

	# ── Tone Mapping ──────────────────────────────────────────────────────
	# CRITICAL: exposure must be set high enough that headless render is NOT black.
	# CameraAttributesPhysical is intentionally NOT used (causes black in headless).
	env.tonemap_mode     = Environment.TONE_MAPPER_FILMIC
	env.tonemap_exposure = lighting["tonemap_exposure"]
	env.tonemap_white    = lighting["tonemap_white"]

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
	env.glow_intensity     = lighting["glow_intensity"]
	env.glow_bloom         = lighting["glow_bloom"]
	env.glow_hdr_threshold = lighting["glow_hdr_threshold"]
	env.glow_hdr_scale     = lighting["glow_hdr_scale"]
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
	if lighting_profile == "day" or lighting_profile == "sunset":
		# Warm overhead fill — simulates ceiling/sky bounce
		var fill = OmniLight3D.new()
		fill.name           = "FillLight"
		fill.position       = Vector3(room_center.x, room_ceiling_y - 0.55, room_center.z)
		fill.light_energy   = lighting["fill_energy"]
		fill.omni_range     = max(room_extent.x, room_extent.y) * 2.5 + 10.0
		fill.light_color    = lighting["fill_color"]
		fill.light_specular = 0.0
		fill.shadow_enabled = false
		add_child(fill)

		# Symmetric front/back fill avoids camera-direction brightness splitting.
		var fill_offset = max(room_extent.x, room_extent.y) * 0.28
		var fill_front = OmniLight3D.new()
		fill_front.name           = "FrontFillLight"
		fill_front.position       = Vector3(room_center.x, room_ceiling_y - 0.9, room_center.z) + cam_forward * fill_offset
		fill_front.light_energy   = lighting["front_back_fill_energy"]
		fill_front.omni_range     = max(room_extent.x, room_extent.y) * 1.6 + 4.0
		fill_front.light_color    = lighting["front_back_fill_color"]
		fill_front.light_specular = 0.0
		fill_front.shadow_enabled = false
		add_child(fill_front)

		var fill_back = OmniLight3D.new()
		fill_back.name           = "BackFillLight"
		fill_back.position       = Vector3(room_center.x, room_ceiling_y - 0.9, room_center.z) - cam_forward * fill_offset
		fill_back.light_energy   = lighting["front_back_fill_energy"]
		fill_back.omni_range     = max(room_extent.x, room_extent.y) * 1.6 + 4.0
		fill_back.light_color    = lighting["front_back_fill_color"]
		fill_back.light_specular = 0.0
		fill_back.shadow_enabled = false
		add_child(fill_back)
	else:
		var fill = OmniLight3D.new()
		fill.name           = "FillLight"
		fill.position       = Vector3(room_center.x, room_ceiling_y - 0.55, room_center.z)
		fill.light_energy   = lighting["fill_energy"]
		fill.omni_range     = max(room_extent.x, room_extent.y) * 2.2 + 7.0
		fill.light_color    = lighting["fill_color"]
		fill.light_specular = 0.0
		fill.shadow_enabled = false
		add_child(fill)

	print("Photorealistic lighting setup complete. profile=", lighting_profile)

func parse_vec3(d):
	if typeof(d) == TYPE_DICTIONARY:
		return Vector3(d.get("x", 0), d.get("y", 0), d.get("z", 0))
	return Vector3.ZERO

func _resolve_lighting_profile(data: Dictionary) -> String:
	var string_keys = ["lighting_profile", "time_of_day", "render_time", "environment_preset"]
	for key in string_keys:
		if data.has(key):
			var value = str(data[key]).to_lower().strip_edges()
			if "sunset" in value or "dusk" in value or "evening" in value:
				return "sunset"
			if "night" in value:
				return "night"
			if "day" in value or "morning" in value or "afternoon" in value:
				return "day"

	if bool(data.get("sunset_render", false)):
		return "sunset"
	if bool(data.get("night_render", false)):
		return "night"
	if data.has("day_render"):
		return "day" if bool(data["day_render"]) else "night"
	return "day"

func _get_lighting_profile_settings() -> Dictionary:
	match lighting_profile:
		"night":
			return {
				"sky_exr": "res://night.exr",
				"window_energy": 0.03,
				"window_color": Color(0.72, 0.80, 1.0),
				"dir_energy": 0.06,
				"dir_color": Color(0.55, 0.62, 0.90),
				"sky_top_color": Color(0.01, 0.02, 0.05),
				"sky_horizon_color": Color(0.04, 0.06, 0.10),
				"ground_bottom_color": Color(0.005, 0.005, 0.01),
				"ground_horizon_color": Color(0.03, 0.05, 0.08),
				"sun_angle_max": 10.0,
				"sun_curve": 0.05,
				"ambient_color": Color(0.20, 0.24, 0.34),
				"ambient_energy": 0.05,
				"tonemap_exposure": 0.45,
				"tonemap_white": 4.8,
				"glow_intensity": 0.26,
				"glow_bloom": 0.10,
				"glow_hdr_threshold": 1.15,
				"glow_hdr_scale": 2.1,
				"fill_energy": 0.05,
				"fill_color": Color(0.5, 0.55, 0.8),
				"front_back_fill_energy": 0.02,
				"front_back_fill_color": Color(0.58, 0.64, 0.88)
			}
		"sunset":
			return {
				"sky_exr": "res://day.exr",
				"window_energy": 0.18,
				"window_color": Color(1.0, 0.76, 0.56),
				"dir_energy": 1.65,
				"dir_color": Color(1.0, 0.63, 0.38),
				"sky_top_color": Color(0.20, 0.24, 0.42),
				"sky_horizon_color": Color(1.0, 0.56, 0.34),
				"ground_bottom_color": Color(0.09, 0.05, 0.03),
				"ground_horizon_color": Color(0.42, 0.22, 0.14),
				"sun_angle_max": 18.0,
				"sun_curve": 0.22,
				"ambient_color": Color(0.62, 0.50, 0.42),
				"ambient_energy": 0.28,
				"tonemap_exposure": 0.78,
				"tonemap_white": 4.6,
				"glow_intensity": 0.28,
				"glow_bloom": 0.11,
				"glow_hdr_threshold": 1.10,
				"glow_hdr_scale": 2.15,
				"fill_energy": 0.15,
				"fill_color": Color(1.0, 0.80, 0.60),
				"front_back_fill_energy": 0.07,
				"front_back_fill_color": Color(1.0, 0.82, 0.64)
			}
		_:
			return {
				"sky_exr": "res://day.exr",
				"window_energy": 0.06,
				"window_color": Color(1.0, 0.95, 0.85),
				"dir_energy": 1.0,
				"dir_color": Color(1.0, 0.96, 0.88),
				"sky_top_color": Color(0.25, 0.40, 0.78),
				"sky_horizon_color": Color(0.75, 0.80, 0.90),
				"ground_bottom_color": Color(0.08, 0.06, 0.04),
				"ground_horizon_color": Color(0.30, 0.28, 0.22),
				"sun_angle_max": 30.0,
				"sun_curve": 0.15,
				"ambient_color": Color(0.76, 0.77, 0.78),
				"ambient_energy": 0.15,
				"tonemap_exposure": 0.72,
				"tonemap_white": 5.0,
				"glow_intensity": 0.22,
				"glow_bloom": 0.08,
				"glow_hdr_threshold": 1.35,
				"glow_hdr_scale": 1.9,
				"fill_energy": 0.10,
				"fill_color": Color(1.0, 0.97, 0.90),
				"front_back_fill_energy": 0.03,
				"front_back_fill_color": Color(0.99, 0.97, 0.93)
			}

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
		var keep_all_layers = show_all_floors
		
		if show_all_floors:
			print("showAllFloors is true — building ALL layers.")
		else:
			print("showAllFloors is false — building only selected layer: ", selected_layer)
		
		for layer_id in data["layers"]:
			# If showAllFloors is false, skip layers that don't match selectedLayer
			if not keep_all_layers and selected_layer != "" and str(layer_id) != str(selected_layer):
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
				# ── render_mode forwarded by scene_optimizer.py ────────────────
				# "INTERIOR" = camera inside a room, "EXTERIOR" = camera outside.
				# Falls back to top-level fp_data render_mode, then "INTERIOR".
				var layer_render_mode = layer.get("render_mode",
					data.get("render_mode", "INTERIOR"))
				print("Layer ", layer_id, " render_mode: ", layer_render_mode)
				_build_layer_geometry(layer, layer_alt, show_all_floors, layer_render_mode)
	elif data.has("lines") and data.has("vertices"):
		print("Found lines/vertices in root data.")
		var root_render_mode = data.get("render_mode", "INTERIOR")
		_build_layer_geometry(data, 0.0, data.get("showAllFloors", true), root_render_mode)
	else:
		print("Warning: No lines or vertices found in floor plan data.")

# ─────────────────────────────────────────────────────────────────────────────
# _get_wall_facing_direction
# ─────────────────────────────────────────────────────────────────────────────
# Mirrors the frontend's getWallFacingDirection() from Wall3D.tsx exactly.
#
# Frontend logic recap:
#   1. Find the area (room polygon) that contains BOTH wall vertices.
#   2. Compute the wall's left (+Z-local) and right (-Z-local) perpendicular normals.
#   3. Sample a point 5 cm to the left and 5 cm to the right of the wall midpoint.
#   4. Run a 2D point-in-polygon test on both samples.
#   5. Return "inner-left"  if the left  sample is inside the polygon.
#      Return "inner-right" if the right sample is inside the polygon.
#
# In Godot we work in the 2D plan space (x,y = frontend x,y before converting
# to metres), so all coordinates stay in centimetres to match the frontend.
#
# Returns: "inner-left", "inner-right", or "" (unknown / no area found).
func _get_wall_facing_direction(line: Dictionary, vertices: Dictionary, areas: Dictionary) -> String:
	var v1_id = str(line["vertices"][0])
	var v2_id = str(line["vertices"][1])
	if not vertices.has(v1_id) or not vertices.has(v2_id):
		return ""

	var vA = vertices[v1_id]
	var vB = vertices[v2_id]
	var ax = float(vA["x"]); var ay = float(vA["y"])
	var bx = float(vB["x"]); var by = float(vB["y"])

	# Wall direction vector (2D)
	var dir = Vector2(bx - ax, by - ay).normalized()

	# Left (+Z local) and right (-Z local) perpendicular normals
	var left_normal  = Vector2(-dir.y,  dir.x)
	var right_normal = Vector2( dir.y, -dir.x)

	# Wall midpoint
	var mid = Vector2((ax + bx) * 0.5, (ay + by) * 0.5)

	# Sample 5 cm to each side (matches frontend's addScaledVector(normal, 5))
	var left_sample  = mid + left_normal  * 5.0
	var right_sample = mid + right_normal * 5.0

	# ── Check ALL areas containing both wall vertices ─────────────────
	# For shared walls (between two rooms), different areas yield opposite
	# results.  We use the lexicographically smallest area_id to guarantee
	# a deterministic outcome regardless of dictionary iteration order
	# (which changes after scene-optimizer culling).
	var best_result   = ""
	var best_area_id  = ""

	for area_id in areas:
		var area = areas[area_id]
		if not area.has("vertices"):
			continue
		var area_verts = area["vertices"]
		var has_v1 = false
		var has_v2 = false
		for vid in area_verts:
			var vs = str(vid)
			if vs == v1_id: has_v1 = true
			if vs == v2_id: has_v2 = true
		if not (has_v1 and has_v2):
			continue

		# Build polygon from this area's vertices
		var area_polygon: Array = []
		for vid in area_verts:
			var vs = str(vid)
			if vertices.has(vs):
				var v = vertices[vs]
				area_polygon.append(Vector2(float(v["x"]), float(v["y"])))
		if area_polygon.size() < 3:
			continue

		var left_inside  = _is_point_in_polygon(left_sample,  area_polygon)
		var right_inside = _is_point_in_polygon(right_sample, area_polygon)

		var result = ""
		if left_inside and not right_inside:
			result = "inner-left"
		elif right_inside and not left_inside:
			result = "inner-right"
		else:
			continue   # Both inside or neither — skip this area

		# Deterministic: pick the area with smallest area_id
		if best_result == "" or area_id < best_area_id:
			best_result  = result
			best_area_id = area_id

	if best_result != "":
		return best_result

	# No area found — return empty (fallback to centroid in caller)
	return ""

# Ray-casting point-in-polygon test (mirrors frontend isPointInPolygon).
func _is_point_in_polygon(point: Vector2, polygon: Array) -> bool:
	var inside = false
	var n = polygon.size()
	var j = n - 1
	for i in range(n):
		var xi = polygon[i].x; var yi = polygon[i].y
		var xj = polygon[j].x; var yj = polygon[j].y
		var intersect = ((yi > point.y) != (yj > point.y)) and \
			(point.x < (xj - xi) * (point.y - yi) / (yj - yi) + xi)
		if intersect:
			inside = not inside
		j = i
	return inside

# ─────────────────────────────────────────────────────────────────────────────
# _build_layer_geometry  (updated — matches frontend inner/outer assignment)
# ─────────────────────────────────────────────────────────────────────────────
func _build_layer_geometry(layer_data, layer_altitude, show_all_floors = true, render_mode: String = "INTERIOR"):
	var csg = CSGCombiner3D.new()
	csg.use_collision = true
	add_child(csg)
	
	var lines    = layer_data["lines"]
	var vertices = layer_data["vertices"]
	var scale_factor = 0.01  # Convert cm → metres
	
	# Helper map for hole definitions
	var all_holes = {}
	if layer_data.has("holes"):
		all_holes = layer_data["holes"]

	# Areas dictionary — required by the facing-direction helper
	var areas = {}
	if layer_data.has("areas"):
		areas = layer_data["areas"]

	# Structures dictionary — used for floor openings and procedural meshes
	var structures = {}
	if layer_data.has("structures") and typeof(layer_data["structures"]) == TYPE_DICTIONARY:
		structures = layer_data["structures"]

	# ── Compute room centroid (used for inner_sign fallback only) ─────────
	# The centroid approach is a legacy fallback kept for walls that belong to
	# no named area. The primary facing-direction logic now uses per-area
	# polygon tests via _get_wall_facing_direction(), matching the frontend.
	var room_centroid = Vector3.ZERO
	var centroid_count = 0
	for area_id in areas:
		var area = areas[area_id]
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
	
	# ── 1. Build Walls ────────────────────────────────────────────────────
	for line_id in lines:
		var line = lines[line_id]
		if line.has("visible") and line["visible"] == false: continue
		if not line.has("vertices") or line["vertices"].size() < 2: continue
		
		var v1_id = str(line["vertices"][0])
		var v2_id = str(line["vertices"][1])
		if not vertices.has(v1_id) or not vertices.has(v2_id): continue
			
		var v1_data = vertices[v1_id]
		var v2_data = vertices[v2_id]
		
		var p1 = Vector3(float(v1_data["x"]), 0.0, float(v1_data["y"])) * scale_factor
		var p2 = Vector3(float(v2_data["x"]), 0.0, float(v2_data["y"])) * scale_factor
		
		var diff   = p2 - p1
		var length = diff.length()
		var center = (p1 + p2) / 2.0
		var height = 240.0 * scale_factor  # Default height
		
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
		var wall_pos   = Vector3(center.x, layer_altitude + height / 2.0, center.z)

		# ── Extend wall length at both ends to fill corner gaps ────────────
		var corner_length = length + wall_thickness

		# ── Determine inner/outer facing (mirrors frontend exactly) ───────
		#
		# Frontend:  getWallFacingDirection() → "inner-left" | "inner-right"
		#   "inner-left"  = the +Z face of the local wall geometry faces the room
		#   "inner-right" = the -Z face of the local wall geometry faces the room
		#
		# In Godot, after rotating the CSGBox3D by wall_angle around Y:
		#   The +Z face (local) becomes the face pointing toward the "left" 
		#   perpendicular of the wall direction.
		#   The -Z face (local) points the other way.
		#
		# We use _get_wall_facing_direction() (polygon point-in-polygon, identical
		# to the frontend) as the primary source. The centroid dot-product is a
		# fallback for walls not covered by any named area.
		var wall_facing = _get_wall_facing_direction(line, vertices, areas)

		# ── Fallback: centroid dot-product (legacy, area-free walls) ──────
		var inner_sign = 1.0  # +1 → +Z is inner, -1 → -Z is inner
		if wall_facing == "inner-left":
			inner_sign = 1.0
		elif wall_facing == "inner-right":
			inner_sign = -1.0
		else:
			# No area polygon found — fall back to centroid approach
			var wall_basis       = Basis(Vector3.UP, wall_angle)
			var wall_normal_world = wall_basis * Vector3(0, 0, 1)
			if has_centroid:
				var to_centroid = room_centroid - center
				to_centroid.y = 0.0
				if to_centroid.dot(wall_normal_world) < 0:
					inner_sign = -1.0
			wall_facing = "inner-left" if inner_sign > 0 else "inner-right"

		print("Wall ", line_id, " facing: ", wall_facing, " inner_sign: ", inner_sign)

		# ── Resolve inner/outer material data from the line ────────────────
		var inner_mat_data = _resolve_wall_face_material(line, "inner")
		var outer_mat_data = _resolve_wall_face_material(line, "outer")

		# ── Build structural wall (CSGBox3D inside csg combiner) ──────────
		# The full-thickness box is what gets holes subtracted from it.
		#
		# INTERIOR TEXTURE FIX:
		#   The structural wall's material is visible on ALL 6 faces of the
		#   CSGBox3D.  In INTERIOR mode the camera is inside the room, so it
		#   primarily sees the inner face.  We therefore apply the INNER
		#   material to the structural wall, and use separate thin overlay
		#   panels for both inner and outer faces.  This mirrors the
		#   frontend's multi-material approach (Wall3D.tsx: materials array
		#   assigns innerMaterial to both front & back slots 0 and 1).
		#
		#   EXTERIOR render → camera is outside.  The structural wall keeps
		#     the OUTER material with CULL_DISABLED (both faces visible).
		#     No overlay panels are needed.
		var wall = CSGBox3D.new()
		wall.size       = Vector3(corner_length, height, wall_thickness)
		wall.position   = wall_pos
		wall.rotation.y = wall_angle

		var outer_mat: StandardMaterial3D
		if outer_mat_data != null:
			outer_mat = create_material(outer_mat_data)
		else:
			outer_mat = StandardMaterial3D.new()
			outer_mat.albedo_color = Color(0.82, 0.82, 0.82)

		var inner_mat_for_wall: StandardMaterial3D
		if inner_mat_data != null:
			inner_mat_for_wall = create_material(inner_mat_data)
		else:
			inner_mat_for_wall = StandardMaterial3D.new()
			inner_mat_for_wall.albedo_color = Color(0.82, 0.82, 0.82)

		if render_mode == "INTERIOR":
			# INTERIOR: apply inner material on the structural wall.
			# The camera is inside and mainly sees the inner face. This
			# prevents outer textures (brick/stone) from bleeding through.
			inner_mat_for_wall.cull_mode = BaseMaterial3D.CULL_DISABLED
			wall.material = inner_mat_for_wall
		else:
			# Exterior: show outer material from both sides
			outer_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
			wall.material = outer_mat

		csg.add_child(wall)

		# ── Dual overlay panels (INTERIOR mode only) ──────────────────────
		#
		# To correctly render both inner and outer textures on their
		# respective faces (matching the frontend's per-face material
		# assignment), we create two thin overlay panels:
		#   1. Inner panel — on the room-facing side, with inner material
		#   2. Outer panel — on the exterior-facing side, with outer material
		#
		# Each panel is 3 mm thick, placed flush with the structural wall
		# surface, using CULL_BACK (only the outward-facing side renders).
		# This ensures the camera sees the correct texture regardless of
		# which side of the wall it is viewing.
		#
		# EXTERIOR render: skip — structural wall alone is sufficient.
		var inner_csg: CSGCombiner3D = null
		var outer_csg: CSGCombiner3D = null

		if render_mode == "INTERIOR":
			var face_t     = 0.003   # 3 mm — clears Z-fighting at close range
			var wall_basis = Basis(Vector3.UP, wall_angle)

			# ── Inner overlay panel ───────────────────────────────────────
			if inner_mat_data != null:
				var inner_offset = wall_thickness / 2.0 + face_t / 2.0
				var inner_world  = wall_pos + wall_basis * Vector3(0, 0, inner_offset * inner_sign)

				inner_csg = CSGCombiner3D.new()
				inner_csg.position   = inner_world
				inner_csg.rotation.y = wall_angle
				add_child(inner_csg)

				var inner_box = CSGBox3D.new()
				inner_box.size = Vector3(corner_length, height, face_t)
				var inner_mat  = create_material(inner_mat_data)
				inner_mat.cull_mode = BaseMaterial3D.CULL_BACK
				inner_box.material  = inner_mat
				inner_csg.add_child(inner_box)

			# ── Outer overlay panel ───────────────────────────────────────
			# Placed on the opposite side from the inner panel.
			# This ensures walls viewed from adjacent rooms or the exterior
			# show the correct outer texture instead of the inner material
			# that is now on the structural wall.
			if outer_mat_data != null:
				var outer_offset = wall_thickness / 2.0 + face_t / 2.0
				# Outer side is opposite to inner_sign
				var outer_world  = wall_pos + wall_basis * Vector3(0, 0, outer_offset * (-inner_sign))

				outer_csg = CSGCombiner3D.new()
				outer_csg.position   = outer_world
				outer_csg.rotation.y = wall_angle
				add_child(outer_csg)

				var outer_box = CSGBox3D.new()
				outer_box.size = Vector3(corner_length, height, face_t)
				var outer_panel_mat = create_material(outer_mat_data)
				outer_panel_mat.cull_mode = BaseMaterial3D.CULL_BACK
				outer_box.material  = outer_panel_mat
				outer_csg.add_child(outer_box)

		# ── Build Holes (Doors / Windows) ─────────────────────────────────
		if line.has("holes"):
			for hole_id in line["holes"]:
				var hid = str(hole_id)
				if not all_holes.has(hid): continue
				var hole = all_holes[hid]
				
				var h_width  = get_dimension_value(hole.get("width",    0)) * scale_factor
				var h_height = get_dimension_value(hole.get("height",   0)) * scale_factor
				var h_alt    = get_dimension_value(hole.get("altitude", 0)) * scale_factor
				
				if hole.has("properties"):
					var props = hole["properties"]
					if props.has("width"):    h_width  = get_dimension_value(props["width"])    * scale_factor
					if props.has("height"):   h_height = get_dimension_value(props["height"])   * scale_factor
					if props.has("altitude"): h_alt    = get_dimension_value(props["altitude"]) * scale_factor

				var offset_ratio = 0.5
				if hole.has("offset"):
					offset_ratio = float(hole["offset"])
				elif hole.has("properties") and hole["properties"].has("offset"):
					offset_ratio = float(hole["properties"]["offset"])
					
				var hole_pos_xz = p1.lerp(p2, offset_ratio)
				var h_center_pos = hole_pos_xz
				h_center_pos.y   = layer_altitude + h_alt + h_height / 2.0
				
				if h_width < 0.01 or h_height < 0.01: continue
				
				# 1. Cut through the structural wall
				var hole_csg = CSGBox3D.new()
				hole_csg.operation  = CSGBox3D.OPERATION_SUBTRACTION
				hole_csg.size       = Vector3(h_width, h_height, wall_thickness + 0.2)
				hole_csg.position   = h_center_pos
				hole_csg.rotation.y = wall_angle
				csg.add_child(hole_csg)
				print("Added hole cutter at: ", h_center_pos)
				
				# 2. Cut through the inner face panel
				if inner_csg != null:
					var inner_hole = CSGBox3D.new()
					inner_hole.operation = CSGBox3D.OPERATION_SUBTRACTION
					inner_hole.size      = Vector3(h_width, h_height, 0.5)
					var delta_w   = h_center_pos - inner_csg.position
					var local_pos = Basis(Vector3.UP, wall_angle).transposed() * delta_w
					inner_hole.position = local_pos
					inner_csg.add_child(inner_hole)

				# 2b. Cut through the outer face panel
				if outer_csg != null:
					var outer_hole = CSGBox3D.new()
					outer_hole.operation = CSGBox3D.OPERATION_SUBTRACTION
					outer_hole.size      = Vector3(h_width, h_height, 0.5)
					var delta_o   = h_center_pos - outer_csg.position
					var local_pos_o = Basis(Vector3.UP, wall_angle).transposed() * delta_o
					outer_hole.position = local_pos_o
					outer_csg.add_child(outer_hole)
				
				# 3. Exterior black blocker (if flagged)
				if hole.get("is_exterior_black", false):
					var blocker = CSGBox3D.new()
					blocker.size       = Vector3(h_width + 0.02, h_height + 0.02, 0.01)
					blocker.position   = h_center_pos
					blocker.rotation.y = wall_angle
					var black_mat = StandardMaterial3D.new()
					black_mat.albedo_color = Color.BLACK
					blocker.material = black_mat
					add_child(blocker)
					print("Added black visibility blocker for exterior hole: ", hid)
				
				# 4. Instantiate door/window asset GLB
				if hole.has("asset_urls"):
					var urls = hole["asset_urls"]
					var model_path = ""
					if urls.has("GLB_File_URL") and urls["GLB_File_URL"] != null:
						model_path = str(urls["GLB_File_URL"])
					elif urls.has("glb_Url") and urls["glb_Url"] != null:
						model_path = str(urls["glb_Url"])
						
					if model_path != "":
						if not FileAccess.file_exists(model_path):
							if FileAccess.file_exists("res://" + model_path): model_path = "res://" + model_path
							elif FileAccess.file_exists("./" + model_path):    model_path = "./" + model_path
						
						if FileAccess.file_exists(model_path):
							var glTF      = GLTFDocument.new()
							var glTFState = GLTFState.new()
							var error     = glTF.append_from_file(model_path, glTFState)
							if error == OK:
								var asset_node = glTF.generate_scene(glTFState)
								if asset_node:
									add_child(asset_node)
									
									var aabb = _get_hierarchy_aabb(asset_node)
									var dims = aabb.size
									if dims.x == 0: dims.x = 1.0
									if dims.y == 0: dims.y = 1.0
									if dims.z == 0: dims.z = 1.0
									
									var scale_x = h_width        / dims.x
									var scale_y = h_height       / dims.y
									var scale_z = wall_thickness / dims.z
									
									var bounds_y_min = aabb.position.y * scale_y
									
									asset_node.position   = h_center_pos
									asset_node.position.y = (layer_altitude + h_alt) - bounds_y_min
									asset_node.rotation.y = wall_angle
									
									var flip_x = -1.0 if hole.get("flipX", false) else 1.0
									var flip_z = -1.0 if hole.get("flipZ", false) else 1.0
									asset_node.scale = Vector3(scale_x * flip_x, scale_y, scale_z * flip_z)
									
									print("Loaded hole asset: ", model_path, " final scale: ", asset_node.scale)
							else:
								print("Failed to load hole GLTF: ", model_path, " Error: ", error)
						
						_tracked_assets.append({
							"type": "hole_asset",
							"id":   hole.get("id",   "unknown"),
							"name": hole.get("name", "unknown"),
							"path": model_path
						})

	# ── 2. Build Floors and Ceilings from Areas ────────────────────────────
	var max_wall_height    = 0.0
	var max_wall_thickness = 0.2 * scale_factor
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
		max_wall_height = 280.0 * scale_factor

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
			for v_id in area["vertices"]:
				var vs = str(v_id)
				if vertices.has(vs):
					var v = vertices[vs]
					polygon.append(Vector2(float(v["x"]) * scale_factor, float(v["y"]) * scale_factor))
			
			if polygon.size() < 3: continue
			
			# Enforce CCW winding
			var signed_area = 0.0
			for pi in range(polygon.size()):
				var pa = polygon[pi]
				var pb = polygon[(pi + 1) % polygon.size()]
				signed_area += (pa.x * pb.y - pb.x * pa.y)
			signed_area *= 0.5
			if signed_area < 0:
				polygon.reverse()

			var uv_polygon = polygon.duplicate()
			polygon = _expand_polygon_outward(polygon, max_wall_thickness / 2.0)
			
			# Floor
			var floor_depth = 0.1
			if area.has("floor_properties") and area["floor_properties"].has("thickness"):
				var t = float(area["floor_properties"]["thickness"]) * scale_factor
				if t > 0.001: floor_depth = t
			if floor_depth < 0.05:
				floor_depth = 0.05
			
			var floor_poly = CSGPolygon3D.new()
			floor_poly.polygon    = polygon
			floor_poly.mode       = CSGPolygon3D.MODE_DEPTH
			floor_poly.depth      = floor_depth
			floor_poly.rotation.x = PI / 2
			floor_poly.position.y = layer_altitude - floor_depth
			_apply_polygon_uv_world_scale(floor_poly, uv_polygon)
			
			if area.has("floor_properties") and area["floor_properties"].has("material"):
				floor_poly.material = create_material(area["floor_properties"]["material"])
			else:
				var floor_mat = StandardMaterial3D.new()
				floor_mat.albedo_color = Color(0.8, 0.8, 0.8)
				floor_poly.material    = floor_mat

			# Subtract floor openings that belong to this area before adding the slab.
			_add_floor_opening_cutters(csg, structures, str(area_id), layer_altitude, floor_depth, scale_factor)
			
			csg.add_child(floor_poly)
			
			# Ceiling
			var ceil_height = global_ceil_height
			if not force_uniform_ceiling_height:
				ceil_height = max_wall_height
				if area.has("ceiling_properties"):
					var cp = area["ceiling_properties"]
					if cp.has("height"):
						var ch = float(cp["height"]) * scale_factor
						if ch > 0.1: ceil_height = min(ch, max_wall_height)
				elif area.has("properties") and area["properties"].has("height"):
					var h_prop = area["properties"]["height"]
					var ch = 0.0
					if typeof(h_prop) == TYPE_DICTIONARY and h_prop.has("length"):
						ch = float(h_prop["length"]) * scale_factor
					else:
						ch = float(h_prop) * scale_factor
					if ch > 0.1: ceil_height = min(ch, max_wall_height)
			
			var ceil_depth = 0.1
			if area.has("ceiling_properties") and area["ceiling_properties"].has("thickness"):
				var t = float(area["ceiling_properties"]["thickness"]) * scale_factor
				if t > 0.001: ceil_depth = t
			if ceil_depth < 0.05:
				ceil_depth = 0.05

			var ceil_poly = CSGPolygon3D.new()
			ceil_poly.polygon    = polygon
			ceil_poly.mode       = CSGPolygon3D.MODE_DEPTH
			ceil_poly.depth      = ceil_depth
			ceil_poly.rotation.x = PI / 2
			ceil_poly.position.y = layer_altitude + ceil_height
			_apply_polygon_uv_world_scale(ceil_poly, uv_polygon)
			
			if area.has("ceiling_properties") and area["ceiling_properties"].has("material"):
				ceil_poly.material = create_material(area["ceiling_properties"]["material"])
			else:
				var ceil_mat = StandardMaterial3D.new()
				ceil_mat.albedo_color = Color(0.82, 0.82, 0.82)
				ceil_poly.material    = ceil_mat
			
			var is_visible = show_all_floors
			if area.has("ceiling_properties") and area["ceiling_properties"].has("isvisible"):
				is_visible = area["ceiling_properties"]["isvisible"]
			
			if not is_visible:
				continue

			csg.add_child(ceil_poly)
			print("Added floor/ceiling for area: ", area_id, " Height: ", ceil_height)

# ─────────────────────────────────────────────────────────────────────────────
# _resolve_wall_face_material
# ─────────────────────────────────────────────────────────────────────────────
# Returns a material-data dictionary for the requested face ("inner" or "outer").
#
# Lookup priority (mirrors the frontend's material resolution):
#   1. line["asset_urls"][face]           — texture set block (mapUrl, fallback_color, etc.)
#   2. line["inner_properties"]["material"] / line["outer_properties"]["material"]
#   3. null (caller uses default grey)
func _resolve_wall_face_material(line: Dictionary, face: String) -> Variant:
	var prop_key = face + "_properties"
	var other_face = "outer" if face == "inner" else "inner"
	var other_prop_key = other_face + "_properties"
	var has_face_material = false
	if line.has(prop_key) and typeof(line[prop_key]) == TYPE_DICTIONARY:
		var face_props = line[prop_key]
		has_face_material = face_props.has("material") and typeof(face_props["material"]) == TYPE_DICTIONARY
	if not has_face_material and line.has(other_prop_key) and typeof(line[other_prop_key]) == TYPE_DICTIONARY:
		var other_props = line[other_prop_key]
		has_face_material = other_props.has("material") and typeof(other_props["material"]) == TYPE_DICTIONARY

	# 1. asset_urls block  (same data the frontend wallTextures come from)
	if line.has("asset_urls") and typeof(line["asset_urls"]) == TYPE_DICTIONARY:
		var au = line["asset_urls"]
		if au.has(face) and typeof(au[face]) == TYPE_DICTIONARY:
			var block = au[face]
			# Build a unified mat_data dict that create_material() understands
			var mat_data = {}

			# Texture URL
			if block.has("mapUrl") and block["mapUrl"] != null and str(block["mapUrl"]) != "":
				mat_data["mapUrl"] = block["mapUrl"]
			elif block.has("texture_urls") and typeof(block["texture_urls"]) == TYPE_ARRAY and block["texture_urls"].size() > 0:
				mat_data["mapUrl"] = block["texture_urls"][0]

			# Normal map
			if block.has("normalUrl") and block["normalUrl"] != null and str(block["normalUrl"]) != "":
				mat_data["normalUrl"] = block["normalUrl"]

			# Roughness map
			if block.has("roughnessUrl") and block["roughnessUrl"] != null and str(block["roughnessUrl"]) != "":
				mat_data["roughnessUrl"] = block["roughnessUrl"]

			# Fallback colour (used when texture is absent or as tint)
			var fallback_color = block.get("fallback_color", "")
			if fallback_color == null or str(fallback_color).strip_edges() == "":
				fallback_color = ""
			if fallback_color != "":
				mat_data["color"] = str(fallback_color)

			# Texture scale (maps to UV repeat in create_material)
			if block.has("texture_scale_x") or block.has("texture_scale_y"):
				var sx = float(block.get("texture_scale_x", 1.0))
				var sy = float(block.get("texture_scale_y", 1.0))
				mat_data["scale"] = [sx if sx > 0 else 1.0, sy if sy > 0 else 1.0]
			elif block.has("repeat") and typeof(block["repeat"]) == TYPE_ARRAY:
				mat_data["repeat"] = block["repeat"]

			if mat_data.size() > 0:
				var has_texture = mat_data.has("mapUrl") or mat_data.has("normalUrl") or mat_data.has("roughnessUrl")
				var has_color = mat_data.has("color")
				if has_texture or (has_color and not has_face_material):
					return mat_data

	# 2. inner_properties / outer_properties
	if line.has(prop_key) and typeof(line[prop_key]) == TYPE_DICTIONARY:
		var props = line[prop_key]
		if props.has("material") and typeof(props["material"]) == TYPE_DICTIONARY:
			return props["material"]

	# 3. Cross-fallback: if requesting "outer" and only "inner" exists, use inner (and vice-versa)
	if line.has(other_prop_key) and typeof(line[other_prop_key]) == TYPE_DICTIONARY:
		var props = line[other_prop_key]
		if props.has("material") and typeof(props["material"]) == TYPE_DICTIONARY:
			return props["material"]

	return null

func build_structures(data):
	if data.has("layers"):
		var layers_dict    = data["layers"]
		var show_all_floors = data.get("showAllFloors", true)
		var selected_layer  = data.get("selectedLayer", "")

		for layer_id in layers_dict:
			if not show_all_floors and selected_layer != "" and str(layer_id) != str(selected_layer):
				continue

			var layer = layers_dict[layer_id]
			var layer_alt = 0.0
			if show_all_floors:
				if layer.has("altitude"):
					var alt_val = layer["altitude"]
					if typeof(alt_val) == TYPE_DICTIONARY and alt_val.has("length"):
						layer_alt = float(alt_val["length"]) * 0.01
					else:
						layer_alt = float(alt_val) * 0.01

			if layer.has("structures"):
				_load_layer_structures(layer["structures"], layer_alt, str(layer_id))
	elif data.has("structures"):
		_load_layer_structures(data["structures"], 0.0, "root")

func _load_layer_structures(structures, layer_altitude, layer_id := ""):
	for structure_id in structures:
		var structure
		if typeof(structures) == TYPE_DICTIONARY:
			structure = structures[structure_id]
		else:
			structure = structure_id
		if typeof(structure) != TYPE_DICTIONARY:
			continue
		if structure.has("visible") and structure["visible"] == false:
			continue
		_build_structure_mesh(structure, layer_altitude, layer_id)

func _build_structure_mesh(structure: Dictionary, layer_altitude: float, layer_id := "") -> void:
	var structure_type = str(structure.get("structure_type", structure.get("type", ""))).to_lower()
	if structure_type == "":
		return

	var props = {}
	if structure.has("properties") and typeof(structure["properties"]) == TYPE_DICTIONARY:
		props = structure["properties"]

	var scale_factor = 0.01
	var width  = get_dimension_value(props.get("width",  structure.get("width",  100))) * scale_factor
	var depth  = get_dimension_value(props.get("depth",  structure.get("depth",  100))) * scale_factor
	var height = get_dimension_value(props.get("height", structure.get("height", 100))) * scale_factor
	var altitude = get_dimension_value(props.get("altitude", structure.get("altitude", 0))) * scale_factor

	var root = Node3D.new()
	root.name = "Structure_%s_%s" % [structure_type, str(structure.get("id", "unknown"))]
	root.position = Vector3(
		float(structure.get("x", 0)) * scale_factor,
		layer_altitude + altitude,
		float(structure.get("y", 0)) * scale_factor
	)

	var rot = 0.0
	if structure.has("rotation"):
		rot = float(structure["rotation"])
	elif props.has("rotation"):
		rot = float(props["rotation"])
	root.rotation.y = -deg_to_rad(rot)
	root.scale = Vector3(
		-1.0 if bool(structure.get("flipX", false)) else 1.0,
		1.0,
		-1.0 if bool(structure.get("flipZ", false)) else 1.0
	)
	add_child(root)

	var mat_data = _resolve_structure_material(structure, structure_type)
	match structure_type:
		"squarecolumn", "flue", "beam":
			_add_box_structure(root, structure_type, width, depth, height, mat_data)
		"circlecolumn":
			_add_cylinder_structure(root, width, depth, height, mat_data)
		"ramp":
			_add_ramp_structure(root, width, depth, height, mat_data)
		"step":
			_add_step_structure(root, structure, width, depth, height, mat_data)
		"falseceiling":
			_add_false_ceiling_structure(root, structure, width, depth, height, mat_data)
		"staircase":
			_add_staircase_structure(root, structure, width, depth, height, mat_data)
		"flooropening":
			_add_floor_opening_frame(root, structure, width, depth, mat_data)
		_:
			_add_box_structure(root, structure_type, width, depth, height, mat_data)

func _resolve_structure_material(structure: Dictionary, structure_type: String) -> Dictionary:
	var mats = {}
	if structure.has("materials") and typeof(structure["materials"]) == TYPE_DICTIONARY:
		mats = structure["materials"]

	var preferred = []
	match structure_type:
		"squarecolumn", "circlecolumn", "flue":
			preferred = ["Column"]
		"beam":
			preferred = ["Beam"]
		"ramp":
			preferred = ["Ramp"]
		"step":
			preferred = ["Step"]
		"falseceiling":
			preferred = ["False Ceiling", "Drop Section"]
		"staircase":
			preferred = ["StaircaseTop", "Step", "LandingTop"]
		"flooropening":
			preferred = ["FloorOpening", "Frame"]
		_:
			preferred = []

	for key in preferred:
		if mats.has(key) and typeof(mats[key]) == TYPE_DICTIONARY:
			return mats[key]

	if mats.size() > 0:
		var first_key = mats.keys()[0]
		if typeof(mats[first_key]) == TYPE_DICTIONARY:
			return mats[first_key]

	return {"color": Color(0.78, 0.78, 0.78)}

func _add_box_structure(parent: Node3D, structure_type: String, width: float, depth: float, height: float, mat_data: Dictionary) -> void:
	var mesh = BoxMesh.new()
	mesh.size = Vector3(max(width, 0.05), max(height, 0.05), max(depth, 0.05))
	var mi = MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = create_material(mat_data)
	mi.position = Vector3(0, max(height, 0.05) * 0.5, 0)
	parent.add_child(mi)

func _add_cylinder_structure(parent: Node3D, width: float, depth: float, height: float, mat_data: Dictionary) -> void:
	var radius = max(width, depth) * 0.5
	var mesh = CylinderMesh.new()
	mesh.top_radius = radius
	mesh.bottom_radius = radius
	mesh.height = max(height, 0.05)
	mesh.radial_segments = 24
	var mi = MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = create_material(mat_data)
	mi.position = Vector3(0, max(height, 0.05) * 0.5, 0)
	parent.add_child(mi)

func _create_ramp_mesh(width: float, depth: float, height: float) -> ArrayMesh:
	var w = max(width, 0.05)
	var d = max(depth, 0.05)
	var h = max(height, 0.05)

	# Triangular prism extruded along X to make a simple ramp wedge.
	var st = SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)

	var v0 = Vector3(-w * 0.5, 0, 0)
	var v1 = Vector3(-w * 0.5, 0, d)
	var v2 = Vector3(-w * 0.5, h, d)
	var v3 = Vector3(w * 0.5, 0, 0)
	var v4 = Vector3(w * 0.5, 0, d)
	var v5 = Vector3(w * 0.5, h, d)

	# Left triangle
	st.add_vertex(v0)
	st.add_vertex(v1)
	st.add_vertex(v2)
	# Right triangle
	st.add_vertex(v3)
	st.add_vertex(v5)
	st.add_vertex(v4)
	# Bottom face
	st.add_vertex(v0)
	st.add_vertex(v3)
	st.add_vertex(v4)
	st.add_vertex(v0)
	st.add_vertex(v4)
	st.add_vertex(v1)
	# Back face
	st.add_vertex(v1)
	st.add_vertex(v4)
	st.add_vertex(v5)
	st.add_vertex(v1)
	st.add_vertex(v5)
	st.add_vertex(v2)
	# Sloped face
	st.add_vertex(v0)
	st.add_vertex(v2)
	st.add_vertex(v5)
	st.add_vertex(v0)
	st.add_vertex(v5)
	st.add_vertex(v3)

	st.generate_normals()
	return st.commit()

func _add_ramp_structure(parent: Node3D, width: float, depth: float, height: float, mat_data: Dictionary) -> void:
	var mesh = MeshInstance3D.new()
	mesh.mesh = _create_ramp_mesh(width, depth, height)
	mesh.material_override = create_material(mat_data)
	parent.add_child(mesh)

func _add_step_structure(parent: Node3D, structure: Dictionary, width: float, depth: float, height: float, mat_data: Dictionary) -> void:
	var props = {}
	if structure.has("properties") and typeof(structure["properties"]) == TYPE_DICTIONARY:
		props = structure["properties"]

	var stair = {}
	if props.has("stair") and typeof(props["stair"]) == TYPE_DICTIONARY:
		stair = props["stair"]

	var flights = []
	if stair.has("flights") and typeof(stair["flights"]) == TYPE_ARRAY:
		flights = stair["flights"]

	var step_count = 1
	var riser = max(height, 0.05)
	var tread = max(depth, 0.05)
	var direction = "forward"

	if flights.size() > 0 and typeof(flights[0]) == TYPE_DICTIONARY:
		var flight = flights[0]
		step_count = max(int(flight.get("step_count", 1)), 1)
		if float(flight.get("riser_height", 0.0)) > 0:
			riser = float(flight.get("riser_height", 0.0)) * 0.01
		else:
			riser = max(height / float(step_count), 0.05)
		if float(flight.get("tread_depth", 0.0)) > 0:
			tread = float(flight.get("tread_depth", 0.0)) * 0.01
		else:
			tread = max(depth / float(step_count), 0.05)
		direction = str(flight.get("direction", "forward")).to_lower()
	else:
		if stair.has("auto_calculate_steps") and bool(stair["auto_calculate_steps"]):
			step_count = max(int(round(height / 0.15)), 1)
			riser = max(height / float(step_count), 0.05)
			tread = max(depth / float(step_count), 0.05)

	var step_width = max(width, 0.05)
	var dir_vec = Vector3(0, 0, 1)
	var step_rot_y = 0.0
	match direction:
		"backward", "back", "reverse":
			dir_vec = Vector3(0, 0, 1)
		"left":
			dir_vec = Vector3(-1, 0, 0)
			step_rot_y = PI / 2
		"right":
			dir_vec = Vector3(1, 0, 0)
			step_rot_y = PI / 2
		_:
			dir_vec = Vector3(0, 0, -1)

	var stair_csg = CSGCombiner3D.new()
	stair_csg.name = "StepCSG_" + str(structure.get("id", "unknown"))
	parent.add_child(stair_csg)

	var step_mat = create_material(mat_data)
	var overlap = 0.002
	var run_length = tread * float(step_count)
	var total_height = riser * float(step_count)

	var support_node = MeshInstance3D.new()
	support_node.mesh = _create_step_support_mesh(step_width, run_length, total_height)
	support_node.material_override = step_mat
	match direction:
		"backward", "back", "reverse":
			support_node.rotation.y = 0.0
		"left":
			support_node.rotation.y = -PI / 2.0
		"right":
			support_node.rotation.y = PI / 2.0
		_:
			support_node.rotation.y = PI
	stair_csg.add_child(support_node)

	for i in range(step_count):
		var step_node = CSGBox3D.new()
		step_node.operation = CSGShape3D.OPERATION_UNION
		step_node.size = Vector3(step_width, riser + overlap, tread + overlap)
		step_node.material = step_mat
		var step_pos = Vector3.ZERO
		step_pos += dir_vec * (tread * (i + 0.5))
		step_pos.y = riser * (i + 0.5)
		step_node.position = step_pos
		step_node.rotation.y = step_rot_y
		stair_csg.add_child(step_node)

func _create_step_support_mesh(width: float, run_length: float, total_height: float) -> ArrayMesh:
	var w = max(width, 0.05)
	var r = max(run_length, 0.05)
	var h = max(total_height, 0.05)

	var st = SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)

	var a = Vector3(-w * 0.5, 0, 0)
	var b = Vector3(-w * 0.5, 0, r)
	var c = Vector3(-w * 0.5, h, r)
	var d = Vector3(w * 0.5, 0, 0)
	var e = Vector3(w * 0.5, 0, r)
	var f = Vector3(w * 0.5, h, r)

	# Left end
	st.add_vertex(a)
	st.add_vertex(b)
	st.add_vertex(c)
	# Right end
	st.add_vertex(d)
	st.add_vertex(f)
	st.add_vertex(e)
	# Bottom
	st.add_vertex(a)
	st.add_vertex(d)
	st.add_vertex(e)
	st.add_vertex(a)
	st.add_vertex(e)
	st.add_vertex(b)
	# Back vertical face
	st.add_vertex(b)
	st.add_vertex(e)
	st.add_vertex(f)
	st.add_vertex(b)
	st.add_vertex(f)
	st.add_vertex(c)
	# Sloped face
	st.add_vertex(a)
	st.add_vertex(c)
	st.add_vertex(f)
	st.add_vertex(a)
	st.add_vertex(f)
	st.add_vertex(d)

	st.generate_normals()
	return st.commit()

func _add_false_ceiling_structure(parent: Node3D, structure: Dictionary, width: float, depth: float, height: float, mat_data: Dictionary) -> void:
	var props = {}
	if structure.has("properties") and typeof(structure["properties"]) == TYPE_DICTIONARY:
		props = structure["properties"]

	var drop_height = get_dimension_value(props.get("drop_height", 0)) * 0.01
	var pattern_type = str(props.get("pattern_type", "")).to_lower()
	var slab_h = max(height, 0.03)
	var root_color = mat_data
	var drop_mat = _resolve_structure_material(structure, "falseceiling")
	if structure.has("materials") and typeof(structure["materials"]) == TYPE_DICTIONARY:
		var mats = structure["materials"]
		if mats.has("Drop Section") and typeof(mats["Drop Section"]) == TYPE_DICTIONARY:
			drop_mat = mats["Drop Section"]

	if pattern_type == "coffered" and drop_height > 0.01 and width > 0.2 and depth > 0.2:
		var border = min(width, depth) * 0.16
		border = clamp(border, 0.08, 0.35)

		# Central panel
		_add_box_part(parent, Vector3(max(width - border * 2.0, 0.1), slab_h, max(depth - border * 2.0, 0.1)), root_color, Vector3(0, -slab_h * 0.5, 0))

		# Lower perimeter bands
		var drop_y = -(drop_height + slab_h * 0.5)
		_add_box_part(parent, Vector3(width, slab_h, border), drop_mat, Vector3(0, drop_y, depth * 0.5 - border * 0.5))
		_add_box_part(parent, Vector3(width, slab_h, border), drop_mat, Vector3(0, drop_y, -depth * 0.5 + border * 0.5))
		_add_box_part(parent, Vector3(border, slab_h, max(depth - border * 2.0, 0.1)), drop_mat, Vector3(width * 0.5 - border * 0.5, drop_y, 0))
		_add_box_part(parent, Vector3(border, slab_h, max(depth - border * 2.0, 0.1)), drop_mat, Vector3(-width * 0.5 + border * 0.5, drop_y, 0))
	else:
		_add_box_part(parent, Vector3(width, slab_h, depth), root_color, Vector3(0, -slab_h * 0.5, 0))

func _add_box_part(parent: Node3D, size: Vector3, mat_data: Dictionary, local_position: Vector3) -> void:
	var mesh = BoxMesh.new()
	mesh.size = Vector3(max(size.x, 0.03), max(size.y, 0.03), max(size.z, 0.03))
	var mi = MeshInstance3D.new()
	mi.mesh = mesh
	mi.material_override = create_material(mat_data)
	mi.position = local_position
	parent.add_child(mi)

func _add_staircase_structure(parent: Node3D, structure: Dictionary, width: float, depth: float, height: float, mat_data: Dictionary) -> void:
	var props = {}
	if structure.has("properties") and typeof(structure["properties"]) == TYPE_DICTIONARY:
		props = structure["properties"]

	var stair = {}
	if props.has("stair") and typeof(props["stair"]) == TYPE_DICTIONARY:
		stair = props["stair"]

	var stair_type = str(stair.get("stair_type", "straight")).to_lower()
	var flights = []
	if stair.has("flights") and typeof(stair["flights"]) == TYPE_ARRAY:
		flights = stair["flights"]

	var step_material = mat_data

	if flights.size() == 0:
		var fallback_width = width
		if fallback_width <= 0.05:
			fallback_width = 1.0
		_add_stair_flight(parent, 12, 0.1524, 0.254, fallback_width, Vector3.ZERO, Vector3(0, 0, 1), step_material)
		return

	var first_flight = flights[0]
	var first_steps = int(first_flight.get("step_count", 12))
	var first_riser = height / max(first_steps, 1)
	if float(first_flight.get("riser_height", 0.0)) > 0:
		first_riser = float(first_flight.get("riser_height", 0.0)) * 0.01
	var first_tread = depth / max(first_steps, 1)
	if float(first_flight.get("tread_depth", 0.0)) > 0:
		first_tread = float(first_flight.get("tread_depth", 0.0)) * 0.01
	var stair_width = float(first_flight.get("width", get_dimension_value(props.get("width", 100)))) * 0.01

	_add_stair_flight(parent, first_steps, first_riser, first_tread, stair_width, Vector3.ZERO, Vector3(0, 0, 1), step_material)

	var current_height = first_steps * first_riser
	var first_run = first_steps * first_tread
	var landing_h = max(first_riser, 0.05)
	var landing_depth = max(stair_width, first_tread * 1.5)

	if stair_type == "straight" or flights.size() == 1:
		return

	if stair_type == "u_shaped":
		_add_box_part(parent, Vector3(stair_width, landing_h, landing_depth), step_material, Vector3(0, current_height - landing_h * 0.5, first_run + landing_depth * 0.5))
		if flights.size() > 1:
			var second_flight = flights[1]
			var second_steps = int(second_flight.get("step_count", first_steps))
			var second_riser = first_riser
			if float(second_flight.get("riser_height", 0.0)) > 0:
				second_riser = float(second_flight.get("riser_height", 0.0)) * 0.01
			var second_tread = first_tread
			if float(second_flight.get("tread_depth", 0.0)) > 0:
				second_tread = float(second_flight.get("tread_depth", 0.0)) * 0.01
			_add_stair_flight(parent, second_steps, second_riser, second_tread, stair_width, Vector3(-stair_width, current_height, first_run + landing_depth), Vector3(0, 0, -1), step_material)
	elif stair_type == "winder":
		_add_box_part(parent, Vector3(stair_width, landing_h, landing_depth), step_material, Vector3(-stair_width * 0.25, current_height - landing_h * 0.5, first_run + landing_depth * 0.5))
		if flights.size() > 1:
			var second_flight_w = flights[1]
			var second_steps_w = int(second_flight_w.get("step_count", first_steps))
			var second_riser_w = first_riser
			if float(second_flight_w.get("riser_height", 0.0)) > 0:
				second_riser_w = float(second_flight_w.get("riser_height", 0.0)) * 0.01
			var second_tread_w = first_tread
			if float(second_flight_w.get("tread_depth", 0.0)) > 0:
				second_tread_w = float(second_flight_w.get("tread_depth", 0.0)) * 0.01
			_add_stair_flight(parent, second_steps_w, second_riser_w, second_tread_w, stair_width, Vector3(-stair_width, current_height, first_run + landing_depth), Vector3(-1, 0, 0), step_material)
	else:
		_add_box_part(parent, Vector3(stair_width, landing_h, landing_depth), step_material, Vector3(0, current_height - landing_h * 0.5, first_run + landing_depth * 0.5))
		if flights.size() > 1:
			var second_flight_l = flights[1]
			var second_steps_l = int(second_flight_l.get("step_count", first_steps))
			var second_riser_l = first_riser
			if float(second_flight_l.get("riser_height", 0.0)) > 0:
				second_riser_l = float(second_flight_l.get("riser_height", 0.0)) * 0.01
			var second_tread_l = first_tread
			if float(second_flight_l.get("tread_depth", 0.0)) > 0:
				second_tread_l = float(second_flight_l.get("tread_depth", 0.0)) * 0.01
			_add_stair_flight(parent, second_steps_l, second_riser_l, second_tread_l, stair_width, Vector3(-stair_width, current_height, first_run + landing_depth), Vector3(-1, 0, 0), step_material)

func _add_stair_flight(parent: Node3D, step_count: int, riser: float, tread: float, stair_width: float, start_pos: Vector3, travel_dir: Vector3, mat_data: Dictionary) -> void:
	var steps = max(step_count, 1)
	var step_w = max(stair_width, 0.05)
	var step_h = max(riser, 0.03)
	var step_d = max(tread, 0.05)
	var dir = travel_dir
	if dir.length() < 0.001:
		dir = Vector3(0, 0, 1)
	dir = dir.normalized()

	for i in range(steps):
		var pos = start_pos
		pos += dir * (step_d * (i + 0.5))
		pos.y += step_h * (i + 0.5)
		_add_box_part(parent, Vector3(step_w, step_h, step_d), mat_data, pos)

func _add_floor_opening_frame(parent: Node3D, structure: Dictionary, width: float, depth: float, mat_data: Dictionary) -> void:
	var frame = max(min(width, depth) * 0.08, 0.04)
	var frame_h = 0.04
	var slab_y = frame_h * 0.5
	_add_box_part(parent, Vector3(width + frame * 2.0, frame_h, frame), mat_data, Vector3(0, slab_y, depth * 0.5 + frame * 0.5))
	_add_box_part(parent, Vector3(width + frame * 2.0, frame_h, frame), mat_data, Vector3(0, slab_y, -depth * 0.5 - frame * 0.5))
	_add_box_part(parent, Vector3(frame, frame_h, depth), mat_data, Vector3(width * 0.5 + frame * 0.5, slab_y, 0))
	_add_box_part(parent, Vector3(frame, frame_h, depth), mat_data, Vector3(-width * 0.5 - frame * 0.5, slab_y, 0))

func _add_floor_opening_cutters(csg: CSGCombiner3D, structures, area_id: String, layer_altitude: float, cut_height: float, scale_factor: float) -> void:
	if typeof(structures) != TYPE_DICTIONARY:
		return

	for structure_id in structures:
		var structure = structures[structure_id]
		if typeof(structure) != TYPE_DICTIONARY:
			continue
		if str(structure.get("structure_type", "")).to_lower() != "flooropening":
			continue
		if not structure.has("parentId") or str(structure["parentId"]) != area_id:
			continue

		var props = {}
		if structure.has("properties") and typeof(structure["properties"]) == TYPE_DICTIONARY:
			props = structure["properties"]

		var width = get_dimension_value(props.get("width", structure.get("width", 100))) * scale_factor
		var depth = get_dimension_value(props.get("depth", structure.get("depth", 100))) * scale_factor
		var altitude = get_dimension_value(props.get("altitude", structure.get("altitude", 0))) * scale_factor
		var opening = CSGBox3D.new()
		opening.operation = CSGBox3D.OPERATION_SUBTRACTION
		opening.size = Vector3(max(width, 0.05), max(cut_height, 0.2), max(depth, 0.05))
		opening.position = Vector3(
			float(structure.get("x", 0)) * scale_factor,
			layer_altitude + altitude - max(cut_height, 0.2) * 0.5,
			float(structure.get("y", 0)) * scale_factor
		)
		csg.add_child(opening)

func load_assets(data):
	if data.has("layers"):
		var layers_dict    = data["layers"]
		var show_all_floors = data.get("showAllFloors", true)
		var selected_layer  = data.get("selectedLayer", "")
		
		if show_all_floors:
			print("load_assets: showAllFloors is true — loading assets for ALL layers.")
		else:
			print("load_assets: showAllFloors is false — loading assets only for layer: ", selected_layer)
		
		for layer_id in layers_dict:
			if not show_all_floors and selected_layer != "" and str(layer_id) != str(selected_layer):
				print("load_assets: Skipping layer: ", layer_id, " (not selected)")
				continue
			
			var layer     = layers_dict[layer_id]
			var layer_alt = 0.0
			
			if show_all_floors:
				if layer.has("altitude"):
					var alt_val = layer["altitude"]
					if typeof(alt_val) == TYPE_DICTIONARY and alt_val.has("length"):
						layer_alt = float(alt_val["length"]) * 0.01
					else:
						layer_alt = float(alt_val) * 0.01
			else:
				print("load_assets: Single-layer mode — placing assets at ground level")

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

	var normalized = str(path).replace("\\", "/")
	if FileAccess.file_exists(normalized):
		return normalized
	if FileAccess.file_exists("res://" + normalized):
		return "res://" + normalized
	if FileAccess.file_exists("./" + normalized):
		return "./" + normalized

	var filename          = normalized.get_file()
	if filename == "": return ""
	var filename_base     = filename.get_basename()
	var parent_folder_hint = normalized.get_base_dir().get_file()

	var project_root           = ProjectSettings.globalize_path("res://")
	var parent_dir             = project_root.get_base_dir()
	var asset_downloads        = parent_dir.path_join("asset_downloads")
	var sibling_asset_downloads = ""
	if parent_dir.ends_with(" - staging"):
		var non_staging_root    = parent_dir.substr(0, parent_dir.length() - " - staging".length())
		sibling_asset_downloads = non_staging_root.path_join("asset_downloads")

	var search_dirs = [
		asset_downloads, sibling_asset_downloads,
		"glb-assets", "res://glb-assets", "./glb-assets",
		"assets", "res://assets", "."
	]

	for dir in search_dirs:
		if dir == "": continue
		var candidate = dir + "/" + filename
		if FileAccess.file_exists(candidate):
			return candidate

	var hints = []
	if parent_folder_hint != "":
		hints.append(parent_folder_hint.to_lower())
	var base_l = filename_base.to_lower()
	if base_l != "":
		hints.append(base_l)
		if base_l.ends_with("_lp"):  hints.append(base_l.substr(0, base_l.length() - 3))
		if base_l.ends_with("_hp"):  hints.append(base_l.substr(0, base_l.length() - 3))
		if base_l.ends_with("_low"): hints.append(base_l.substr(0, base_l.length() - 4))

	for dir in search_dirs:
		if dir == "": continue
		var da = DirAccess.open(dir)
		if da == null: continue
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

func _is_light_fixture_item(item: Dictionary) -> bool:
	var item_type = str(item.get("type", "")).to_lower()
	var item_name = str(item.get("name", "")).to_lower()
	var mounting_type = str(item.get("mounting_type", "")).to_lower()
	var model_hint = ""
	if item.has("asset_urls") and typeof(item["asset_urls"]) == TYPE_DICTIONARY:
		var urls = item["asset_urls"]
		if urls.has("GLB_File_URL") and urls["GLB_File_URL"] != null:
			model_hint = str(urls["GLB_File_URL"]).to_lower()
		elif urls.has("glb_Url") and urls["glb_Url"] != null:
			model_hint = str(urls["glb_Url"]).to_lower()

	var full_desc = item_type + " " + item_name + " " + mounting_type + " " + model_hint
	var light_keywords = [
		"light", "lamp", "pendant", "pendent", "chandelier",
		"lantern", "sconce", "ceilinglamp", "ceiling_light",
		"wallmount", "streetlamp", "lighting fixture"
	]
	for kw in light_keywords:
		if kw in full_desc:
			return true
	return false

func _apply_light_fixture_emission(node: Node3D, light_color: Color, emission_energy: float) -> bool:
	var meshes = _get_all_meshes(node)
	var applied = false
	var emissive_keywords = [
		"bulb", "emiss", "glow", "glass",
		"shade_inner", "lamp_head", "tube", "led",
		"filament", "diffuser"
	]
	for m in meshes:
		if not m.mesh:
			continue
		for i in range(m.mesh.get_surface_count()):
			var mat = m.get_active_material(i)
			if mat == null:
				mat = m.mesh.surface_get_material(i)
			var mat_name = ""
			var surface_name = ""
			if mat != null:
				mat_name = str(mat.resource_name).to_lower()
			surface_name = str(m.mesh.surface_get_name(i)).to_lower()
			var is_emissive_surface = false
			for kw in emissive_keywords:
				if kw in mat_name or kw in surface_name:
					is_emissive_surface = true
					break
			if not is_emissive_surface:
				continue
			if mat and mat is StandardMaterial3D:
				var new_mat = mat.duplicate()
				new_mat.emission_enabled = true
				new_mat.emission = light_color
				new_mat.emission_energy_multiplier = emission_energy
				m.set_surface_override_material(i, new_mat)
				applied = true
	return applied

func _add_light_fixture_effect(node: Node3D, item: Dictionary, local_aabb: AABB, final_scale: Vector3) -> void:
	if item.get("is_exterior_black", false):
		return
	if not _is_light_fixture_item(item):
		return

	var item_type = str(item.get("type", "")).to_lower()
	var item_name = str(item.get("name", "")).to_lower()
	var mounting_type = str(item.get("mounting_type", "")).to_lower()
	var full_desc = item_type + " " + item_name + " " + mounting_type

	var scaled_origin = Vector3(
		local_aabb.position.x * final_scale.x,
		local_aabb.position.y * final_scale.y,
		local_aabb.position.z * final_scale.z
	)
	var scaled_size = Vector3(
		abs(local_aabb.size.x * final_scale.x),
		abs(local_aabb.size.y * final_scale.y),
		abs(local_aabb.size.z * final_scale.z)
	)
	var emitter_local = scaled_origin + scaled_size * 0.5

	if mounting_type == "ceiling_mount":
		emitter_local.y = scaled_origin.y + scaled_size.y * 0.18
	elif "wall" in full_desc:
		emitter_local.y = scaled_origin.y + scaled_size.y * 0.45
		emitter_local.z = scaled_origin.z + scaled_size.z * 0.22
	elif mounting_type == "floor_mount":
		emitter_local.y = scaled_origin.y + scaled_size.y * 0.82
	else:
		emitter_local.y = scaled_origin.y + scaled_size.y * 0.72

	var is_street_light = "street" in full_desc or "exterior" in full_desc
	var is_night = lighting_profile == "night"
	var is_sunset = lighting_profile == "sunset"
	var warm_color = Color(1.0, 0.90, 0.74)
	var street_color = Color(1.0, 0.92, 0.80)
	if is_sunset:
		warm_color = Color(1.0, 0.72, 0.46)
		street_color = Color(1.0, 0.78, 0.52)
	elif is_night:
		warm_color = Color(1.0, 0.84, 0.62)
		street_color = Color(1.0, 0.78, 0.52)
	var light_color = street_color if is_street_light else warm_color

	var light_energy = 1.15
	var light_range = max(max(scaled_size.x, scaled_size.z) * 5.5, 3.6)
	var emission_energy = 4.5
	if is_sunset:
		light_energy = 2.6
		emission_energy = 7.5
	elif is_night:
		light_energy = 4.2
		emission_energy = 9.0

	if mounting_type == "ceiling_mount":
		light_energy = 1.5
		light_range = max(light_range, 5.8)
		emission_energy = 5.8
		if is_sunset:
			light_energy = 3.4
			emission_energy = 9.2
		elif is_night:
			light_energy = 5.5
			emission_energy = 11.5
	elif "wall" in full_desc:
		light_energy = 1.2
		light_range = max(light_range, 4.2)
		emission_energy = 5.0
		if is_sunset:
			light_energy = 2.9
			emission_energy = 8.1
		elif is_night:
			light_energy = 4.6
			emission_energy = 9.8
	elif mounting_type == "floor_mount":
		if is_street_light:
			light_energy = 2.0
			light_range = max(light_range, 8.5)
			emission_energy = 6.0
			if is_sunset:
				light_energy = 4.2
				emission_energy = 9.6
			elif is_night:
				light_energy = 7.2
				emission_energy = 12.0
		else:
			light_energy = 1.35
			light_range = max(light_range, 4.8)
			emission_energy = 5.2
			if is_sunset:
				light_energy = 3.1
				emission_energy = 8.4
			elif is_night:
				light_energy = 5.0
				emission_energy = 10.0

	var emissive_surface_found = _apply_light_fixture_emission(node, light_color, emission_energy)

	var world_pos = node.to_global(emitter_local)
	if mounting_type == "ceiling_mount" or "wall" in full_desc or is_street_light:
		var spot = SpotLight3D.new()
		spot.name = "FixtureLight_" + str(item.get("id", item.get("name", "Light")))
		spot.global_position = world_pos
		spot.light_color = light_color
		spot.light_energy = light_energy
		spot.light_specular = 0.25
		spot.shadow_enabled = false
		spot.spot_range = light_range
		spot.spot_angle = 62.0 if mounting_type == "ceiling_mount" else 48.0
		spot.spot_attenuation = 0.75
		add_child(spot)

		var target_local = emitter_local + Vector3(0, -max(1.0, light_range * 0.45), 0)
		if "wall" in full_desc and not is_street_light:
			target_local = emitter_local + Vector3(0, -0.15, -max(0.8, light_range * 0.25))
		spot.look_at(node.to_global(target_local), Vector3.UP)
	else:
		var omni = OmniLight3D.new()
		omni.name = "FixtureLight_" + str(item.get("id", item.get("name", "Light")))
		omni.global_position = world_pos
		omni.light_color = light_color
		omni.light_energy = light_energy
		omni.light_specular = 0.25
		omni.shadow_enabled = false
		omni.omni_range = light_range
		add_child(omni)

	if not emissive_surface_found:
		print("Light fixture added without material override for asset: ", item.get("name", "Light"))

func _load_layer_items(items, layer_altitude, layer_id := ""):
	for item_id in items:
		var item
		if typeof(items) == TYPE_DICTIONARY:
			item = items[item_id]
		else:
			item = item_id
		if typeof(item) != TYPE_DICTIONARY:
			continue
		var model_path = ""
		
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
		var used_path   = resolved_model_path

		if model_path == "":
			load_reason = "missing_model_path"
			_tracked_assets.append({
				"type": "item_asset", "id": item.get("id", "unknown"),
				"name": item.get("name", "unknown"), "layer_id": layer_id,
				"requested_path": model_path, "path": "",
				"status": load_status, "reason": load_reason,
				"position": item.get("x", 0),
			})
			continue

		if resolved_model_path == "":
			load_reason = "file_not_found"
			_tracked_assets.append({
				"type": "item_asset", "id": item.get("id", "unknown"),
				"name": item.get("name", "unknown"), "layer_id": layer_id,
				"requested_path": model_path, "path": model_path,
				"status": load_status, "reason": load_reason,
				"position": item.get("x", 0),
			})
			continue

		if resolved_model_path != "":
			var glTF      = GLTFDocument.new()
			var glTFState = GLTFState.new()
			var error     = glTF.append_from_file(resolved_model_path, glTFState)
			if error == OK:
				var node = glTF.generate_scene(glTFState)
				if node:
					add_child(node)
					
					var px = float(item.get("x", 0)) * 0.01
					var py = float(item.get("y", 0)) * 0.01
					var pz = 0.0
					
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
					
					var props = {}
					if item.has("properties") and typeof(item["properties"]) == TYPE_DICTIONARY:
						props = item["properties"]
						
					var raw_width  = props.get("width",  100)
					var raw_depth  = props.get("depth",  100)
					var raw_height = props.get("height", 100)
					
					var target_w = get_dimension_value(raw_width)  * 0.01
					var target_d = get_dimension_value(raw_depth)  * 0.01
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
					
					var scale_x = target_w / dims.x
					var scale_y = target_h / dims.y
					var scale_z = target_d / dims.z
					
					var scale_x_swapped = target_w / dims.z
					var scale_z_swapped = target_d / dims.x
					
					var diff_normal  = abs(scale_x - scale_z)
					var diff_swapped = abs(scale_x_swapped - scale_z_swapped)
					
					var final_scale = Vector3.ONE
					
					if diff_swapped < diff_normal and diff_swapped < 0.3:
						final_scale = Vector3(scale_x_swapped, scale_y, scale_z_swapped)
					else:
						var avg_xz_scale = (scale_x + scale_z) / 2.0
						var item_type    = str(item.get("type", "")).to_lower()
						var item_name    = str(item.get("name", "")).to_lower()
						var full_desc    = item_type + " " + item_name
						
						var is_ceiling_light  = ("hanging" in full_desc or "chandelier" in full_desc or "ceiling_light" in full_desc or "ceilinglight" in full_desc or "lamp" in full_desc or "light" in full_desc) and str(item.get("mounting_type", "")).to_lower() == "ceiling_mount"
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
					var bounds_y_min = aabb.position.y * final_scale.y
					node.position.y  = pz - bounds_y_min
					
					if item.get("is_exterior_black", false):
						var meshes    = _get_all_meshes(node)
						var black_mat = StandardMaterial3D.new()
						black_mat.albedo_color  = Color.BLACK
						black_mat.transparency  = BaseMaterial3D.TRANSPARENCY_DISABLED
						for m in meshes:
							if not m.mesh: continue
							for i in range(m.mesh.get_surface_count()):
								m.set_surface_override_material(i, black_mat)
						print("Force-overrode all materials to BLACK for exterior item: ", item_id)

					if item.has("materials") and typeof(item["materials"]) == TYPE_DICTIONARY:
						var item_mats = item["materials"]
						var meshes    = _get_all_meshes(node)
						for m in meshes:
							if not m.mesh: continue
							for i in range(m.mesh.get_surface_count()):
								var mat          = m.get_active_material(i)
								if mat and mat is StandardMaterial3D:
									var mat_name     = str(mat.resource_name)
									var surface_name = str(m.mesh.surface_get_name(i))
									var mat_name_l   = mat_name.to_lower()
									var surface_name_l = surface_name.to_lower()

									var matched_key = ""
									for key in item_mats:
										var key_l = str(key).to_lower()
										if key_l == "": continue
										if (mat_name_l != "" and (key_l in mat_name_l or mat_name_l in key_l)) \
										or (surface_name_l != "" and (key_l in surface_name_l or surface_name_l in key_l)):
											matched_key = key
											break

									if matched_key == "" and item_mats.size() == 1:
										matched_key = item_mats.keys()[0]

									if matched_key != "":
										var mat_opt = item_mats[matched_key]
										if typeof(mat_opt) == TYPE_DICTIONARY:
											var new_mat  = mat.duplicate()
											var modified = false

											var asset_map_url = ""
											if mat_opt.has("mapUrl") and mat_opt["mapUrl"] != null and str(mat_opt["mapUrl"]) != "":
												asset_map_url = str(mat_opt["mapUrl"])
											elif mat_opt.has("texture_urls") and typeof(mat_opt["texture_urls"]) == TYPE_ARRAY and mat_opt["texture_urls"].size() > 0:
												asset_map_url = str(mat_opt["texture_urls"][0])
											var has_texture = asset_map_url != ""

											var has_asset_color = false
											if item.get("is_exterior_black", false):
												new_mat.albedo_color = Color.BLACK
												has_asset_color = true
												modified = true
											elif mat_opt.has("color"):
												var c_str = str(mat_opt.get("color", "ffffff")).strip_edges()
												if c_str.length() >= 3 and not c_str.begins_with("#"):
													c_str = "#" + c_str
												if c_str.is_valid_html_color():
													new_mat.albedo_color = Color(c_str)
													has_asset_color = true
													modified = true

											if has_texture:
												var albedo_tex = load_texture_from_path(asset_map_url)
												if albedo_tex:
													new_mat.albedo_texture = albedo_tex
													if not has_asset_color:
														new_mat.albedo_color = Color(1.0, 1.0, 1.0)
													modified = true
											elif has_asset_color:
												new_mat.albedo_texture = null

											if mat_opt.has("normalUrl") and mat_opt["normalUrl"] != null and str(mat_opt["normalUrl"]) != "":
												var normal_tex = load_texture_from_path(str(mat_opt["normalUrl"]))
												if normal_tex:
													new_mat.normal_enabled  = true
													new_mat.normal_texture  = normal_tex
													modified = true

											if mat_opt.has("roughnessUrl") and mat_opt["roughnessUrl"] != null and str(mat_opt["roughnessUrl"]) != "":
												var rough_tex = load_texture_from_path(str(mat_opt["roughnessUrl"]))
												if rough_tex:
													new_mat.roughness_texture         = rough_tex
													new_mat.roughness_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_GREEN
													modified = true

											if item.get("is_exterior_black", false):
												new_mat.transparency = BaseMaterial3D.TRANSPARENCY_DISABLED

											var scale_u      = new_mat.uv1_scale.x
											var scale_v      = new_mat.uv1_scale.y
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
														scale_u = sf; scale_v = sf
														changed_scale = true
												elif typeof(s) == TYPE_STRING and s.is_valid_float():
													var sf = float(s)
													if sf > 0:
														scale_u = sf; scale_v = sf
														changed_scale = true

											if changed_scale:
												new_mat.uv1_scale = Vector3(scale_u, scale_v, 1.0)
												modified = true

											if modified:
												m.set_surface_override_material(i, new_mat)

					_add_light_fixture_effect(node, item, aabb, final_scale)
					
					print("Loaded asset: ", resolved_model_path, " final scale: ", final_scale)
					load_status = "loaded"
					load_reason = ""
				else:
					load_reason = "generate_scene_null"
			else:
				print("Failed to load GLTF: ", resolved_model_path, " Error: ", error)
				load_reason = "append_from_file_error_" + str(error)
		
		_tracked_assets.append({
			"type": "item_asset", "id": item.get("id", "unknown"),
			"name": item.get("name", "unknown"), "layer_id": layer_id,
			"requested_path": model_path,
			"path": used_path if used_path != "" else model_path,
			"status": load_status, "reason": load_reason,
			"position": item.get("x", 0),
		})

func _expand_polygon_outward(polygon: PackedVector2Array, offset: float) -> PackedVector2Array:
	var n = polygon.size()
	if n < 3 or offset < 0.0001:
		return polygon
	
	var expanded = PackedVector2Array()
	for i in range(n):
		var prev_pt = polygon[(i - 1 + n) % n]
		var curr_pt = polygon[i]
		var next_pt = polygon[(i + 1) % n]
		
		var e1 = curr_pt - prev_pt
		var e2 = next_pt - curr_pt
		
		var n1 = Vector2(e1.y, -e1.x).normalized()
		var n2 = Vector2(e2.y, -e2.x).normalized()
		
		var bisector = (n1 + n2)
		if bisector.length() < 0.0001:
			bisector = n1
		else:
			bisector = bisector.normalized()
		
		var cos_half = n1.dot(bisector)
		if abs(cos_half) < 0.1:
			cos_half = 0.1
		
		var move_dist = offset / cos_half
		expanded.append(curr_pt + bisector * move_dist)
	
	return expanded

func _apply_polygon_uv_world_scale(poly: CSGPolygon3D, polygon: PackedVector2Array) -> void:
	if polygon.size() < 3:
		return

	var min_x = 1e20; var max_x = -1e20
	var min_y = 1e20; var max_y = -1e20
	for p in polygon:
		min_x = min(min_x, p.x); max_x = max(max_x, p.x)
		min_y = min(min_y, p.y); max_y = max(max_y, p.y)

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
	var aabb  := AABB()
	var first  = true
	var meshes = _get_all_meshes(node)
	if meshes.size() == 0:
		return AABB(Vector3.ZERO, Vector3.ONE)
	
	for mesh_inst in meshes:
		var transform = node.global_transform.affine_inverse() * mesh_inst.global_transform
		var mesh_aabb = transform * mesh_inst.get_aabb()
		if first:
			aabb  = mesh_aabb
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
	mat.cull_mode       = BaseMaterial3D.CULL_DISABLED
	mat.roughness       = mat_data.get("roughness", 0.65)
	mat.metallic        = mat_data.get("metallic",  0.0)
	mat.metallic_specular = 0.5
	mat.shading_mode    = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	mat.specular_mode   = BaseMaterial3D.SPECULAR_SCHLICK_GGX

	var base_color       = Color(0.82, 0.82, 0.82)
	var has_explicit_color = false
	if mat_data.has("color"):
		var c_str = str(mat_data["color"]).strip_edges()
		if c_str.length() >= 3 and not c_str.begins_with("#"):
			c_str = "#" + c_str
		if c_str.is_valid_html_color():
			base_color         = Color(c_str)
			has_explicit_color = true
		else:
			print("  Warning: Invalid color string: ", c_str)
		
	mat.albedo_color = base_color
	
	var map_url = ""
	if mat_data.has("mapUrl") and mat_data["mapUrl"] != null and str(mat_data["mapUrl"]) != "":
		map_url = str(mat_data["mapUrl"])
	elif mat_data.has("texture_urls") and typeof(mat_data["texture_urls"]) == TYPE_ARRAY and mat_data["texture_urls"].size() > 0:
		map_url = str(mat_data["texture_urls"][0])
	
	if map_url != "":
		var tex = load_texture_from_path(map_url)
		if tex:
			mat.albedo_texture = tex
			if not has_explicit_color:
				mat.albedo_color = Color(1.0, 1.0, 1.0)
	
	if mat_data.has("normalUrl") and mat_data["normalUrl"] != null and str(mat_data["normalUrl"]) != "":
		var tex = load_texture_from_path(str(mat_data["normalUrl"]))
		if tex:
			mat.normal_enabled = true
			mat.normal_texture = tex
			
	if mat_data.has("roughnessUrl") and mat_data["roughnessUrl"] != null and str(mat_data["roughnessUrl"]) != "":
		var tex = load_texture_from_path(str(mat_data["roughnessUrl"]))
		if tex:
			mat.roughness_texture         = tex
			mat.roughness_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_GREEN
	
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
				scale_u = sf; scale_v = sf
		elif typeof(s) == TYPE_STRING and s.is_valid_float():
			var sf = float(s)
			if sf > 0:
				scale_u = sf; scale_v = sf

	mat.uv1_scale = Vector3(scale_u, scale_v, 1.0)
	return mat

func _resolve_local_texture(url: String) -> String:
	if url == "" or url == "null": return ""
	var normalized = url.replace("\\", "/")
	if FileAccess.file_exists(normalized): return normalized
	var filename = normalized.get_file()
	if filename == "": return ""
	var project_root    = ProjectSettings.globalize_path("res://")
	var parent_dir      = project_root.get_base_dir()
	var asset_downloads = parent_dir.path_join("asset_downloads")
	var search_dirs = [
		asset_downloads, "textures", "res://textures", "./textures",
		"downloaded_textures", "assets/textures", "res://assets", "."
	]
	for dir in search_dirs:
		var candidate = dir + "/" + filename
		if FileAccess.file_exists(candidate):
			return candidate
	return ""

func load_texture_from_path(path):
	if path == "" or path == "null" or path == "None": return null
	var norm_path = str(path).replace("\\", "/")
	if FileAccess.file_exists(norm_path):
		return load_image_texture(norm_path)
	var local = _resolve_local_texture(norm_path)
	if local != "":
		return load_image_texture(local)
	if norm_path.begins_with("http"):
		return null
	print("Texture not found (skipped): ", norm_path)
	return null

func load_image_texture(path):
	var img = Image.load_from_file(path)
	if img:
		return ImageTexture.create_from_image(img)
	return null
