extends "res://image_glb_creation.gd"

# render.gd
# Main entry point for rendering. Handles arguments, camera, and rendering output.
var use_threejs = true
var convert_blender_camera = true
var _headless = OS.has_feature("headless") or OS.has_feature("server")

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
	
	set_resolution(data)
	build_scene(data) # From image_glb_creation.gd
	setup_camera(data)
	
	if data.has("video_animation"):
		call_deferred("render_video", data, output_path)
	else:
		call_deferred("render_image", data, output_path)

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
			
	print("Setting Resolution to: ", w, "x", h)
	get_viewport().size = Vector2i(w, h)
	
	get_viewport().msaa_3d = Viewport.MSAA_4X
	get_viewport().screen_space_aa = Viewport.SCREEN_SPACE_AA_FXAA
	get_viewport().use_taa = true
	get_viewport().use_debanding = true
	get_viewport().mesh_lod_threshold = 0.0

func setup_camera(data):
	var cam = Camera3D.new()
	cam.name = "MainCamera"
	var pos = Vector3(0, 1.5, 5)
	var target = Vector3(0, 1.0, 0)
	var need_look_at = false
	var need_look_at_blender = false

	var prefer_threejs = data.get("use_threejs", use_threejs)

	var cam_data = {}
	if prefer_threejs and data.has("threejs_camera"):
		cam_data = data["threejs_camera"]
		if cam_data.has("position"):
			var p = cam_data["position"]
			pos = Vector3(float(p["x"]), float(p["y"]), float(p["z"]))

		cam.position = pos

		if cam_data.has("rotation"):
			var r = cam_data["rotation"]
			var rot_v = Vector3(float(r["x"]), float(r["y"]), float(r["z"]))
			cam.basis = Basis.from_euler(rot_v, EULER_ORDER_XYZ)
			print("Camera Setup (ThreeJS): Pos=", pos, " Rotation=", cam.rotation)
		elif cam_data.has("target"):
			var t = cam_data["target"]
			target = Vector3(float(t["x"]), float(t["y"]), float(t["z"]))
			need_look_at = true   # defer until after add_child

	elif data.has("blender_camera"):
		cam_data = data["blender_camera"]
		var should_convert = data.get("convert_blender_camera", convert_blender_camera)

		if cam_data.has("location"):
			var loc = cam_data["location"]
			if should_convert:
				pos = Vector3(loc[0], loc[2], -loc[1])
			else:
				pos = Vector3(loc[0], loc[1], loc[2])

		cam.position = pos

		if data.has("blender_target") and data["blender_target"].has("location"):
			var t_loc = data["blender_target"]["location"]
			if should_convert:
				target = Vector3(t_loc[0], t_loc[2], -t_loc[1])
			else:
				target = Vector3(t_loc[0], t_loc[1], t_loc[2])
			need_look_at = true   # defer until after add_child
			print("Camera Setup (Blender): Pos=", pos, " Target=", target)
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
			print("Camera Setup (Blender): Pos=", pos, " Rotation=", cam.rotation)

	if cam_data.has("fov"):
		cam.fov = float(cam_data["fov"])

	# CRITICAL FIX: add_child BEFORE any look_at() call.
	# look_at() needs the node inside the scene tree to access global_transform.
	# Calling look_at() before add_child() crashes silently and aborts _ready(),
	# which is why Godot opens but render_image() never gets called.
	add_child(cam)

	# Now safe to call look_at() — node is in the tree
	if need_look_at:
		# Guard against degenerate case where camera position == target
		if pos.distance_to(target) > 0.001:
			cam.look_at(target, Vector3.UP)
			print("Camera look_at: Pos=", pos, " Target=", target)
		else:
			print("Warning: camera pos == target, skipping look_at")

func render_image(data, output_path):
	print("Rendering Single Image...")
	await _wait_frames(2)
	var cam = get_node_or_null("MainCamera")
	if cam:
		setup_smart_point_lights(cam)
		# Wait one more frame for lights to register if needed, 
		# though OmniLight3D is usually immediate for the next force_draw
		await _wait_frames(1)
	
	var img = await _capture_viewport_image(6)
	if img:
		img.save_png(output_path)
		print("Image saved to: ", output_path)
		# Save WebP thumbnail alongside the rendered image
		_save_thumbnail_webp(img, output_path)
	else:
		print("Error: Failed to capture image from viewport.")
	
	save_logs(data, output_path)
	get_tree().quit(0)

func save_logs(input_data, output_image_path):
	print("Saving logs...")
	
	# Save logs and input_json to sibling directories (one level above godot_project)
	var project_root = ProjectSettings.globalize_path("res://")
	var parent_dir = project_root.get_base_dir()  # Go up from godot_project/
	var logs_abs = parent_dir.path_join("logs")
	var input_abs = parent_dir.path_join("input_json")
	
	# Use absolute paths for both DirAccess and FileAccess
	var logs_dir = logs_abs
	var input_save_dir = input_abs
	
	if not DirAccess.dir_exists_absolute(logs_abs):
		DirAccess.make_dir_recursive_absolute(logs_abs)
	if not DirAccess.dir_exists_absolute(input_abs):
		DirAccess.make_dir_recursive_absolute(input_abs)
		
	var timestamp = str(Time.get_unix_time_from_system())
	
	# Use the output render filename as the base for log/input filenames
	var output_basename = output_image_path.get_file().get_basename()
	
	# 1. Save Input JSON copy
	var input_filename = output_basename + "_input.json"
	var input_save_path = input_save_dir.path_join(input_filename)
	
	var json_string = JSON.stringify(input_data, "\t")
	var file_input = FileAccess.open(input_save_path, FileAccess.WRITE)
	if file_input:
		file_input.store_string(json_string)
		file_input.close()
		print("Saved copy of input JSON to: ", input_save_path)
	else:
		print("Error saving input JSON copy to ", input_save_path)
		
	# 2. detailed Log JSON
	var log_data = {}
	log_data["timestamp"] = timestamp
	log_data["input_file_saved"] = input_save_path
	log_data["output_image"] = output_image_path
	
	# Camera Info
	var cam = get_node_or_null("MainCamera")
	var cam_info = {}
	if cam:
		cam_info["position"] = { "x": cam.position.x, "y": cam.position.y, "z": cam.position.z }
		cam_info["rotation_degrees"] = { "x": cam.rotation_degrees.x, "y": cam.rotation_degrees.y, "z": cam.rotation_degrees.z }
		cam_info["fov"] = cam.fov
		
		# Viewing direction
		var forward = -cam.global_transform.basis.z.normalized()
		cam_info["look_direction_vector"] = { "x": forward.x, "y": forward.y, "z": forward.z }
		cam_info["cardinal_direction"] = get_cardinal_direction(forward)
		
	log_data["camera_actual"] = cam_info
	
	log_data["camera_input_threejs"] = input_data.get("threejs_camera", {})
	log_data["camera_input_blender"] = input_data.get("blender_camera", {})
	
	# Assets Used
	log_data["assets_used"] = _tracked_assets
	
	var log_filename = output_basename + "_render_log.json"
	var log_path = logs_dir.path_join(log_filename)
	
	var log_json_str = JSON.stringify(log_data, "\t")
	var file_log = FileAccess.open(log_path, FileAccess.WRITE)
	if file_log:
		file_log.store_string(log_json_str)
		file_log.close()
		print("Saved render log to: ", log_path)
	else:
		print("Error saving render log to ", log_path)

func get_cardinal_direction(dir: Vector3) -> String:
	# Ignore Y component for cardinal direction
	var flat_dir = Vector2(dir.x, dir.z).normalized()
	
	# Godot Coordinate System:
	# -Z is North, +Z is South
	# +X is East,  -X is West
	
	var angle = rad_to_deg(flat_dir.angle()) # Angle in -PI to PI
	# Vector2.angle() returns angle relative to +X (East)
	# 0 = East
	# PI/2 (90) = South (+Z)
	# PI (180) = West (-X)
	# -PI/2 (-90) = North (-Z)
	
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
	
	for frame in range(total_frames):
		var t = float(frame) / float(total_frames - 1) if total_frames > 1 else 0.0
		cam.position = start_pos.lerp(end_pos, t)
		setup_smart_point_lights(cam)
		
		await _wait_frames(1)
		
		var img = await _capture_viewport_image(3)
		var frame_filename = "%s_%04d.png" % [base_filename, frame]
		if img:
			img.save_png(frame_filename)
			# Save the 1st frame as WebP thumbnail for the video
			if not first_frame_saved:
				_save_thumbnail_webp(img, output_base_path)
				first_frame_saved = true
		else:
			print("Warning: empty frame at index ", frame)
		
		if frame % 10 == 0:
			print("Rendered frame ", frame, "/", total_frames)
			
	get_tree().quit(0)

func setup_smart_point_lights(cam: Camera3D):
	# Clear existing smart lights to allow re-placement (important for video)
	var existing = get_tree().get_nodes_in_group("smart_lights")
	for l in existing:
		l.queue_free()
	
	var space_state = cam.get_world_3d().direct_space_state
	if not space_state:
		print("Warning: Physics space state not available for smart lights.")
		return
		
	var cam_pos = cam.global_position
	var basis = cam.global_transform.basis
	var forward = -basis.z
	var right = basis.x
	var up = basis.y
	
	# Define a set of light offsets relative to the camera
	# We want to illuminate the scene around and in front of the camera
	var light_configs = [
		{"offset": Vector3(0, 0.5, 0), "energy": 1.5, "range": 15.0, "name": "Near"},
		{"offset": forward * 2.5 + up * 0.5, "energy": 1.2, "range": 12.0, "name": "Ahead"},
		{"offset": right * 1.8 + up * 0.2, "energy": 0.8, "range": 10.0, "name": "Right"},
		{"offset": -right * 1.8 + up * 0.2, "energy": 0.8, "range": 10.0, "name": "Left"},
		{"offset": -forward * 1.2 + up * 1.0, "energy": 0.6, "range": 10.0, "name": "Behind"}
	]
	
	print("Placing ", light_configs.size(), " smart point lights...")
	
	for config in light_configs:
		var target_pos = cam_pos + config["offset"]
		var safe_pos = _get_safe_pos(cam_pos, target_pos, space_state)
		
		var light = OmniLight3D.new()
		light.name = "SmartLight_" + config["name"]
		light.position = safe_pos
		light.light_energy = config["energy"]
		light.omni_range = config["range"]
		light.shadow_enabled = true
		# Neutral slightly warm light
		light.light_color = Color(1.0, 0.98, 0.92)
		# Add to group so we can find and clean them up
		light.add_to_group("smart_lights")
		add_child(light)

func _get_safe_pos(start: Vector3, end: Vector3, space_state: PhysicsDirectSpaceState3D) -> Vector3:
	var query = PhysicsRayQueryParameters3D.create(start, end)
	# Collide with everything (walls, floors, etc.)
	query.collide_with_bodies = true
	query.collide_with_areas = false
	
	var result = space_state.intersect_ray(query)
	if result:
		var hit_pos = result.position
		var dir = (hit_pos - start).normalized()
		var dist = (hit_pos - start).length()
		
		# Back off from the hit point to avoid intersecting geometry
		# We use a smaller back-off if the total distance is very small
		var back_off = min(0.25, dist * 0.4) 
		return hit_pos - dir * back_off
		
	return end

# ─────────────────────────────────────────────────────────────────
# Thumbnail Generation
# ─────────────────────────────────────────────────────────────────
func _save_thumbnail_webp(source_image: Image, output_path: String) -> void:
	"""Saves a small WebP thumbnail next to the render output.
	The thumbnail is saved with a '_thumb.webp' suffix in the same directory.
	Max dimension is 480px, WebP quality 75% to keep file size low."""
	if source_image == null or source_image.is_empty():
		print("Warning: Cannot create thumbnail — source image is empty.")
		return
	
	# Determine thumbnail path: same directory, basename + _thumb.webp
	var dir = output_path.get_base_dir()
	var basename = output_path.get_file().get_basename()
	var thumb_path = dir.path_join(basename + "_thumb.webp")
	
	# Create a copy to avoid mutating the original image
	var thumb = source_image.duplicate()
	
	# Resize to max 480px on the longest side, preserving aspect ratio
	var max_dim = 480
	var w = thumb.get_width()
	var h = thumb.get_height()
	if w > max_dim or h > max_dim:
		if w >= h:
			var new_w = max_dim
			var new_h = int(float(h) * float(max_dim) / float(w))
			thumb.resize(new_w, max(new_h, 1), Image.INTERPOLATE_LANCZOS)
		else:
			var new_h = max_dim
			var new_w = int(float(w) * float(max_dim) / float(h))
			thumb.resize(max(new_w, 1), new_h, Image.INTERPOLATE_LANCZOS)
	
	# Save as WebP with lossy compression (quality 0.75 = 75%)
	var err = thumb.save_webp(thumb_path, true, 0.75)
	if err == OK:
		print("Thumbnail saved to: ", thumb_path)
	else:
		print("Warning: Failed to save thumbnail. Error code: ", err)

func _wait_frames(count: int) -> void:
	for i in range(count):
		await get_tree().process_frame

func _capture_viewport_image(retries: int = 3):
	var vp = get_viewport()
	for i in range(max(1, retries)):
		RenderingServer.force_draw()
		await get_tree().process_frame
		var tex = vp.get_texture()
		if tex:
			var img = tex.get_image()
			if img and not img.is_empty():
				return img
		await get_tree().process_frame
	return null