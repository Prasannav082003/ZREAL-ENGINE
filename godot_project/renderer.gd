extends Node3D

# Renderer for 4k_Unreal_Engine_v1 - Godot Migration

func _ready():
	print("Godot Renderer Started")
	
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
	build_scene(data)
	
	if data.has("video_animation"):
		call_deferred("render_video", data, output_path)
	else:
		call_deferred("render_image", data, output_path)

func set_resolution(data):
	var w = 1920
	var h = 1080
	
	if data.has("render_quality"):
		var quality = str(data["render_quality"]).to_upper()
		if quality == "4K":
			w = 3840
			h = 2160
		elif quality == "2K":
			w = 2560
			h = 1440
		elif quality == "1080P" or quality == "FHD":
			w = 1920
			h = 1080
	
	if data.has("width"): w = int(data["width"])
	if data.has("height"): h = int(data["height"])
			
	print("Setting Resolution to: ", w, "x", h)
	get_viewport().size = Vector2i(w, h)
	# DisplayServer.window_set_size(Vector2i(w, h)) # Not always needed for headless but good for windowed debug

func build_scene(data):
	setup_lighting(data)

	
	var geom_data = data
	if data.has("floor_plan_data"):
		geom_data = data["floor_plan_data"]
		
	build_architecture(geom_data)
	load_assets(geom_data) 
	setup_camera(data)

func load_assets(data):
	if data.has("layers"):
		for layer_id in data["layers"]:
			var layer = data["layers"][layer_id]
			if layer.has("items"):
				_load_layer_items(layer["items"])
			elif layer.has("assets"):
				_load_layer_items(layer["assets"])
	elif data.has("assets"):
		_load_layer_items(data["assets"])
	elif data.has("items"):
		_load_layer_items(data["items"])

func _load_layer_items(items):
	for item_id in items:
		var item = items[item_id]
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
			
		if model_path != "" and FileAccess.file_exists(model_path):
			var glTF = GLTFDocument.new()
			var glTFState = GLTFState.new()
			var error = glTF.append_from_file(model_path, glTFState)
			if error == OK:
				var node = glTF.generate_scene(glTFState)
				if node:
					add_child(node)
					
					# Transform
					# Default Transform
					var px = float(item.get("x", 0)) * 0.01
					var py = float(item.get("y", 0)) * 0.01 # This is Z in 3D
					var pz = 0.0
					
					# Check altitude (often in properties in complex JSON)
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
						
					node.position = Vector3(px, pz, py)
					
					var rot = 0.0
					if item.has("rotation"):
						rot = float(item["rotation"])
					elif item.has("properties") and item["properties"].has("rotation"):
						rot = float(item["properties"]["rotation"])
						
					# Godot uses Radians. JSON usually Deg or Rad? 
					# Input JSON has rotation: -1.166... which looks like Radians. 
					# BUT previous code used deg_to_rad. 
					# If the value is small (< 7), it might be Radians.
					# Let's assume Radians if it looks small, or stick to what logic implies.
					# Input JSON examples: -1.16, -89.11, 178.8. 
					# 178 implies Degrees. -1.16 implies Radians? 
					# 90 degrees = 1.57 rad. 
					# -89.11 is definitely Degrees.
					# -1.16 is ambiguous (could be -66 deg).
					# Standardize on Degrees usually.
					# BUT `rotation` in properties usually comes from frontend in Radians?
					# Let's assume DEGREES and convert to Radians.
					node.rotation.y = -deg_to_rad(rot)
					
					# Scale
					var sx = 1.0; var sy = 1.0; var sz = 1.0
					if item.has("scaleX"): sx = float(item["scaleX"])
					if item.has("scaleY"): sy = float(item["scaleY"])
					if item.has("scaleZ"): sz = float(item["scaleZ"])
					
					node.scale = Vector3(sx, sz, sy)
						
					print("Loaded asset: ", model_path)
			else:
				print("Failed to load GLTF: ", model_path, " Error: ", error)
		else:
			pass

func render_image(data, output_path):
	print("Rendering Single Image...")
	await get_tree().process_frame
	await get_tree().process_frame
	
	# Force a draw
	RenderingServer.force_draw()
	
	for i in range(10):
		await get_tree().process_frame
		
	await RenderingServer.frame_post_draw
	
	var vp = get_viewport()
	print("Viewport Size: ", vp.size)
	
	var tex = vp.get_texture()
	if tex:
		var img = tex.get_image()
		if img:
			if img.is_empty():
				print("Error: Image is empty.")
			else:
				img.save_png(output_path)
				print("Image saved to: ", output_path)
		else:
			print("Error: Viewport texture has no image data.")
	else:
		print("Error: Viewport texture is null.")
	
	get_tree().quit(0)

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
	
	for frame in range(total_frames):
		var t = float(frame) / float(total_frames - 1) if total_frames > 1 else 0.0
		cam.position = start_pos.lerp(end_pos, t)
		
		await get_tree().process_frame
		
		var img = get_viewport().get_texture().get_image()
		var frame_filename = "%s_%04d.png" % [base_filename, frame]
		img.save_png(frame_filename)
		
		if frame % 10 == 0:
			print("Rendered frame ", frame, "/", total_frames)
			
	get_tree().quit(0)

func parse_vec3(d):
	if typeof(d) == TYPE_DICTIONARY:
		return Vector3(d.get("x", 0), d.get("y", 0), d.get("z", 0))
	return Vector3.ZERO

func setup_camera(data):
	var cam = Camera3D.new()
	cam.name = "MainCamera"
	var pos = Vector3(0, 1.5, 5)
	
	var cam_data = {}
	if data.has("threejs_camera"): cam_data = data["threejs_camera"]
	elif data.has("blender_camera"): cam_data = data["blender_camera"]
	
	if cam_data.has("position"): pos = parse_vec3(cam_data["position"])
	cam.position = pos
	
	if cam_data.has("target"): cam.look_at(parse_vec3(cam_data["target"]))
	elif cam_data.has("rotation"): cam.rotation = parse_vec3(cam_data["rotation"])
		
	if cam_data.has("fov"): cam.fov = cam_data["fov"]
		
	add_child(cam)

func setup_lighting(data):
	var dir_light = DirectionalLight3D.new()
	dir_light.name = "Sun"
	dir_light.shadow_enabled = true
	
	if data.has("directional_light"):
		var l = data["directional_light"]
		dir_light.light_energy = l.get("intensity", 1.0)
		if l.has("position"):
			dir_light.position = parse_vec3(l["position"])
			dir_light.look_at(Vector3.ZERO)
			
	add_child(dir_light)
	
	var env = Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = Sky.new()
	env.sky.sky_material = ProceduralSkyMaterial.new()
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	
	var world_env = WorldEnvironment.new()
	world_env.environment = env
	add_child(world_env)

func build_architecture(data):
	# Add a floor
	var floor_mesh = MeshInstance3D.new()
	var plane = PlaneMesh.new()
	plane.size = Vector2(100, 100)
	floor_mesh.mesh = plane
	floor_mesh.create_trimesh_collision()
	add_child(floor_mesh)

	if not data.has("lines") or not data.has("vertices"): 
		print("Warning: No lines or vertices found in floor plan data.")
		return
	
	var csg = CSGCombiner3D.new()
	add_child(csg)
	
	var lines = data["lines"]
	var vertices = data["vertices"]
	var scale_factor = 0.01 # Convert cm to meters
	
	# Handle both dictionary and list formats if necessary, but assuming dict based on previous code
	for line_id in lines:
		var line = lines[line_id]
		# Check if vertices exist
		if not line.has("vertices") or line["vertices"].size() < 2: continue
		
		var v1_id = str(line["vertices"][0])
		var v2_id = str(line["vertices"][1])
		
		if not vertices.has(v1_id) or not vertices.has(v2_id): continue
			
		var v1_data = vertices[v1_id]
		var v2_data = vertices[v2_id]
		
		# Swapping Y and Z is common for 2D plans -> 3D
		# Assuming 2D plan is X, Y and height is Z (or Y in Godot)
		var p1 = Vector3(float(v1_data["x"]), 0, float(v1_data["y"])) * scale_factor
		var p2 = Vector3(float(v2_data["x"]), 0, float(v2_data["y"])) * scale_factor
		
		var diff = p2 - p1
		var length = diff.length()
		var center = (p1 + p2) / 2.0
		var height = 240.0 * scale_factor # Default height
		
		if line.has("properties") and line["properties"].has("height"):
			height = float(line["properties"]["height"]) * scale_factor
			
		var wall = CSGBox3D.new()
		wall.size = Vector3(length, height, 0.2) # 20cm thickness
		wall.position = Vector3(center.x, height/2.0, center.z)
		# Calculate angle in X-Z plane
		wall.rotation.y = -atan2(diff.z, diff.x)
		csg.add_child(wall)
