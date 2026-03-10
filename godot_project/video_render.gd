extends "res://video_glb_creation.gd"

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
var _render_data = {}    # Full parsed JSON — used by setup_fixed_fill_lights()
var _output_path: String = ""  # Stored for save_logs at completion

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
	_render_data = data   # Cache for later use by setup_fixed_fill_lights()
	_output_path = output_path  # Store for save_logs at completion
	set_resolution(data)
	build_scene(data)
	# Set up fill lights IMMEDIATELY so they exist from frame 0.
	# If done during warmup, the first few frames captured by MovieWriter are dark.
	setup_fixed_fill_lights()
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
		
		# Log keyframe format detection
		if _total_frames > 0 and _keyframes.size() > 0:
			var first_kf = _keyframes[0]
			if first_kf.has("threejs_camera_data"):
				print("Keyframe format: threejs_camera_data (position, lookAt, quaternion, euler, fov)")
			else:
				print("Keyframe format: legacy (position, target, rotation)")
		
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
	
	# Warmup: give Godot's renderer (shadow maps, ambient cache, GI) time to settle
	# before the first frame is captured. 10 frames is safe even at 4K.
	_warmup_frames = 10
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
			# Fill lights were already set up in _ready() - no need to call again here
		return
	
	if not _rendering:
		return
	
	# Check if done
	if _current_frame >= _total_frames:
		print("============================================================")
		print("VIDEO RENDER COMPLETE - ", _total_frames, " frames rendered.")
		print("============================================================")
		save_logs(_render_data, _output_path)
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
	
	# Coordinate scale: JSON positions are in centimetres; Godot uses metres
	var CM = 0.01

	# Check if this keyframe has threejs_camera_data (preferred format)
	if kf.has("threejs_camera_data") and kf["threejs_camera_data"] != null:
		var tjs = kf["threejs_camera_data"]

		# Position (cm → metres)
		if tjs.has("position") and tjs["position"] != null:
			var p = tjs["position"]
			cam.position = Vector3(float(p["x"]) * CM, float(p["y"]) * CM, float(p["z"]) * CM)

		var orientation_set = false

		# lookAt (cm → metres) — guard against straight-down look
		if tjs.has("lookAt") and tjs["lookAt"] != null:
			var la = tjs["lookAt"]
			var look_target = Vector3(float(la["x"]) * CM, float(la["y"]) * CM, float(la["z"]) * CM)
			if cam.position.distance_to(look_target) > 0.001:
				var diff_v = (look_target - cam.position).normalized()
				var up_vec = Vector3.UP
				if abs(diff_v.dot(Vector3.UP)) > 0.999:
					up_vec = Vector3.BACK  # Use -Z as up when camera points straight down/up
				cam.look_at(look_target, up_vec)
				orientation_set = true

		# Quaternion fallback (skip identity)
		if not orientation_set and tjs.has("quaternion") and tjs["quaternion"] != null:
			var q = tjs["quaternion"]
			var qx = float(q["x"])
			var qy = float(q["y"])
			var qz = float(q["z"])
			var qw = float(q["w"])
			if abs(qx) > 0.0001 or abs(qy) > 0.0001 or abs(qz) > 0.0001 or abs(qw - 1.0) > 0.0001:
				cam.basis = Basis(Quaternion(qx, qy, qz, qw).normalized())
				orientation_set = true

		# Euler fallback
		if not orientation_set and tjs.has("euler") and tjs["euler"] != null:
			var e = tjs["euler"]
			var ex = float(e["x"])
			var ey = float(e["y"])
			var ez = float(e["z"])
			if abs(ex) > 0.0001 or abs(ey) > 0.0001 or abs(ez) > 0.0001:
				cam.basis = Basis.from_euler(Vector3(ex, ey, ez), EULER_ORDER_XYZ)
				orientation_set = true

		# Per-frame FOV
		if tjs.has("fov") and tjs["fov"] != null:
			cam.fov = float(tjs["fov"])
	else:
		# Legacy format (positions already expected in Godot units)
		if kf.has("position") and kf["position"] != null:
			var p = kf["position"]
			cam.position = Vector3(float(p["x"]) * CM, float(p["y"]) * CM, float(p["z"]) * CM)

		if kf.has("target") and kf["target"] != null:
			var t = kf["target"]
			var tgt = Vector3(float(t["x"]) * CM, float(t["y"]) * CM, float(t["z"]) * CM)
			if cam.position.distance_to(tgt) > 0.001:
				var diff_v = (tgt - cam.position).normalized()
				var up_vec = Vector3.UP
				if abs(diff_v.dot(Vector3.UP)) > 0.999:
					up_vec = Vector3.BACK
				cam.look_at(tgt, up_vec)
		elif kf.has("rotation") and kf["rotation"] != null:
			var r = kf["rotation"]
			cam.basis = Basis.from_euler(Vector3(float(r["x"]), float(r["y"]), float(r["z"])), EULER_ORDER_XYZ)

	# Top-level FOV (legacy)
	if kf.has("fov") and kf["fov"] != null:
		cam.fov = float(kf["fov"])

	# ── Smooth interpolation toward this frame's target ──────────────────
	# After hard-setting position/rotation above, we blend the camera
	# gently toward the NEXT keyframe so movement is fluid (no snap/jitter).
	if frame_idx < _keyframes.size() - 1:
		var next_kf = _keyframes[frame_idx + 1]
		var smooth_t = _smooth_step(0.15)   # blend 15% toward next frame each tick
		# Smooth position
		var next_pos = _extract_kf_position(next_kf)
		if next_pos != null:
			cam.position = cam.position.lerp(next_pos, smooth_t)
		# Smooth rotation (slerp quaternion so gimbal-lock cannot occur)
		var next_quat = _extract_kf_quaternion(next_kf, cam)
		if next_quat != null:
			var cur_q = cam.basis.get_rotation_quaternion().normalized()
			var blended = cur_q.slerp(next_quat.normalized(), smooth_t)
			cam.basis = Basis(blended)

	# Fixed lights are set up once — no per-frame update needed

# ─────────────────────────────────────────────────────────────────
# Smooth Camera Helpers
# ─────────────────────────────────────────────────────────────────

# Cubic ease-in-out smoothstep for the given blend factor
func _smooth_step(t: float) -> float:
	return t * t * (3.0 - 2.0 * t)

# Extract world position (metres) from any keyframe format, or return null
func _extract_kf_position(kf: Dictionary):
	var CM = 0.01
	if kf.has("threejs_camera_data") and kf["threejs_camera_data"] != null:
		var tjs = kf["threejs_camera_data"]
		if tjs.has("position") and tjs["position"] != null:
			var p = tjs["position"]
			return Vector3(float(p["x"]) * CM, float(p["y"]) * CM, float(p["z"]) * CM)
	elif kf.has("position") and kf["position"] != null:
		var p = kf["position"]
		return Vector3(float(p["x"]) * CM, float(p["y"]) * CM, float(p["z"]) * CM)
	return null

# Extract rotation as Quaternion from any keyframe format.
# If only a lookAt / target is provided, compute it from cam's current position.
func _extract_kf_quaternion(kf: Dictionary, cam: Camera3D):
	var CM = 0.01
	if kf.has("threejs_camera_data") and kf["threejs_camera_data"] != null:
		var tjs = kf["threejs_camera_data"]
		# Quaternion field
		if tjs.has("quaternion") and tjs["quaternion"] != null:
			var q = tjs["quaternion"]
			return Quaternion(float(q["x"]), float(q["y"]), float(q["z"]), float(q["w"]))
		# lookAt → derive quaternion from direction
		if tjs.has("lookAt") and tjs["lookAt"] != null:
			var la = tjs["lookAt"]
			var look_target = Vector3(float(la["x"]) * CM, float(la["y"]) * CM, float(la["z"]) * CM)
			var next_pos = _extract_kf_position(kf)
			var origin = next_pos if next_pos != null else cam.global_position
			if origin.distance_to(look_target) > 0.001:
				var dir = (look_target - origin).normalized()
				var up = Vector3.UP
				if abs(dir.dot(Vector3.UP)) > 0.999: up = Vector3.BACK
				var tmp = Camera3D.new()  # temp node to use look_at math
				tmp.position = origin
				tmp.look_at(look_target, up)
				var q = tmp.basis.get_rotation_quaternion()
				tmp.free()
				return q
		# Euler fallback
		if tjs.has("euler") and tjs["euler"] != null:
			var e = tjs["euler"]
			return Basis.from_euler(Vector3(float(e["x"]), float(e["y"]), float(e["z"])), EULER_ORDER_XYZ).get_rotation_quaternion()
	elif kf.has("target") and kf["target"] != null:
		var t = kf["target"]
		var tgt = Vector3(float(t["x"]) * CM, float(t["y"]) * CM, float(t["z"]) * CM)
		var next_pos = _extract_kf_position(kf)
		var origin = next_pos if next_pos != null else cam.global_position
		if origin.distance_to(tgt) > 0.001:
			var dir = (tgt - origin).normalized()
			var up = Vector3.UP
			if abs(dir.dot(Vector3.UP)) > 0.999: up = Vector3.BACK
			var tmp = Camera3D.new()
			tmp.position = origin
			tmp.look_at(tgt, up)
			var q = tmp.basis.get_rotation_quaternion()
			tmp.free()
			return q
	return null

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

	# Anti-aliasing for video:
	# TAA is intentionally DISABLED — it accumulates frames over time and causes
	# strong ghosting/blur whenever the camera moves, making the video look smeared.
	# MSAA_4X gives sharp per-frame quality without temporal artifacts.
	get_viewport().msaa_3d = Viewport.MSAA_4X
	get_viewport().screen_space_aa = Viewport.SCREEN_SPACE_AA_FXAA
	get_viewport().use_taa = false          # ← must stay OFF during animated renders
	get_viewport().use_debanding = true
	get_viewport().mesh_lod_threshold = 0.0

# ─────────────────────────────────────────────────────────────────
# Camera Setup (Initial)
# ─────────────────────────────────────────────────────────────────
func setup_camera(data):
	var cam = Camera3D.new()
	cam.name = "MainCamera"
	cam.far = 1000.0   # Far enough to render the procedural sky without clipping

	# Coordinate scale: JSON positions are in centimetres; Godot uses metres
	var CM = 0.01

	var pos    = Vector3(0, 1.5, 5)
	var target = Vector3(0, 1.0, 0)

	var cam_data = {}

	# 1) ThreeJS initial camera (from video_animation first keyframe or top-level)
	if data.has("video_animation") and data["video_animation"] != null:
		var anim = data["video_animation"]
		var keyframes = anim.get("keyframes", [])
		if keyframes.size() > 0:
			var kf0 = keyframes[0]
			var tjs = kf0.get("threejs_camera_data", null)
			if tjs != null and typeof(tjs) == TYPE_DICTIONARY:
				if tjs.has("position"):
					var p = tjs["position"]
					pos = Vector3(float(p["x"]) * CM, float(p["y"]) * CM, float(p["z"]) * CM)
					cam.position = pos
				if tjs.has("lookAt"):
					var la = tjs["lookAt"]
					target = Vector3(float(la["x"]) * CM, float(la["y"]) * CM, float(la["z"]) * CM)
					if cam.position.distance_to(target) > 0.001:
						var diff_v = (target - cam.position).normalized()
						var up_vec = Vector3.UP
						if abs(diff_v.dot(Vector3.UP)) > 0.999:
							up_vec = Vector3.BACK
						cam.look_at(target, up_vec)
				if tjs.has("fov") and tjs["fov"] != null:
					cam.fov = float(tjs["fov"])
				print("Camera Setup (Video KF0 ThreeJS): Pos=", pos, " Target=", target)
				add_child(cam)
				return

	# 2) Explicit threejs_camera block
	var prefer_threejs = data.get("use_threejs", use_threejs)
	if prefer_threejs and data.has("threejs_camera"):
		cam_data = data["threejs_camera"]
		if cam_data.has("position"):
			var p = cam_data["position"]
			pos = Vector3(float(p["x"]) * CM, float(p["y"]) * CM, float(p["z"]) * CM)
		cam.position = pos
		if cam_data.has("rotation"):
			var r = cam_data["rotation"]
			cam.basis = Basis.from_euler(Vector3(float(r["x"]), float(r["y"]), float(r["z"])), EULER_ORDER_XYZ)
			print("Camera Setup (ThreeJS): Pos=", pos, " Rotation=", cam.rotation)
		elif cam_data.has("target"):
			var t = cam_data["target"]
			target = Vector3(float(t["x"]) * CM, float(t["y"]) * CM, float(t["z"]) * CM)
			cam.look_at(target)
			print("Camera Setup (ThreeJS): Pos=", pos, " Target=", target)

	# 3) Blender camera
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
# Fixed World-Space Room Lights
# One OmniLight3D per room, placed at the polygon centroid, just
# below that room's ceiling. Created ONCE — never repositioned.
# ─────────────────────────────────────────────────────────────────
func setup_fixed_fill_lights():
	# Safety: clear any lights from a previous call
	for l in get_tree().get_nodes_in_group("fill_lights"):
		l.queue_free()

	var placed = 0

	# ── 1. Try floor-plan area data (preferred — one light per room) ──
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
				sum_x += float(v.get("x", 0)) * 0.01
				sum_z += float(v.get("y", 0)) * 0.01  # floor-plan Y → Godot Z
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
						hf = float(ch["length"]) * 0.01
					elif typeof(ch) == TYPE_INT or typeof(ch) == TYPE_FLOAT:
						hf = float(ch) * 0.01
					elif typeof(ch) == TYPE_STRING and ch.is_valid_float():
						hf = float(ch) * 0.01
					if hf > 0.5:
						ceil_h = hf
			elif area.has("properties") and typeof(area["properties"]) == TYPE_DICTIONARY:
				var h_prop = area["properties"].get("height", null)
				if h_prop != null:
					var hf = get_dimension_value(h_prop) * 0.01
					if hf > 0.5: ceil_h = hf

			# Light hangs 30 cm below the ceiling — well inside the room
			var light_y = ceil_h - 0.30
			light_y = max(light_y, 1.4)  # never lower than 1.4 m

			# ── Emit radius scaled to room size ──
			#    Estimate room footprint from bounding box of polygon
			var min_x = 1e9; var max_x = -1e9
			var min_z = 1e9; var max_z = -1e9
			for v_id in v_ids:
				var vs = str(v_id)
				if not verts.has(vs): continue
				var v = verts[vs]
				var vx = float(v.get("x", 0)) * 0.01
				var vz = float(v.get("y", 0)) * 0.01
				min_x = min(min_x, vx); max_x = max(max_x, vx)
				min_z = min(min_z, vz); max_z = max(max_z, vz)
			var room_w = max(max_x - min_x, 0.5)
			var room_d = max(max_z - min_z, 0.5)
			var diag   = sqrt(room_w * room_w + room_d * room_d)
			# Range = diagonal + 1 m headroom, clamped between 6 m and 20 m
			var omni_range = clamp(diag + 1.0, 6.0, 20.0)
			# Energy scales gently with room size
			var energy = clamp(0.8 + diag * 0.12, 0.8, 2.0)

			var light = OmniLight3D.new()
			light.name = "RoomLight_" + str(area_id)
			light.position = Vector3(cx, light_y, cz)  # ← fixed world position
			light.light_energy = energy
			light.omni_range = omni_range
			light.shadow_enabled = false   # sun handles primary shadows; fills add brightness
			light.light_color = Color(1.0, 0.97, 0.90)  # warm white
			light.add_to_group("fill_lights")
			add_child(light)
			placed += 1
			print("[VideoRender] Room light '%s' at (%.2f, %.2f, %.2f)  range=%.1f  energy=%.2f" \
				% [area_id, cx, light_y, cz, omni_range, energy])

	# ── 2. Fallback: single centre light derived from mesh AABB ──
	if placed == 0:
		print("[VideoRender] No area data found — placing single fallback fill light at scene centre.")
		var scene_center = Vector3.ZERO
		var light_y = 2.1
		var all_meshes = _get_all_meshes(self)
		if all_meshes.size() > 0:
			var combined = AABB()
			var first_m = true
			for m in all_meshes:
				var world_aabb = m.global_transform * m.get_aabb()
				if first_m: combined = world_aabb; first_m = false
				else: combined = combined.merge(world_aabb)
			scene_center = combined.get_center()
			light_y = combined.position.y + combined.size.y - 0.15
			light_y = max(light_y, scene_center.y + 1.8)

		var light = OmniLight3D.new()
		light.name = "RoomLight_Fallback"
		light.position = Vector3(scene_center.x, light_y, scene_center.z)
		light.light_energy = 1.5
		light.omni_range = 18.0
		light.shadow_enabled = false
		light.light_color = Color(1.0, 0.97, 0.90)
		light.add_to_group("fill_lights")
		add_child(light)
		placed = 1

	print("[VideoRender] %d room light(s) placed — all fixed, shadows will NOT travel with camera." % placed)

# ── Helpers: extract areas / vertices from cached floor-plan data ──
func _get_floor_plan_areas() -> Dictionary:
	var src = _render_data
	if src.has("floor_plan_data"):
		var fp = src["floor_plan_data"]
		if typeof(fp) == TYPE_STRING:
			var j = JSON.new()
			if j.parse(fp) == OK: src = j.data
		elif typeof(fp) == TYPE_DICTIONARY:
			src = fp

	var raw = src.get("areas", null)
	if raw == null: return {}
	if typeof(raw) == TYPE_DICTIONARY: return raw
	# Array format — convert to id-keyed dict
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

	var raw = src.get("vertices", null)
	if raw == null: return {}
	if typeof(raw) == TYPE_DICTIONARY: return raw
	var d = {}
	for v in raw:
		if typeof(v) == TYPE_DICTIONARY and v.has("id"):
			d[str(v["id"])] = v
	return d

# ─────────────────────────────────────────────────────────────────
# Logging — save input JSON copy & render log to sibling directories
# ─────────────────────────────────────────────────────────────────
func save_logs(input_data, output_video_path):
	print("Saving video render logs...")
	
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
	var output_basename = output_video_path.get_file().get_basename()
	
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
	
	# 2. Detailed Log JSON
	var log_data = {}
	log_data["timestamp"] = timestamp
	log_data["input_file_saved"] = input_save_path
	log_data["output_video"] = output_video_path
	log_data["total_frames"] = _total_frames
	log_data["fps"] = _fps
	log_data["duration_seconds"] = float(_total_frames) / float(_fps) if _fps > 0 else 0.0
	
	# Camera Info (final frame position)
	var cam = get_node_or_null("MainCamera")
	var cam_info = {}
	if cam:
		cam_info["position"] = { "x": cam.position.x, "y": cam.position.y, "z": cam.position.z }
		cam_info["rotation_degrees"] = { "x": cam.rotation_degrees.x, "y": cam.rotation_degrees.y, "z": cam.rotation_degrees.z }
		cam_info["fov"] = cam.fov
		var forward = -cam.global_transform.basis.z.normalized()
		cam_info["look_direction_vector"] = { "x": forward.x, "y": forward.y, "z": forward.z }
	log_data["camera_final"] = cam_info
	
	log_data["camera_input_threejs"] = input_data.get("threejs_camera", {})
	log_data["camera_input_blender"] = input_data.get("blender_camera", {})
	
	var log_filename = output_basename + "_render_log.json"
	var log_path = logs_dir.path_join(log_filename)
	
	var log_json_str = JSON.stringify(log_data, "\t")
	var file_log = FileAccess.open(log_path, FileAccess.WRITE)
	if file_log:
		file_log.store_string(log_json_str)
		file_log.close()
		print("Saved video render log to: ", log_path)
	else:
		print("Error saving video render log to ", log_path)
