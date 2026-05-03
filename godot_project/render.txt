extends "res://image_glb_creation.gd"

# render.gd
# Main entry point for rendering. Handles arguments, camera, and rendering output.
var use_threejs = true
var convert_blender_camera = true
var _headless = OS.has_feature("headless") or OS.has_feature("server")
var _render_data = {}

func _ready():
	print("Godot Renderer Started (Split Architecture)")
	
	var args = OS.get_cmdline_args()
	if OS.has_method("get_cmdline_user_args"):
		args.append_array(OS.get_cmdline_user_args())
	
	var input_json_path = ""
	var output_path = ""
	
	for i in range(args.size()):
		var arg = args[i]
		if arg.ends_with(".json"):
			input_json_path = arg
		elif arg.ends_with(".png") or arg.ends_with(".jpg") or arg.ends_with(".mp4") or arg == "video_output":
			output_path = arg
			
	if (input_json_path == "" or output_path == "") and args.size() >= 2:
		input_json_path = args[args.size()-2]
		output_path = args[args.size()-1]
	
	if input_json_path == "" or output_path == "":
		print("Error: Missing arguments.")
		get_tree().quit(1)
		return

	print("Input JSON: ", input_json_path)
	print("Output Path: ", output_path)
	
	if not FileAccess.file_exists(input_json_path):
		print("Error: Input file does not exist.")
		get_tree().quit(1)
		return
		
	var json_text = FileAccess.get_file_as_string(input_json_path)
	var json = JSON.new()
	var error = json.parse(json_text)
	if error != OK:
		print("Error parsing JSON: ", json.get_error_message())
		get_tree().quit(1)
		return
		
	var data = json.data
	_render_data = data
	
	set_resolution(data)
	build_scene(data) # From image_glb_creation.gd
	setup_camera(data)
	
	if _should_render_video(data):
		call_deferred("render_video", data, output_path)
	else:
		call_deferred("render_image", data, output_path)


func _should_render_video(data) -> bool:
	if not data.has("video_animation"):
		return false
	var anim_data = data["video_animation"]
	return typeof(anim_data) == TYPE_DICTIONARY and not anim_data.is_empty()

func set_resolution(data):
	var w = 1920
	var h = 1080
	
	if data.has("render_quality"):
		var quality = str(data["render_quality"]).to_upper().strip_edges()
		if quality == "12K":
			w = 12288
			h = 6642
		elif quality == "8K":
			w = 7680
			h = 4320
		elif quality == "6K":
			w = 6144
			h = 3321
		elif quality == "4K":
			w = 3840
			h = 2160
		elif quality == "2K":
			w = 2560
			h = 1440
		elif quality == "QUAD HD":
			w = 2048
			h = 1080
		elif quality == "1080P" or quality == "FHD" or quality == "FULL HD":
			w = 1920
			h = 1080
		elif quality == "HD":
			w = 1280
			h = 720

	if data.has("width"): w = int(data["width"])
	if data.has("height"): h = int(data["height"])

	var aspect_ratio_text = str(data.get("aspect_ratio", "16:9")).strip_edges()
	var aspect_ratio = _parse_aspect_ratio(aspect_ratio_text)
	if aspect_ratio > 0.0 and abs(aspect_ratio - (float(w) / float(max(h, 1)))) > 0.001:
		var base_long_side = max(w, h)
		if aspect_ratio >= 1.0:
			w = base_long_side
			h = int(round(float(base_long_side) / aspect_ratio))
		else:
			h = base_long_side
			w = int(round(float(base_long_side) * aspect_ratio))
		w = max(w, 1)
		h = max(h, 1)

	_apply_render_resolution(w, h)

	get_viewport().msaa_3d = Viewport.MSAA_8X
	get_viewport().screen_space_aa = Viewport.SCREEN_SPACE_AA_FXAA
	get_viewport().use_taa = true
	get_viewport().use_debanding = true
	get_viewport().mesh_lod_threshold = 0.0

func _apply_render_resolution(w: int, h: int) -> void:
	var size = Vector2i(max(w, 1), max(h, 1))
	print("Setting Resolution to: ", size.x, "x", size.y)
	get_viewport().size = size
	DisplayServer.window_set_size(size)
	var window = get_window()
	if window:
		window.size = size
		window.content_scale_size = size

func _parse_aspect_ratio(aspect_ratio_text: String) -> float:
	var cleaned = aspect_ratio_text.strip_edges()
	if cleaned == "":
		return -1.0
	if cleaned.find(":") != -1:
		var parts = cleaned.split(":")
		if parts.size() == 2:
			var numerator = float(str(parts[0]).strip_edges())
			var denominator = float(str(parts[1]).strip_edges())
			if numerator > 0.0 and denominator > 0.0:
				return numerator / denominator
		return -1.0
	if cleaned.find("/") != -1:
		var ratio_parts = cleaned.split("/")
		if ratio_parts.size() == 2:
			var numerator2 = float(str(ratio_parts[0]).strip_edges())
			var denominator2 = float(str(ratio_parts[1]).strip_edges())
			if numerator2 > 0.0 and denominator2 > 0.0:
				return numerator2 / denominator2
		return -1.0
	var numeric_ratio = float(cleaned)
	if numeric_ratio > 0.0:
		return numeric_ratio
	return -1.0

func setup_camera(data):
	var cam = Camera3D.new()
	cam.name = "MainCamera"
	var pos = Vector3(0, 1.5, 5)
	var target = Vector3(0, 1.0, 0)
	var need_look_at = false
	var has_view_target = false
	var use_plan_transform = abs(_scene_plan_rotation_radians) > 0.000001

	var prefer_threejs = data.get("use_threejs", use_threejs)

	var cam_data = {}
	if prefer_threejs and data.has("threejs_camera"):
		cam_data = data["threejs_camera"]
		if cam_data.has("position"):
			var p = cam_data["position"]
			pos = Vector3(float(p["x"]), float(p["y"]), float(p["z"]))
			if use_plan_transform:
				pos = _transform_plan_point_meters(pos)

		cam.position = pos

		if cam_data.has("target"):
			var t = cam_data["target"]
			target = Vector3(float(t["x"]), float(t["y"]), float(t["z"]))
			if use_plan_transform:
				target = _transform_plan_point_meters(target)
			has_view_target = true
			if not cam_data.has("rotation"):
				need_look_at = true
		if cam_data.has("rotation"):
			var r = cam_data["rotation"]
			var rot_v = Vector3(float(r["x"]), float(r["y"]), float(r["z"]))
			cam.basis = Basis.from_euler(rot_v, EULER_ORDER_XYZ)
		elif has_view_target:
			need_look_at = true

	elif data.has("blender_camera"):
		cam_data = data["blender_camera"]
		var should_convert = data.get("convert_blender_camera", convert_blender_camera)

		if cam_data.has("location"):
			var loc = cam_data["location"]
			if should_convert:
				pos = Vector3(loc[0], loc[2], -loc[1])
			else:
				pos = Vector3(loc[0], loc[1], loc[2])
			if use_plan_transform:
				pos = _transform_plan_point_meters(pos)

		cam.position = pos

		if data.has("blender_target") and data["blender_target"].has("location"):
			var t_loc = data["blender_target"]["location"]
			if should_convert:
				target = Vector3(t_loc[0], t_loc[2], -t_loc[1])
			else:
				target = Vector3(t_loc[0], t_loc[1], t_loc[2])
			if use_plan_transform:
				target = _transform_plan_point_meters(target)
			has_view_target = true
			if not cam_data.has("rotation_euler"):
				need_look_at = true
		elif cam_data.has("rotation_euler"):
			var r = cam_data["rotation_euler"]
			var rot_v = Vector3(float(r[0]), float(r[1]), float(r[2]))
			if should_convert:
				var b_basis = Basis.from_euler(rot_v, EULER_ORDER_XYZ)
				var godot_basis = Basis()
				godot_basis.x = Vector3(b_basis.x.x, b_basis.x.z, -b_basis.x.y)
				godot_basis.y = Vector3(b_basis.z.x, b_basis.z.z, -b_basis.z.y)
				godot_basis.z = Vector3(-b_basis.y.x, -b_basis.y.z, b_basis.y.y)
				cam.basis = godot_basis
			else:
				cam.basis = Basis.from_euler(rot_v, EULER_ORDER_XYZ)

	if cam_data.has("fov"):
		cam.fov = float(cam_data["fov"])

	add_child(cam)

	if need_look_at:
		if cam.global_position.distance_to(target) > 0.001:
			cam.look_at(target, Vector3.UP)

func render_image(data, output_path):
	print("Rendering Single Image...")
	await _wait_frames(2)
	var cam = get_node_or_null("MainCamera")
	if cam:
		setup_fixed_fill_lights()
		await _wait_frames(1)
	
	var img = await _capture_viewport_image(6)
	if img:
		img.save_png(output_path)
		print("Image saved to: ", output_path)
		_save_thumbnail_webp(img, output_path)
	
	save_logs(data, output_path)
	get_tree().quit(0)

func save_logs(input_data, output_image_path):
	print("Saving logs...")
	var project_root = ProjectSettings.globalize_path("res://")
	var parent_dir = project_root.get_base_dir()
	var logs_abs = parent_dir.path_join("logs")
	var input_abs = parent_dir.path_join("input_json")
	
	if not DirAccess.dir_exists_absolute(logs_abs):
		DirAccess.make_dir_recursive_absolute(logs_abs)
	if not DirAccess.dir_exists_absolute(input_abs):
		DirAccess.make_dir_recursive_absolute(input_abs)
		
	var timestamp = str(Time.get_unix_time_from_system())
	var output_basename = output_image_path.get_file().get_basename()
	
	var input_save_path = input_abs.path_join(output_basename + "_input.json")
	var json_string = JSON.stringify(input_data, "\t")
	var file_input = FileAccess.open(input_save_path, FileAccess.WRITE)
	if file_input:
		file_input.store_string(json_string)
		file_input.close()
		
	var log_data = {}
	log_data["timestamp"] = timestamp
	log_data["output_image"] = output_image_path
	
	var cam = get_node_or_null("MainCamera")
	if cam:
		log_data["camera_actual"] = {
			"position": { "x": cam.position.x, "y": cam.position.y, "z": cam.position.z },
			"fov": cam.fov
		}
	
	log_data["assets_used"] = _tracked_assets
	
	var log_path = logs_abs.path_join(output_basename + "_render_log.json")
	var log_json_str = JSON.stringify(log_data, "\t")
	var file_log = FileAccess.open(log_path, FileAccess.WRITE)
	if file_log:
		file_log.store_string(log_json_str)
		file_log.close()

func render_video(data, output_base_path):
	print("Rendering Video Sequence...")
	var anim_data = data["video_animation"]
	var fps = anim_data.get("fps", 30)
	var duration = anim_data.get("duration_seconds", 5.0)
	var total_frames = int(duration * fps)
	
	var cam = get_node_or_null("MainCamera")
	if not cam: return

	var start_pos = cam.position
	var end_pos = start_pos
	
	if anim_data.has("camera_position_start"):
		start_pos = parse_vec3(anim_data["camera_position_start"])
	if anim_data.has("camera_position_end"):
		end_pos = parse_vec3(anim_data["camera_position_end"])
		
	var base_filename = output_base_path.get_basename()
	var first_frame_saved = false
	
	setup_fixed_fill_lights() # Set up once for video
	
	for frame in range(total_frames):
		var t = float(frame) / float(total_frames - 1) if total_frames > 1 else 0.0
		cam.position = start_pos.lerp(end_pos, t)
		
		await _wait_frames(1)
		
		var img = await _capture_viewport_image(3)
		if img:
			var frame_filename = "%s_%04d.png" % [base_filename, frame]
			img.save_png(frame_filename)
			if not first_frame_saved:
				_save_thumbnail_webp(img, output_base_path)
				first_frame_saved = true
		
		if frame % 10 == 0:
			print("Rendered frame ", frame, "/", total_frames)
			
	get_tree().quit(0)

func setup_fixed_fill_lights():
	# Unified room-based lighting logic (synced with video_render.gd)
	for l in get_tree().get_nodes_in_group("fill_lights"):
		l.queue_free()

	# Dim automated room fills if manual light fixtures are already present
	var energy_multiplier = 1.0
	var fixture_count = get_tree().get_nodes_in_group("fixture_lights").size()
	if fixture_count > 0:
		energy_multiplier = 0.5
		print("[Render] %d fixture lights detected. Dimming automated room fill lights (0.5x)." % fixture_count)

	var placed = 0

	var areas = _get_floor_plan_areas()
	var verts = _get_floor_plan_vertices()

	if areas.size() > 0 and verts.size() > 0:
		for area_id in areas:
			var area = areas[area_id]
			var v_ids = area.get("vertices", [])
			if v_ids.size() < 3:
				continue

			# ── Centroid: average of all polygon vertex positions (cm → m) ──
			var sum_x = 0.0
			var sum_z = 0.0
			var count = 0
			for v_id in v_ids:
				var vs = str(v_id)
				if not verts.has(vs):
					continue
				var v = verts[vs]
				sum_x += float(v.get("x", 0)) * _scene_unit_scale
				sum_z += float(v.get("y", 0)) * _scene_unit_scale  # floor-plan Y → Godot Z
				count += 1
			if count == 0:
				continue

			var cx = sum_x / float(count)
			var cz = sum_z / float(count)

			# ── Ceiling height for THIS room ──
			var ceil_h = 2.4  # default 240 cm → 2.4 m
			if area.has("ceiling_properties") and typeof(area["ceiling_properties"]) == TYPE_DICTIONARY:
				var cp = area["ceiling_properties"]
				if cp.has("height"):
					var ch = cp["height"]
					var hf = 0.0
					if typeof(ch) == TYPE_DICTIONARY and ch.has("length"):
						hf = float(ch["length"]) * _scene_unit_scale
					elif typeof(ch) == TYPE_INT or typeof(ch) == TYPE_FLOAT:
						hf = float(ch) * _scene_unit_scale
					elif typeof(ch) == TYPE_STRING and ch.is_valid_float():
						hf = float(ch) * _scene_unit_scale
					if hf > 0.5:
						ceil_h = hf
			elif area.has("properties") and typeof(area["properties"]) == TYPE_DICTIONARY:
				var h_prop = area["properties"].get("height", null)
				if h_prop != null:
					var hf = get_dimension_value(h_prop) * _scene_unit_scale
					if hf > 0.5: ceil_h = hf

			# Light hangs 30 cm below the ceiling — well inside the room
			var light_y = ceil_h - 0.30
			light_y = max(light_y, 1.4)  # never lower than 1.4 m

			# ── Emit radius scaled to room size ──
			var min_x = 1e9; var max_x = -1e9
			var min_z = 1e9; var max_z = -1e9
			for v_id in v_ids:
				var vs = str(v_id)
				if not verts.has(vs): continue
				var v = verts[vs]
				var vx = float(v.get("x", 0)) * _scene_unit_scale
				var vz = float(v.get("y", 0)) * _scene_unit_scale
				min_x = min(min_x, vx); max_x = max(max_x, vx)
				min_z = min(min_z, vz); max_z = max(max_z, vz)
			var room_w = max(max_x - min_x, 0.5)
			var room_d = max(max_z - min_z, 0.5)
			var diag   = sqrt(room_w * room_w + room_d * room_d)
			
			# Range = diagonal + 1 m headroom, clamped between 6 m and 20 m (from video_render.gd)
			var omni_range = clamp(diag + 1.0, 6.0, 20.0)
			# Energy scales gently with room size (0.8 to 2.0 from video_render.gd)
			var energy = clamp(0.8 + diag * 0.12, 0.8, 2.0)

			var light = OmniLight3D.new()
			light.name = "RoomLight_" + str(area_id)
			light.position = Vector3(cx, light_y, cz)  # ← fixed world position
			light.light_energy = energy * energy_multiplier
			light.omni_range = omni_range
			light.shadow_enabled = false
			light.light_color = Color(1.0, 0.97, 0.90)  # warm white (match video)
			light.add_to_group("fill_lights")
			add_child(light)
			placed += 1

	if placed == 0:
		print("[Render] No area data found — placing single fallback fill light at scene centre.")
		var light = OmniLight3D.new()
		light.name = "RoomLight_Fallback"
		light.position = Vector3(0, 2.1, 0)
		light.light_energy = 1.5
		light.omni_range = 18.0
		light.shadow_enabled = false
		light.light_color = Color(1.0, 0.97, 0.90)
		light.add_to_group("fill_lights")
		add_child(light)
		placed = 1
	
	print("[Render] Placed %d room light(s) — synced with video render." % placed)

func _get_floor_plan_areas() -> Dictionary:
	var src = _render_data
	if src.has("floor_plan_data"):
		var fp = src["floor_plan_data"]
		if typeof(fp) == TYPE_STRING:
			var j = JSON.new()
			if j.parse(fp) == OK: src = j.data
		elif typeof(fp) == TYPE_DICTIONARY:
			src = fp

	# Handle nested layers structure (from video_render.gd)
	if src.has("layers") and typeof(src["layers"]) == TYPE_DICTIONARY:
		var merged = {}
		for l_id in src["layers"]:
			var layer = src["layers"][l_id]
			if typeof(layer) == TYPE_DICTIONARY and layer.has("areas"):
				var raw_areas = layer["areas"]
				if typeof(raw_areas) == TYPE_DICTIONARY:
					for a_id in raw_areas:
						merged[str(a_id)] = raw_areas[a_id]
				elif typeof(raw_areas) == TYPE_ARRAY:
					for a in raw_areas:
						if typeof(a) == TYPE_DICTIONARY and a.has("id"):
							merged[str(a["id"])] = a
		return merged
	
	var raw = src.get("areas", null)
	if raw == null: return {}
	if typeof(raw) == TYPE_DICTIONARY: return raw
	# Array format fallback
	var d = {}
	for a in raw:
		if typeof(a) == TYPE_DICTIONARY and a.has("id"):
			d[str(a["id"])] = a
	return d

func _get_floor_plan_vertices() -> Dictionary:
	var src = _render_data
	if src.has("floor_plan_data"):
		var fp = src["floor_plan_data"]
		if typeof(fp) == TYPE_STRING:
			var j = JSON.new()
			if j.parse(fp) == OK: src = j.data
		elif typeof(fp) == TYPE_DICTIONARY:
			src = fp

	# Handle nested layers structure (from video_render.gd)
	if src.has("layers") and typeof(src["layers"]) == TYPE_DICTIONARY:
		var merged = {}
		for l_id in src["layers"]:
			var layer = src["layers"][l_id]
			if typeof(layer) == TYPE_DICTIONARY and layer.has("vertices"):
				var raw_verts = layer["vertices"]
				if typeof(raw_verts) == TYPE_DICTIONARY:
					for v_id in raw_verts:
						merged[str(v_id)] = raw_verts[v_id]
				elif typeof(raw_verts) == TYPE_ARRAY:
					for v in raw_verts:
						if typeof(v) == TYPE_DICTIONARY and v.has("id"):
							merged[str(v["id"])] = v
		return merged
	
	var raw = src.get("vertices", null)
	if raw == null: return {}
	if typeof(raw) == TYPE_DICTIONARY: return raw
	# Array format fallback
	var d = {}
	for v in raw:
		if typeof(v) == TYPE_DICTIONARY and v.has("id"):
			d[str(v["id"])] = v
	return d

func get_cardinal_direction(dir: Vector3) -> String:
	var flat_dir = Vector2(dir.x, dir.z).normalized()
	var deg = rad_to_deg(flat_dir.angle())
	if deg >= -22.5 and deg < 22.5: return "East"
	if deg >= 22.5 and deg < 67.5: return "South-East"
	if deg >= 67.5 and deg < 112.5: return "South"
	if deg >= 112.5 and deg < 157.5: return "South-West"
	if deg >= 157.5 or deg < -157.5: return "West"
	if deg >= -157.5 and deg < -112.5: return "North-West"
	if deg >= -112.5 and deg < -67.5: return "North"
	if deg >= -67.5 and deg < -22.5: return "North-East"
	return "Unknown"

func _save_thumbnail_webp(source_image: Image, output_path: String) -> void:
	if source_image == null or source_image.is_empty(): return
	var thumb_path = output_path.get_base_dir().path_join(output_path.get_file().get_basename() + "_thumb.webp")
	var thumb = source_image.duplicate()
	var max_dim = 480
	var w = thumb.get_width(); var h = thumb.get_height()
	if w > max_dim or h > max_dim:
		if w >= h: thumb.resize(max_dim, int(float(h)*max_dim/w), Image.INTERPOLATE_LANCZOS)
		else: thumb.resize(int(float(w)*max_dim/h), max_dim, Image.INTERPOLATE_LANCZOS)
	thumb.save_webp(thumb_path, true, 0.75)

func _wait_frames(count: int) -> void:
	for i in range(count): await get_tree().process_frame

func _capture_viewport_image(retries: int = 3):
	var vp = get_viewport()
	for i in range(max(1, retries)):
		RenderingServer.force_draw()
		await get_tree().process_frame
		var tex = vp.get_texture()
		if tex:
			var img = tex.get_image()
			if img and not img.is_empty(): return img
		await get_tree().process_frame
	return null
