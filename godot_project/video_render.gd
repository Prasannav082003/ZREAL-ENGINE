extends "res://image_glb_creation.gd"

# video_render.gd
# Fast video renderer using Godot's built-in MovieWriter (--write-movie).
# Instead of manually saving PNGs, this script advances the camera each frame
# in _process() and MovieWriter captures every frame automatically.

var use_threejs = true
var convert_blender_camera = true

# Video state
var _keyframes = []
var _current_frame: int = 0
var _total_frames: int = 0
var _fps: int = 30
var _base_filename: String = ""
var _rendering: bool = false
var _scene_ready: bool = false
var _warmup_frames: int = 0
var _use_movie_writer: bool = false
var _use_png_fallback: bool = false
var _needs_auto_pan: bool = false

func _ready():
	print("Godot VIDEO Renderer Started")
	
	var args = OS.get_cmdline_args()
	if OS.has_method("get_cmdline_user_args"):
		args.append_array(OS.get_cmdline_user_args())
	
	var input_json_path = ""
	var output_path = ""
	
	for i in range(args.size()):
		var arg = args[i]
		if arg.ends_with(".json"):
			input_json_path = arg
		elif arg.ends_with(".png") or arg.ends_with(".jpg") or arg.ends_with(".mp4") or arg.ends_with(".avi") or arg == "video_output":
			output_path = arg
			
	if (input_json_path == "" or output_path == "") and args.size() >= 2:
		input_json_path = args[args.size()-2]
		output_path = args[args.size()-1]
	
	if input_json_path == "":
		print("Error: Missing input JSON argument.")
		get_tree().quit(1)
		return

	print("Input JSON: ", input_json_path)
	print("Output Path: ", output_path)
	
	# Detect if MovieWriter is active
	_use_movie_writer = Engine.get_write_movie_path() != ""
	if _use_movie_writer:
		print("MovieWriter ACTIVE - recording to: ", Engine.get_write_movie_path())
	else:
		print("MovieWriter NOT active - using PNG fallback")
		_use_png_fallback = true
		if output_path != "":
			_base_filename = output_path.get_basename()
	
	if not FileAccess.file_exists(input_json_path):
		print("Error: Input file does not exist: ", input_json_path)
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
	
	# Build the 3D scene
	set_resolution(data)
	build_scene(data)
	setup_camera(data)
	
	# Extract keyframes
	if data.has("video_animation") and data["video_animation"] != null:
		var anim_data = data["video_animation"]
		_fps = int(anim_data.get("fps", 30))
		_keyframes = anim_data.get("keyframes", [])
		_total_frames = _keyframes.size()
		
		if _total_frames == 0:
			# Lerp fallback - generate keyframes from start/end
			var duration = anim_data.get("duration_seconds", 5.0)
			_total_frames = int(duration * _fps)
			_generate_lerp_keyframes(anim_data, _total_frames)
		
		print("Video: ", _total_frames, " frames at ", _fps, " fps")
		print("Expected duration: ", float(_total_frames) / float(_fps), " seconds")
	else:
		# No video_animation — auto-generate horizontal pan (deferred to warmup phase)
		print("No video_animation found — will generate 5-second horizontal pan from camera position")
		_fps = 30
		var duration = 5.0
		_total_frames = int(duration * _fps)
		_needs_auto_pan = true  # Generate pan after scene settles
	
	# Set camera to first frame immediately (only if we have keyframes already)
	if _total_frames > 0 and _keyframes.size() > 0:
		_apply_keyframe(0)
	
	# We need a few warmup frames for the scene to settle
	_warmup_frames = 4
	_current_frame = 0
	_rendering = false

func _process(_delta):
	# Warmup phase - let the scene settle
	if _warmup_frames > 0:
		_warmup_frames -= 1
		if _warmup_frames == 0:
			# Generate auto-pan AFTER scene has settled (global_transform is now reliable)
			if _needs_auto_pan:
				_generate_horizontal_pan(_total_frames)
				_needs_auto_pan = false
				if _keyframes.size() > 0:
					_apply_keyframe(0)
			
			_rendering = true
			print("Scene settled. Starting video render...")
			# Set up smart lights for first frame
			var cam = get_node_or_null("MainCamera")
			if cam:
				setup_smart_point_lights(cam)
		return
	
	if not _rendering:
		return
	
	# Check if done
	if _current_frame >= _total_frames:
		print("============================================================")
		print("VIDEO RENDER COMPLETE - ", _total_frames, " frames rendered.")
		print("============================================================")
		get_tree().quit(0)
		return
	
	# Apply current keyframe
	_apply_keyframe(_current_frame)
	
	# PNG fallback: manually save frame
	if _use_png_fallback and _base_filename != "":
		var img = get_viewport().get_texture().get_image()
		if img and not img.is_empty():
			var frame_filename = "%s_%04d.png" % [_base_filename, _current_frame]
			img.save_png(frame_filename)
	
	# Progress logging
	if _current_frame % 10 == 0 or _current_frame == _total_frames - 1:
		var pct = int(float(_current_frame) / float(_total_frames) * 100.0)
		print("Frame ", _current_frame, "/", _total_frames, " (", pct, "%)")
	
	_current_frame += 1

func _apply_keyframe(frame_idx: int):
	var cam = get_node_or_null("MainCamera")
	if not cam:
		return
	
	var kf = _keyframes[frame_idx]
	
	# Position
	if kf.has("position") and kf["position"] != null:
		var p = kf["position"]
		cam.position = Vector3(float(p["x"]), float(p["y"]), float(p["z"]))
	
	# Orientation: Prefer target, fallback to rotation
	if kf.has("target") and kf["target"] != null:
		var t = kf["target"]
		var tgt = Vector3(float(t["x"]), float(t["y"]), float(t["z"]))
		if cam.position.distance_to(tgt) > 0.001:
			cam.look_at(tgt)
	elif kf.has("rotation") and kf["rotation"] != null:
		var r = kf["rotation"]
		var rot_v = Vector3(float(r["x"]), float(r["y"]), float(r["z"]))
		cam.basis = Basis.from_euler(rot_v, EULER_ORDER_XYZ)
	
	# Update smart lights every 5 frames (optimization)
	if frame_idx % 5 == 0:
		setup_smart_point_lights(cam)

func _generate_lerp_keyframes(anim_data, total_frames: int):
	var cam = get_node_or_null("MainCamera")
	if not cam:
		return
	
	var start_pos = cam.position
	var end_pos = start_pos
	
	if anim_data.has("camera_position_start") and anim_data["camera_position_start"] != null:
		start_pos = parse_vec3(anim_data["camera_position_start"])
	if anim_data.has("camera_position_end") and anim_data["camera_position_end"] != null:
		end_pos = parse_vec3(anim_data["camera_position_end"])
	
	var start_target = null
	var end_target = null
	if anim_data.has("camera_target_start") and anim_data["camera_target_start"] != null:
		start_target = parse_vec3(anim_data["camera_target_start"])
	if anim_data.has("camera_target_end") and anim_data["camera_target_end"] != null:
		end_target = parse_vec3(anim_data["camera_target_end"])
	
	_keyframes = []
	for frame in range(total_frames):
		var t = float(frame) / float(total_frames - 1) if total_frames > 1 else 0.0
		var pos = start_pos.lerp(end_pos, t)
		var kf = {"position": {"x": pos.x, "y": pos.y, "z": pos.z}}
		
		if start_target != null and end_target != null:
			var tgt = start_target.lerp(end_target, t)
			kf["target"] = {"x": tgt.x, "y": tgt.y, "z": tgt.z}
		
		_keyframes.append(kf)
	
	print("Generated ", _keyframes.size(), " lerp keyframes")

func _generate_horizontal_pan(total_frames: int):
	# Generate a horizontal pan from the camera's initial position
	# Camera moves sideways (along its local right axis) while looking at a fixed target
	var cam = get_node_or_null("MainCamera")
	if not cam:
		return
	
	var start_pos = cam.global_position
	var forward = -cam.global_transform.basis.z.normalized()
	var right = cam.global_transform.basis.x.normalized()
	
	# Target point: where the camera is currently looking (5 meters ahead)
	var look_distance = 5.0
	var target_point = start_pos + forward * look_distance
	
	# Pan distance: move camera 3 meters to the right over 5 seconds
	var pan_distance = 3.0
	
	_keyframes = []
	for frame in range(total_frames):
		var t = float(frame) / float(total_frames - 1) if total_frames > 1 else 0.0
		# Move camera horizontally along the right axis
		var offset = right * (t * pan_distance - pan_distance * 0.5)  # Center the pan
		var pos = start_pos + offset
		
		var kf = {
			"position": {"x": pos.x, "y": pos.y, "z": pos.z},
			"target": {"x": target_point.x, "y": target_point.y, "z": target_point.z}
		}
		_keyframes.append(kf)
	
	print("Generated ", _keyframes.size(), " horizontal pan keyframes")
	print("  Start: ", start_pos, " Target: ", target_point, " Pan: ", pan_distance, "m")

# ─────────────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────────
# Camera Setup (Initial)
# ─────────────────────────────────────────────────────────────────
func setup_camera(data):
	var cam = Camera3D.new()
	cam.name = "MainCamera"
	var pos = Vector3(0, 1.5, 5)
	var target = Vector3(0, 1.0, 0)
	
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
			cam.look_at(target)
			print("Camera Setup (ThreeJS): Pos=", pos, " Target=", target)
		
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
			cam.look_at(target)
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

	if cam_data.has("fov"): cam.fov = float(cam_data["fov"])
		
	add_child(cam)

# ─────────────────────────────────────────────────────────────────
# Smart Point Lights
# ─────────────────────────────────────────────────────────────────
func setup_smart_point_lights(cam: Camera3D):
	var existing = get_tree().get_nodes_in_group("smart_lights")
	for l in existing:
		l.queue_free()
	
	var space_state = cam.get_world_3d().direct_space_state
	if not space_state:
		return
		
	var cam_pos = cam.global_position
	var basis = cam.global_transform.basis
	var forward = -basis.z
	var right = basis.x
	var up = basis.y
	
	var light_configs = [
		{"offset": Vector3(0, 0.5, 0), "energy": 1.5, "range": 15.0, "name": "Near"},
		{"offset": forward * 2.5 + up * 0.5, "energy": 1.2, "range": 12.0, "name": "Ahead"},
		{"offset": right * 1.8 + up * 0.2, "energy": 0.8, "range": 10.0, "name": "Right"},
		{"offset": -right * 1.8 + up * 0.2, "energy": 0.8, "range": 10.0, "name": "Left"},
		{"offset": -forward * 1.2 + up * 1.0, "energy": 0.6, "range": 10.0, "name": "Behind"}
	]
	
	for config in light_configs:
		var target_pos = cam_pos + config["offset"]
		var safe_pos = _get_safe_pos(cam_pos, target_pos, space_state)
		
		var light = OmniLight3D.new()
		light.name = "SmartLight_" + config["name"]
		light.position = safe_pos
		light.light_energy = config["energy"]
		light.omni_range = config["range"]
		light.shadow_enabled = true
		light.light_color = Color(1.0, 0.98, 0.92)
		light.add_to_group("smart_lights")
		add_child(light)

func _get_safe_pos(start: Vector3, end: Vector3, space_state: PhysicsDirectSpaceState3D) -> Vector3:
	var query = PhysicsRayQueryParameters3D.create(start, end)
	query.collide_with_bodies = true
	query.collide_with_areas = false
	
	var result = space_state.intersect_ray(query)
	if result:
		var hit_pos = result.position
		var dir = (hit_pos - start).normalized()
		var dist = (hit_pos - start).length()
		var back_off = min(0.25, dist * 0.4)
		return hit_pos - dir * back_off
		
	return end
