extends Node3D

# video_glb_creation.gd
# Scene builder for VIDEO rendering.
# Mirrors image_glb_creation.gd logic but handles the VIDEO JSON format:
#   - floor_plan_data has lines/vertices/holes/areas/items as ARRAYS (each with an "id" field)
#   - Camera is driven per-frame by video_render.gd via threejs_camera_data
#   - All floor-plan coordinates are in centimetres (multiply by 0.01 for Godot metres)

var _tracked_assets = []
var day_render = true
var lighting_profile = "day"

# ─────────────────────────────────────────────────────────────────
# Entry-point (called by video_render.gd)
# ─────────────────────────────────────────────────────────────────
func build_scene(data):
	lighting_profile = _resolve_lighting_profile(data)
	day_render = lighting_profile != "night"

	# Unwrap floor_plan_data (may be a JSON string or already a dict)
	var geom_data = data
	if data.has("floor_plan_data"):
		var fp = data["floor_plan_data"]
		if typeof(fp) == TYPE_STRING:
			var json = JSON.new()
			var err = json.parse(fp)
			if err == OK:
				geom_data = json.data
				print("[VideoGLB] Parsed floor_plan_data from string.")
			else:
				print("[VideoGLB] ERROR parsing floor_plan_data: ", json.get_error_message())
		else:
			geom_data = fp

	# ── Unwrap layers structure ───────────────────────────────────────────────
	# scene_optimizer_video.py writes geometry inside geom_data["layers"][layer_id].
	# Flatten the selected (or first) layer up to the top level so all downstream
	# functions (build_architecture, load_assets, lighting bounds) can find
	# lines / vertices / holes / areas / items without knowing about layers.
	if geom_data.has("layers") and typeof(geom_data["layers"]) == TYPE_DICTIONARY:
		var layers = geom_data["layers"]
		var layer_id = geom_data.get("selectedLayer", "")
		var layer = null
		if layer_id != "" and layers.has(layer_id):
			layer = layers[layer_id]
		else:
			# Fall back to the first layer
			var first_key = layers.keys()[0] if layers.size() > 0 else ""
			if first_key != "":
				layer_id = first_key
				layer = layers[first_key]
		if layer != null and typeof(layer) == TYPE_DICTIONARY:
			print("[VideoGLB] Unwrapping layer '", layer_id, "' from layers dict.")
			# Merge layer contents into a flat working copy so existing code works
			var flat = {}
			for k in geom_data:
				if k != "layers":
					flat[k] = geom_data[k]
			for k in layer:
				flat[k] = layer[k]
			geom_data = flat
		else:
			print("[VideoGLB] WARNING: layers dict found but no valid layer could be selected.")

	var cam_pose = _extract_camera_pose(data)
	setup_lighting(data, geom_data)
	var show_all_floors = data.get("showAllFloors", null)
	if geom_data.has("showAllFloors"):
		show_all_floors = geom_data["showAllFloors"]
	build_architecture(geom_data, cam_pose.get("position", Vector3.ZERO), show_all_floors)
	load_assets(geom_data)

# ─────────────────────────────────────────────────────────────────
# Lighting  — ported from image_glb_creation.gd (full photorealistic setup)
# ─────────────────────────────────────────────────────────────────
func setup_lighting(data, geom_data = {}):
	var lighting = _get_lighting_profile_settings()

	# Extract camera pose for light anchor and room bounds
	var cam_pose     = _extract_camera_pose(data)
	var cam_pos: Vector3    = cam_pose["position"]
	var cam_target: Vector3 = cam_pose["target"]
	var cam_forward: Vector3 = (cam_target - cam_pos).normalized()
	if cam_forward.length() < 0.001:
		cam_forward = Vector3(0, 0, -1)
	var cam_right = cam_forward.cross(Vector3.UP).normalized()
	if cam_right.length() < 0.001:
		cam_right = Vector3.RIGHT
	var light_anchor = cam_target
	var room_info    = _extract_room_lighting_bounds(geom_data)
	var room_center: Vector3  = room_info.get("center",     light_anchor)
	var room_extent: Vector2  = room_info.get("extent",     Vector2(6.0, 6.0))
	var room_ceiling_y: float = room_info.get("ceiling_y",  room_center.y + 2.8)

	# ── Window / ambient OmniLight ─────────────────────────────────────────────
	var window_light = OmniLight3D.new()
	window_light.position     = Vector3(room_center.x, room_ceiling_y - 1.25, room_center.z)
	window_light.light_energy = lighting["window_energy"]
	window_light.omni_range   = max(room_extent.x, room_extent.y) * 2.1 + 6.0
	window_light.light_color  = lighting["window_color"]
	window_light.light_specular = 0.0
	window_light.shadow_enabled = false
	add_child(window_light)

	# ── Primary Directional Light (Sun / Moon) ─────────────────────────────────
	var dir_light = DirectionalLight3D.new()
	dir_light.name = "Sun"
	# Shadows disabled — avoid the half-surface seam bug in headless/video export
	dir_light.shadow_enabled = false
	dir_light.light_angular_distance  = 0.6
	dir_light.shadow_bias             = 0.01
	dir_light.shadow_normal_bias      = 0.2
	dir_light.directional_shadow_mode = DirectionalLight3D.SHADOW_ORTHOGONAL
	dir_light.directional_shadow_max_distance    = 120.0
	dir_light.directional_shadow_blend_splits    = true
	dir_light.shadow_transmittance_bias          = 0.05
	dir_light.shadow_blur                        = 1.5
	dir_light.light_angular_distance             = 0.5

	if data.has("directional_light"):
		var l = data["directional_light"]
		dir_light.light_energy = l.get("intensity", lighting["dir_energy"])
		if l.has("color") and typeof(l["color"]) == TYPE_DICTIONARY:
			var c = l["color"]
			var default_dir_color = lighting["dir_color"]
			dir_light.light_color = Color(
				float(c.get("r", default_dir_color.r)),
				float(c.get("g", default_dir_color.g)),
				float(c.get("b", default_dir_color.b))
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

	# ── Sky / Environment ──────────────────────────────────────────────────────
	var env = Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = Sky.new()

	var exr_path = ProjectSettings.globalize_path(lighting["sky_exr"])

	var sky_material_set = false
	if FileAccess.file_exists(exr_path):
		var exr_image = Image.new()
		var load_err  = exr_image.load(exr_path)
		if load_err == OK:
			var sky_tex      = ImageTexture.create_from_image(exr_image)
			var panorama_mat = PanoramaSkyMaterial.new()
			panorama_mat.panorama = sky_tex
			env.sky.sky_material  = panorama_mat
			sky_material_set = true
			print("[VideoGLB] Loaded EXR sky: ", exr_path)
		else:
			print("[VideoGLB] Warning: Failed to load EXR: ", exr_path, " Error: ", load_err)
	else:
		print("[VideoGLB] Warning: EXR sky file not found: ", exr_path)

	if not sky_material_set:
		var sky_mat = ProceduralSkyMaterial.new()
		sky_mat.sky_top_color         = lighting["sky_top_color"]
		sky_mat.sky_horizon_color     = lighting["sky_horizon_color"]
		sky_mat.ground_bottom_color   = lighting["ground_bottom_color"]
		sky_mat.ground_horizon_color  = lighting["ground_horizon_color"]
		sky_mat.sun_angle_max         = lighting["sun_angle_max"]
		sky_mat.sun_curve             = lighting["sun_curve"]
		env.sky.sky_material = sky_mat
		print("[VideoGLB] Using fallback procedural sky")

	# ── Ambient Light ──────────────────────────────────────────────────────────
	# Use COLOR ambient for enclosed rooms — avoids sky-light bleeding at corners.
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color  = lighting["ambient_color"]
	env.ambient_light_energy = lighting["ambient_energy"]

	# ── Tone Mapping ───────────────────────────────────────────────────────────
	env.tonemap_mode     = Environment.TONE_MAPPER_FILMIC
	env.tonemap_exposure = lighting["tonemap_exposure"]
	env.tonemap_white    = lighting["tonemap_white"]

	# ── Disabled effects (crash / black in headless export) ────────────────────
	env.sdfgi_enabled = false
	env.ssil_enabled  = false
	env.ssr_enabled   = false

	# ── SSAO ───────────────────────────────────────────────────────────────────
	env.ssao_enabled   = true
	env.ssao_radius    = 1.0
	env.ssao_intensity = 0.45
	env.ssao_power     = 0.9
	env.ssao_detail    = 0.25
	env.ssao_horizon   = 0.03
	env.ssao_sharpness = 0.98

	# ── Glow ───────────────────────────────────────────────────────────────────
	env.glow_enabled       = true
	env.glow_intensity     = lighting["glow_intensity"]
	env.glow_bloom         = lighting["glow_bloom"]
	env.glow_hdr_threshold = lighting["glow_hdr_threshold"]
	env.glow_hdr_scale     = lighting["glow_hdr_scale"]
	env.glow_blend_mode    = Environment.GLOW_BLEND_MODE_SOFTLIGHT

	# ── Colour Grading ─────────────────────────────────────────────────────────
	env.adjustment_enabled    = true
	env.adjustment_brightness = 1.0
	env.adjustment_contrast   = 1.06
	env.adjustment_saturation = 1.10

	# ── ReflectionProbe ────────────────────────────────────────────────────────
	var room_height = max(2.8, (room_ceiling_y - room_center.y) * 2.0)
	var probe = ReflectionProbe.new()
	probe.size        = Vector3(max(room_extent.x + 4.0, 8.0), room_height + 2.0, max(room_extent.y + 4.0, 8.0))
	probe.update_mode = ReflectionProbe.UPDATE_ONCE
	probe.intensity   = 1.0
	probe.max_distance = max(room_extent.x, room_extent.y) * 1.5 + 12.0
	probe.position    = room_center
	add_child(probe)

	var world_env = WorldEnvironment.new()
	world_env.environment = env
	add_child(world_env)

	# ── Scene Fill Lights ──────────────────────────────────────────────────────
	if lighting_profile == "day" or lighting_profile == "sunset":
		var fill = OmniLight3D.new()
		fill.name           = "FillLight"
		fill.position       = Vector3(room_center.x, room_ceiling_y - 0.55, room_center.z)
		fill.light_energy   = lighting["fill_energy"]
		fill.omni_range     = max(room_extent.x, room_extent.y) * 2.5 + 10.0
		fill.light_color    = lighting["fill_color"]
		fill.light_specular = 0.0
		fill.shadow_enabled = false
		add_child(fill)

		# Symmetric front/back fill avoids camera-direction brightness splits
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

	print("[VideoGLB] Photorealistic lighting setup complete. profile=", lighting_profile)

# ─────────────────────────────────────────────────────────────────
# Parse vec3 helper
# ─────────────────────────────────────────────────────────────────
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
				"window_energy": 0.07,
				"window_color": Color(0.72, 0.80, 1.0),
				"dir_energy": 0.14,
				"dir_color": Color(0.55, 0.62, 0.90),
				"sky_top_color": Color(0.01, 0.02, 0.05),
				"sky_horizon_color": Color(0.04, 0.06, 0.10),
				"ground_bottom_color": Color(0.005, 0.005, 0.01),
				"ground_horizon_color": Color(0.03, 0.05, 0.08),
				"sun_angle_max": 10.0,
				"sun_curve": 0.05,
				"ambient_color": Color(0.20, 0.24, 0.34),
				"ambient_energy": 0.10,
				"tonemap_exposure": 0.68,
				"tonemap_white": 4.8,
				"glow_intensity": 0.26,
				"glow_bloom": 0.10,
				"glow_hdr_threshold": 1.15,
				"glow_hdr_scale": 2.1,
				"fill_energy": 0.10,
				"fill_color": Color(0.5, 0.55, 0.8),
				"front_back_fill_energy": 0.04,
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
				"window_energy": 0.14,
				"window_color": Color(1.0, 0.95, 0.85),
				"dir_energy": 2.8,
				"dir_color": Color(1.0, 0.96, 0.88),
				"sky_top_color": Color(0.25, 0.40, 0.78),
				"sky_horizon_color": Color(0.75, 0.80, 0.90),
				"ground_bottom_color": Color(0.08, 0.06, 0.04),
				"ground_horizon_color": Color(0.30, 0.28, 0.22),
				"sun_angle_max": 30.0,
				"sun_curve": 0.15,
				"ambient_color": Color(0.76, 0.77, 0.78),
				"ambient_energy": 0.42,
				"tonemap_exposure": 0.92,
				"tonemap_white": 5.0,
				"glow_intensity": 0.22,
				"glow_bloom": 0.08,
				"glow_hdr_threshold": 1.35,
				"glow_hdr_scale": 1.9,
				"fill_energy": 0.18,
				"fill_color": Color(1.0, 0.97, 0.90),
				"front_back_fill_energy": 0.055,
				"front_back_fill_color": Color(0.99, 0.97, 0.93)
			}

# ─────────────────────────────────────────────────────────────────
# Camera pose extractor
# ─────────────────────────────────────────────────────────────────
func _extract_camera_pose(data: Dictionary) -> Dictionary:
	var cam_pos    = Vector3(0, 1.6, 6)
	var cam_target = Vector3.ZERO
	var found      = false

	# Try video_animation first keyframe (threejs_camera_data coords are in cm)
	if data.has("video_animation") and data["video_animation"] != null:
		var anim      = data["video_animation"]
		var keyframes = anim.get("keyframes", [])
		if keyframes.size() > 0:
			var kf0 = keyframes[0]
			var tjs = kf0.get("threejs_camera_data", null)
			if tjs != null and typeof(tjs) == TYPE_DICTIONARY:
				if tjs.has("position") and typeof(tjs["position"]) == TYPE_DICTIONARY:
					var p = tjs["position"]
					cam_pos = Vector3(float(p.get("x", 0)) * 0.01, float(p.get("y", 0)) * 0.01, float(p.get("z", 0)) * 0.01)
					found = true
				if tjs.has("lookAt") and typeof(tjs["lookAt"]) == TYPE_DICTIONARY:
					var la = tjs["lookAt"]
					cam_target = Vector3(float(la.get("x", 0)) * 0.01, float(la.get("y", 0)) * 0.01, float(la.get("z", 0)) * 0.01)

	if not found and data.has("threejs_camera") and typeof(data["threejs_camera"]) == TYPE_DICTIONARY:
		var tc = data["threejs_camera"]
		if tc.has("position") and typeof(tc["position"]) == TYPE_DICTIONARY:
			cam_pos = parse_vec3(tc["position"])
			found   = true
		if tc.has("target") and typeof(tc["target"]) == TYPE_DICTIONARY:
			cam_target = parse_vec3(tc["target"])

	if not found:
		cam_pos = Vector3(0, 1.6, 6)
	if cam_target.distance_to(cam_pos) < 0.001:
		cam_target = cam_pos + Vector3(0, 0, -1)

	return {"position": cam_pos, "target": cam_target}

# ─────────────────────────────────────────────────────────────────
# Room bounds extractor — for lighting (ported from image_glb_creation.gd)
# ─────────────────────────────────────────────────────────────────
func _extract_room_lighting_bounds(geom_data) -> Dictionary:
	var out = {
		"center":    Vector3.ZERO,
		"extent":    Vector2(6.0, 6.0),
		"ceiling_y": 2.8
	}
	if typeof(geom_data) != TYPE_DICTIONARY:
		return out

	# Normalise array-format data to dict format for bounds extraction
	var layer_data = _arrays_to_dicts(geom_data)

	if not layer_data.has("areas") or not layer_data.has("vertices"):
		return out

	var areas    = layer_data["areas"]
	var vertices = layer_data["vertices"]
	if typeof(areas) != TYPE_DICTIONARY or typeof(vertices) != TYPE_DICTIONARY or areas.size() == 0:
		return out

	var min_x = 1e20; var max_x = -1e20
	var min_z = 1e20; var max_z = -1e20
	var got_any = false

	for aid in areas:
		var area = areas[aid]
		if typeof(area) != TYPE_DICTIONARY or not area.has("vertices"):
			continue
		for vid in area["vertices"]:
			var key = str(vid)
			if not vertices.has(key):
				continue
			var v   = vertices[key]
			var x   = float(v.get("x", 0.0)) * 0.01
			var z   = float(v.get("y", 0.0)) * 0.01
			min_x = min(min_x, x); max_x = max(max_x, x)
			min_z = min(min_z, z); max_z = max(max_z, z)
			got_any = true

	if not got_any:
		return out

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

	out["center"]    = Vector3((min_x + max_x) * 0.5, ceil_h * 0.5, (min_z + max_z) * 0.5)
	out["extent"]    = Vector2(max(2.0, max_x - min_x), max(2.0, max_z - min_z))
	out["ceiling_y"] = ceil_h
	return out

# ─────────────────────────────────────────────────────────────────
# Ceiling visibility helpers
# ─────────────────────────────────────────────────────────────────
func _camera_requires_ceiling(cam_pos: Vector3, layer_altitude: float, ceil_height: float, polygon: PackedVector2Array) -> bool:
	if polygon.size() < 3:
		return false
	var ceiling_y = layer_altitude + ceil_height
	if cam_pos.y > ceiling_y - 0.01:
		return false
	var p2 = Vector2(cam_pos.x, cam_pos.z)
	return _point_in_polygon(p2, polygon)

func _point_in_polygon(p: Vector2, poly: PackedVector2Array) -> bool:
	var inside = false
	var j = poly.size() - 1
	for i in range(poly.size()):
		var pi = poly[i]
		var pj = poly[j]
		var intersect = ((pi.y > p.y) != (pj.y > p.y))
		if intersect:
			var denom = pj.y - pi.y
			if abs(denom) < 0.000001:
				denom = 0.000001
			var x_at = (pj.x - pi.x) * (p.y - pi.y) / denom + pi.x
			if p.x < x_at:
				inside = !inside
		j = i
	return inside

# ─────────────────────────────────────────────────────────────────
# Architecture — converts arrays to lookup dicts then calls shared builder
# ─────────────────────────────────────────────────────────────────
func build_architecture(data, cam_pos = null, show_all_floors_override = null):
	print("[VideoGLB] Building Architecture...")

	var layer_data = _arrays_to_dicts(data)

	if not layer_data.has("lines") or not layer_data.has("vertices"):
		print("[VideoGLB] WARNING: No lines/vertices found in floor_plan_data.")
		return

	print("[VideoGLB] Lines: ", layer_data["lines"].size(),
		"  Vertices: ", layer_data["vertices"].size(),
		"  Holes: ",    layer_data.get("holes", {}).size(),
		"  Areas: ",    layer_data.get("areas", {}).size())

	var show_all_floors = data.get("showAllFloors", true)
	if show_all_floors_override != null:
		show_all_floors = bool(show_all_floors_override)

	# Determine render_mode (video is almost always INTERIOR but respect override)
	var render_mode = data.get("render_mode", "INTERIOR")

	if show_all_floors:
		print("[VideoGLB] showAllFloors=true — building ceilings.")
	else:
		print("[VideoGLB] showAllFloors=false — ceilings hidden unless camera is inside.")

	_build_layer_geometry(layer_data, 0.0, show_all_floors, cam_pos, render_mode)

# Convert list-format arrays to id-keyed dicts (matching image_glb_creation.gd layout)
func _arrays_to_dicts(data: Dictionary) -> Dictionary:
	var out = {}

	# Vertices
	var raw_v = data.get("vertices", null)
	if typeof(raw_v) == TYPE_ARRAY:
		var d = {}
		for v in raw_v:
			if typeof(v) == TYPE_DICTIONARY and v.has("id"):
				d[str(v["id"])] = v
		out["vertices"] = d
	elif typeof(raw_v) == TYPE_DICTIONARY:
		out["vertices"] = raw_v

	# Lines
	var raw_l = data.get("lines", null)
	if typeof(raw_l) == TYPE_ARRAY:
		var d = {}
		for l in raw_l:
			if typeof(l) == TYPE_DICTIONARY and l.has("id"):
				d[str(l["id"])] = l
		out["lines"] = d
	elif typeof(raw_l) == TYPE_DICTIONARY:
		out["lines"] = raw_l

	# Holes
	var raw_h = data.get("holes", null)
	if typeof(raw_h) == TYPE_ARRAY:
		var d = {}
		for h in raw_h:
			if typeof(h) == TYPE_DICTIONARY and h.has("id"):
				d[str(h["id"])] = h
		out["holes"] = d
	elif typeof(raw_h) == TYPE_DICTIONARY:
		out["holes"] = raw_h

	# Areas
	var raw_a = data.get("areas", null)
	if typeof(raw_a) == TYPE_ARRAY:
		var d = {}
		for a in raw_a:
			if typeof(a) == TYPE_DICTIONARY and a.has("id"):
				d[str(a["id"])] = a
		out["areas"] = d
	elif typeof(raw_a) == TYPE_DICTIONARY:
		out["areas"] = raw_a

	# Items
	var raw_i = data.get("items", null)
	if typeof(raw_i) == TYPE_ARRAY:
		out["items"] = raw_i
	elif typeof(raw_i) == TYPE_DICTIONARY:
		out["items"] = raw_i

	return out

# ─────────────────────────────────────────────────────────────────
# Wall facing direction — mirrors frontend getWallFacingDirection()
# Returns "inner-left", "inner-right", or "" (fallback to centroid)
# ─────────────────────────────────────────────────────────────────
func _get_wall_facing_direction(line: Dictionary, vertices: Dictionary, areas: Dictionary) -> String:
	var v1_id = str(line["vertices"][0])
	var v2_id = str(line["vertices"][1])
	if not vertices.has(v1_id) or not vertices.has(v2_id):
		return ""

	var vA = vertices[v1_id]
	var vB = vertices[v2_id]
	var ax = float(vA["x"]); var ay = float(vA["y"])
	var bx = float(vB["x"]); var by = float(vB["y"])

	var dir         = Vector2(bx - ax, by - ay).normalized()
	var left_normal  = Vector2(-dir.y,  dir.x)
	var right_normal = Vector2( dir.y, -dir.x)
	var mid          = Vector2((ax + bx) * 0.5, (ay + by) * 0.5)
	var left_sample  = mid + left_normal  * 5.0
	var right_sample = mid + right_normal * 5.0

	var best_result  = ""
	var best_area_id = ""

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
			continue

		# Deterministic: smallest area_id wins (handles shared walls between rooms)
		if best_result == "" or area_id < best_area_id:
			best_result  = result
			best_area_id = area_id

	return best_result

# Ray-casting point-in-polygon (matches frontend isPointInPolygon)
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

# ─────────────────────────────────────────────────────────────────
# Wall face material resolver — mirrors image_glb_creation.gd
# Priority: asset_urls block → inner/outer_properties → cross-fallback → null
# ─────────────────────────────────────────────────────────────────
func _resolve_wall_face_material(line: Dictionary, face: String) -> Variant:
	# 1. asset_urls block
	if line.has("asset_urls") and typeof(line["asset_urls"]) == TYPE_DICTIONARY:
		var au = line["asset_urls"]
		if au.has(face) and typeof(au[face]) == TYPE_DICTIONARY:
			var block    = au[face]
			var mat_data = {}

			if block.has("mapUrl") and block["mapUrl"] != null and str(block["mapUrl"]) != "":
				mat_data["mapUrl"] = block["mapUrl"]
			elif block.has("texture_urls") and typeof(block["texture_urls"]) == TYPE_ARRAY and block["texture_urls"].size() > 0:
				mat_data["mapUrl"] = block["texture_urls"][0]

			if block.has("normalUrl") and block["normalUrl"] != null and str(block["normalUrl"]) != "":
				mat_data["normalUrl"] = block["normalUrl"]

			if block.has("roughnessUrl") and block["roughnessUrl"] != null and str(block["roughnessUrl"]) != "":
				mat_data["roughnessUrl"] = block["roughnessUrl"]

			var fallback_color = block.get("fallback_color", "")
			if fallback_color == null or str(fallback_color).strip_edges() == "":
				fallback_color = ""
			if fallback_color != "":
				mat_data["color"] = str(fallback_color)

			if block.has("texture_scale_x") or block.has("texture_scale_y"):
				var sx = float(block.get("texture_scale_x", 1.0))
				var sy = float(block.get("texture_scale_y", 1.0))
				mat_data["scale"] = [sx if sx > 0 else 1.0, sy if sy > 0 else 1.0]
			elif block.has("repeat") and typeof(block["repeat"]) == TYPE_ARRAY:
				mat_data["repeat"] = block["repeat"]

			if mat_data.size() > 0:
				return mat_data

	# 2. inner_properties / outer_properties
	var prop_key = face + "_properties"
	if line.has(prop_key) and typeof(line[prop_key]) == TYPE_DICTIONARY:
		var props = line[prop_key]
		if props.has("material") and typeof(props["material"]) == TYPE_DICTIONARY:
			return props["material"]

	# 3. Cross-fallback
	var other_face    = "outer" if face == "inner" else "inner"
	var other_prop_key = other_face + "_properties"
	if line.has(other_prop_key) and typeof(line[other_prop_key]) == TYPE_DICTIONARY:
		var props = line[other_prop_key]
		if props.has("material") and typeof(props["material"]) == TYPE_DICTIONARY:
			return props["material"]

	return null

# ─────────────────────────────────────────────────────────────────
# Core geometry builder — full parity with image_glb_creation.gd
# ─────────────────────────────────────────────────────────────────
func _build_layer_geometry(layer_data, layer_altitude, show_all_floors = true, cam_pos = null, render_mode: String = "INTERIOR"):
	var csg = CSGCombiner3D.new()
	csg.use_collision = true
	add_child(csg)

	var lines        = layer_data["lines"]
	var vertices     = layer_data["vertices"]
	var scale_factor = 0.01  # cm → metres

	var all_holes = {}
	if layer_data.has("holes"):
		all_holes = layer_data["holes"]

	var areas = {}
	if layer_data.has("areas"):
		areas = layer_data["areas"]

	# ── Room centroid (fallback for walls with no named area) ──────────────────
	var room_centroid  = Vector3.ZERO
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

	print("[VideoGLB] Layer has ", lines.size(), " lines. Room centroid: ", room_centroid)

	# ── 1. Build Walls ─────────────────────────────────────────────────────────
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
		var height = 240.0 * scale_factor  # default

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

		# Extend wall at both ends to fill corner gaps
		var corner_length = length + wall_thickness

		# ── Determine inner/outer facing (mirrors frontend exactly) ───────────
		var wall_facing = _get_wall_facing_direction(line, vertices, areas)

		var inner_sign = 1.0  # +1 → +Z is inner, -1 → -Z is inner
		if wall_facing == "inner-left":
			inner_sign = 1.0
		elif wall_facing == "inner-right":
			inner_sign = -1.0
		else:
			# No area polygon found — fall back to centroid dot-product
			var wall_basis        = Basis(Vector3.UP, wall_angle)
			var wall_normal_world = wall_basis * Vector3(0, 0, 1)
			if has_centroid:
				var to_centroid = room_centroid - center
				to_centroid.y = 0.0
				if to_centroid.dot(wall_normal_world) < 0:
					inner_sign = -1.0
			wall_facing = "inner-left" if inner_sign > 0 else "inner-right"

		print("[VideoGLB] Wall ", line_id, " facing: ", wall_facing, " inner_sign: ", inner_sign)

		# ── Resolve inner/outer material data ─────────────────────────────────
		var inner_mat_data = _resolve_wall_face_material(line, "inner")
		var outer_mat_data = _resolve_wall_face_material(line, "outer")

		# ── Build structural CSGBox3D wall ─────────────────────────────────────
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
			# INTERIOR: camera is inside — structural wall uses inner material.
			# Prevents outer brick/stone textures from bleeding through.
			inner_mat_for_wall.cull_mode = BaseMaterial3D.CULL_DISABLED
			wall.material = inner_mat_for_wall
		else:
			# EXTERIOR: camera is outside — structural wall uses outer material.
			outer_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
			wall.material = outer_mat

		csg.add_child(wall)

		# ── Dual overlay panels (INTERIOR mode only) ───────────────────────────
		# Two 3mm panels, one on each face, with CULL_BACK so each is only visible
		# from its own side. Matches the frontend's per-face material assignment.
		var inner_csg: CSGCombiner3D = null
		var outer_csg: CSGCombiner3D = null

		if render_mode == "INTERIOR":
			var face_t     = 0.003   # 3 mm — clears Z-fighting at close range
			var wall_basis = Basis(Vector3.UP, wall_angle)

			# Inner overlay panel (room-facing side)
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

			# Outer overlay panel (exterior-facing side)
			if outer_mat_data != null:
				var outer_offset = wall_thickness / 2.0 + face_t / 2.0
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

		# ── Holes (Doors / Windows) ────────────────────────────────────────────
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

				var hole_pos_xz  = p1.lerp(p2, offset_ratio)
				var h_center_pos = hole_pos_xz
				h_center_pos.y   = layer_altitude + h_alt + h_height / 2.0

				if h_width < 0.01 or h_height < 0.01:
					continue

				# 1. Cut through structural wall
				var hole_csg = CSGBox3D.new()
				hole_csg.operation = CSGBox3D.OPERATION_SUBTRACTION
				hole_csg.size      = Vector3(h_width, h_height, wall_thickness + 0.2)
				hole_csg.position  = h_center_pos
				hole_csg.rotation.y = wall_angle
				csg.add_child(hole_csg)

				# 2a. Cut through inner overlay panel
				if inner_csg != null:
					var inner_hole = CSGBox3D.new()
					inner_hole.operation = CSGBox3D.OPERATION_SUBTRACTION
					inner_hole.size      = Vector3(h_width, h_height, 0.5)
					var delta_w   = h_center_pos - inner_csg.position
					var local_pos = Basis(Vector3.UP, wall_angle).transposed() * delta_w
					inner_hole.position = local_pos
					inner_csg.add_child(inner_hole)

				# 2b. Cut through outer overlay panel
				if outer_csg != null:
					var outer_hole = CSGBox3D.new()
					outer_hole.operation = CSGBox3D.OPERATION_SUBTRACTION
					outer_hole.size      = Vector3(h_width, h_height, 0.5)
					var delta_o     = h_center_pos - outer_csg.position
					var local_pos_o = Basis(Vector3.UP, wall_angle).transposed() * delta_o
					outer_hole.position = local_pos_o
					outer_csg.add_child(outer_hole)

				# 3. Exterior black blocker
				if hole.get("is_exterior_black", false):
					var blocker = CSGBox3D.new()
					blocker.size       = Vector3(h_width + 0.02, h_height + 0.02, 0.01)
					blocker.position   = h_center_pos
					blocker.rotation.y = wall_angle
					var black_mat = StandardMaterial3D.new()
					black_mat.albedo_color = Color.BLACK
					blocker.material = black_mat
					add_child(blocker)

				# 4. Door / window GLB asset
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

									print("[VideoGLB] Loaded hole asset: ", model_path, " scale: ", asset_node.scale)
							else:
								print("[VideoGLB] Failed to load hole GLTF: ", model_path, " Error: ", error)

						_tracked_assets.append({
							"type": "hole_asset",
							"id":   hole.get("id",   "unknown"),
							"name": hole.get("name", "unknown"),
							"path": model_path
						})

	# ── 2. Build Floors and Ceilings from Areas ────────────────────────────────
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

	if layer_data.has("areas"):
		print("[VideoGLB] Building Floors/Ceilings for ", layer_data["areas"].size(), " areas.")
		for area_id in layer_data["areas"]:
			var area = layer_data["areas"][area_id]
			if not area.has("vertices") or area["vertices"].size() < 3:
				continue

			var polygon = PackedVector2Array()
			for v_id in area["vertices"]:
				var vs = str(v_id)
				if vertices.has(vs):
					var v = vertices[vs]
					polygon.append(Vector2(float(v["x"]) * scale_factor, float(v["y"]) * scale_factor))

			if polygon.size() < 3:
				continue

			# Enforce CCW winding (required by CSGPolygon3D)
			var signed_area = 0.0
			for pi in range(polygon.size()):
				var pa = polygon[pi]
				var pb = polygon[(pi + 1) % polygon.size()]
				signed_area += (pa.x * pb.y - pb.x * pa.y)
			signed_area *= 0.5
			if signed_area < 0:
				polygon.reverse()

			var uv_polygon = polygon.duplicate()
			# Expand polygon outward to fill wall-thickness gaps at edges
			polygon = _expand_polygon_outward(polygon, max_wall_thickness / 2.0)

			# ── Floor ──────────────────────────────────────────────────────────
			var floor_depth = 0.1
			if area.has("floor_properties") and typeof(area["floor_properties"]) == TYPE_DICTIONARY:
				var fp2 = area["floor_properties"]
				if fp2.has("thickness"):
					var t = float(fp2["thickness"]) * scale_factor
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

			if area.has("floor_properties") and typeof(area["floor_properties"]) == TYPE_DICTIONARY and area["floor_properties"].has("material"):
				floor_poly.material = create_material(area["floor_properties"]["material"])
			else:
				var floor_mat = StandardMaterial3D.new()
				floor_mat.albedo_color = Color(0.8, 0.8, 0.8)
				floor_poly.material    = floor_mat

			csg.add_child(floor_poly)

			# ── Ceiling ────────────────────────────────────────────────────────
			var ceil_height = max_wall_height

			if area.has("ceiling_properties") and typeof(area["ceiling_properties"]) == TYPE_DICTIONARY:
				var cp = area["ceiling_properties"]
				if cp.has("height"):
					var ch_val = 0.0
					if typeof(cp["height"]) == TYPE_DICTIONARY and cp["height"].has("length"):
						ch_val = float(cp["height"]["length"]) * scale_factor
					elif typeof(cp["height"]) == TYPE_INT or typeof(cp["height"]) == TYPE_FLOAT:
						ch_val = float(cp["height"]) * scale_factor
					elif typeof(cp["height"]) == TYPE_STRING and cp["height"].is_valid_float():
						ch_val = float(cp["height"]) * scale_factor
					if ch_val > 0.1:
						ceil_height = min(ch_val, max_wall_height)

			if ceil_height < 0.1 and area.has("properties") and typeof(area["properties"]) == TYPE_DICTIONARY:
				var h_prop = area["properties"].get("height", null)
				if h_prop != null:
					var h_val = 0.0
					if typeof(h_prop) == TYPE_DICTIONARY and h_prop.has("length"):
						h_val = float(h_prop["length"]) * scale_factor
					elif typeof(h_prop) == TYPE_INT or typeof(h_prop) == TYPE_FLOAT:
						h_val = float(h_prop) * scale_factor
					if h_val > 0.1:
						ceil_height = min(h_val, max_wall_height)

			if ceil_height < 0.1:
				ceil_height = max_wall_height

			var ceil_depth = 0.1
			if area.has("ceiling_properties") and typeof(area["ceiling_properties"]) == TYPE_DICTIONARY:
				var cp = area["ceiling_properties"]
				if cp.has("thickness"):
					var t = float(cp["thickness"]) * scale_factor
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

			if area.has("ceiling_properties") and typeof(area["ceiling_properties"]) == TYPE_DICTIONARY and area["ceiling_properties"].has("material"):
				ceil_poly.material = create_material(area["ceiling_properties"]["material"])
			else:
				var ceil_mat = StandardMaterial3D.new()
				ceil_mat.albedo_color = Color(0.95, 0.95, 0.95)
				ceil_poly.material    = ceil_mat

			# Ceiling visibility gate
			var is_visible = show_all_floors
			if area.has("ceiling_properties") and typeof(area["ceiling_properties"]) == TYPE_DICTIONARY and area["ceiling_properties"].has("isvisible"):
				is_visible = bool(area["ceiling_properties"]["isvisible"])
			elif not show_all_floors and cam_pos != null:
				if _camera_requires_ceiling(cam_pos, layer_altitude, ceil_height, polygon):
					is_visible = true

			if not is_visible:
				continue

			csg.add_child(ceil_poly)
			print("[VideoGLB] Floor/Ceiling area: ", area_id, " ceil_h=", ceil_height, " floor_depth=", floor_depth)

# ─────────────────────────────────────────────────────────────────
# Polygon helpers (ported from image_glb_creation.gd)
# ─────────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────────
# Asset loading — mirrors image_glb_creation.gd load_assets / _load_layer_items
# ─────────────────────────────────────────────────────────────────
func load_assets(data):
	if data.has("items"):
		_load_layer_items(data["items"], 0.0)
	elif data.has("assets"):
		_load_layer_items(data["assets"], 0.0)

func _resolve_local_model_path(path: String) -> String:
	if path == "" or path == "null" or path == "None":
		return ""

	var normalized = str(path).replace("\\", "/")
	if FileAccess.file_exists(normalized):             return normalized
	if FileAccess.file_exists("res://" + normalized):  return "res://" + normalized
	if FileAccess.file_exists("./" + normalized):      return "./" + normalized

	var filename           = normalized.get_file()
	if filename == "": return ""
	var filename_base      = filename.get_basename()
	var parent_folder_hint = normalized.get_base_dir().get_file()

	var project_root            = ProjectSettings.globalize_path("res://")
	var parent_dir              = project_root.get_base_dir()
	var asset_downloads         = parent_dir.path_join("asset_downloads")
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
						return dir + "/" + entry
			entry = da.get_next()

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
		print("[VideoGLB] Light fixture added without material override for asset: ", item.get("name", "Light"))

func _load_layer_items(items, layer_altitude, layer_id = "root"):
	for item_id in items:
		var item
		if typeof(items) == TYPE_DICTIONARY:
			item = items[item_id]
		else:
			item = item_id  # Array iteration
		if typeof(item) != TYPE_DICTIONARY:
			continue

		var model_path = ""
		var used_path  = ""

		if item.has("local_glb_path") and str(item["local_glb_path"]) != "":
			model_path = str(item["local_glb_path"])
		elif item.has("local_path") and str(item["local_path"]) != "":
			model_path = str(item["local_path"])
		elif item.has("asset_urls"):
			var urls = item["asset_urls"]
			if urls.has("GLB_File_URL") and urls["GLB_File_URL"] != null and str(urls["GLB_File_URL"]) != "":
				model_path = str(urls["GLB_File_URL"])
			elif urls.has("glb_Url") and urls["glb_Url"] != null and str(urls["glb_Url"]) != "":
				model_path = str(urls["glb_Url"])

		var load_status = "not_attempted"
		var load_reason = ""

		if model_path != "":
			var resolved_model_path = _resolve_local_model_path(model_path)
			if resolved_model_path != "":
				used_path = resolved_model_path
			else:
				used_path = model_path

			if FileAccess.file_exists(used_path):
				var glTF      = GLTFDocument.new()
				var glTFState = GLTFState.new()
				var error     = glTF.append_from_file(used_path, glTFState)
				if error == OK:
					var node = glTF.generate_scene(glTFState)
					if node:
						add_child(node)
						load_status = "loaded"

						# Position
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

						# Rotation
						var rot = 0.0
						if item.has("rotation"):
							rot = float(item["rotation"])
						elif item.has("properties") and item["properties"].has("rotation"):
							rot = float(item["properties"]["rotation"])
						node.rotation.y = -deg_to_rad(rot)

						# Smart scale (same logic as image_glb_creation.gd)
						var props = {}
						if item.has("properties") and typeof(item["properties"]) == TYPE_DICTIONARY:
							props = item["properties"]

						var raw_width   = props.get("width",  100)
						var raw_depth   = props.get("depth",  100)
						var raw_height_p = props.get("height", 100)

						var target_w = get_dimension_value(raw_width)    * 0.01
						var target_d = get_dimension_value(raw_depth)    * 0.01
						var target_h = get_dimension_value(raw_height_p) * 0.01

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

						# Force-black for exterior items
						if item.get("is_exterior_black", false):
							var meshes    = _get_all_meshes(node)
							var black_mat = StandardMaterial3D.new()
							black_mat.albedo_color = Color.BLACK
							black_mat.transparency = BaseMaterial3D.TRANSPARENCY_DISABLED
							for m in meshes:
								if not m.mesh: continue
								for i in range(m.mesh.get_surface_count()):
									m.set_surface_override_material(i, black_mat)

						# Material overrides per surface
						if item.has("materials") and typeof(item["materials"]) == TYPE_DICTIONARY:
							var item_mats = item["materials"]
							var meshes    = _get_all_meshes(node)
							for m in meshes:
								if not m.mesh: continue
								for i in range(m.mesh.get_surface_count()):
									var mat          = m.get_active_material(i)
									if mat and mat is StandardMaterial3D:
										var mat_name       = str(mat.resource_name)
										var surface_name   = str(m.mesh.surface_get_name(i))
										var mat_name_l     = mat_name.to_lower()
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
														new_mat.normal_enabled = true
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

												var scale_u       = new_mat.uv1_scale.x
												var scale_v       = new_mat.uv1_scale.y
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

						print("[VideoGLB] Loaded asset: ", used_path, " final scale: ", final_scale)
					else:
						load_reason = "generate_scene_null"
				else:
					print("[VideoGLB] Failed to load GLTF: ", used_path, " Error: ", error)
					load_reason = "append_from_file_error_" + str(error)
			else:
				load_reason = "file_not_found"

		_tracked_assets.append({
			"type":     "item_asset",
			"id":       item.get("id",   "unknown"),
			"name":     item.get("name", "unknown"),
			"layer_id": layer_id,
			"path":     used_path if used_path != "" else model_path,
			"status":   load_status,
			"reason":   load_reason,
			"position": item.get("x", 0),
		})

# ─────────────────────────────────────────────────────────────────
# get_dimension_value — identical to image_glb_creation.gd
# ─────────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────────
# AABB helpers — identical to image_glb_creation.gd
# ─────────────────────────────────────────────────────────────────
func _get_hierarchy_aabb(node: Node3D) -> AABB:
	var aabb  := AABB()
	var first  = true
	var meshes = _get_all_meshes(node)
	if meshes.size() == 0:
		return AABB(Vector3.ZERO, Vector3.ONE)
	for mesh_inst in meshes:
		var xform    = node.global_transform.affine_inverse() * mesh_inst.global_transform
		var mesh_aabb = xform * mesh_inst.get_aabb()
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

# ─────────────────────────────────────────────────────────────────
# create_material — full parity with image_glb_creation.gd
# ─────────────────────────────────────────────────────────────────
func create_material(mat_data):
	var mat = StandardMaterial3D.new()
	mat.cull_mode        = BaseMaterial3D.CULL_DISABLED
	mat.roughness        = mat_data.get("roughness", 0.65)
	mat.metallic         = mat_data.get("metallic",  0.0)
	mat.metallic_specular = 0.5
	mat.shading_mode     = BaseMaterial3D.SHADING_MODE_PER_PIXEL
	mat.specular_mode    = BaseMaterial3D.SPECULAR_SCHLICK_GGX

	var base_color        = Color(0.82, 0.82, 0.82)
	var has_explicit_color = false
	if mat_data.has("color"):
		var c_str = str(mat_data["color"]).strip_edges()
		if c_str.length() >= 3 and not c_str.begins_with("#"):
			c_str = "#" + c_str
		if c_str.is_valid_html_color():
			base_color         = Color(c_str)
			has_explicit_color = true
		else:
			print("[VideoGLB] Warning: Invalid color string: ", c_str)
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
			mat.normal_texture  = tex

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

# ─────────────────────────────────────────────────────────────────
# Texture loading — identical to image_glb_creation.gd
# ─────────────────────────────────────────────────────────────────
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
	print("[VideoGLB] Texture not found (skipped): ", norm_path)
	return null

func load_image_texture(path):
	var img = Image.load_from_file(path)
	if img:
		return ImageTexture.create_from_image(img)
	return null
