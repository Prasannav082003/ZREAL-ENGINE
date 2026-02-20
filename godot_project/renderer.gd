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
	
	# IMPROVED RENDER QUALITY SETTINGS
	get_viewport().msaa_3d = Viewport.MSAA_4X
	get_viewport().screen_space_aa = Viewport.SCREEN_SPACE_AA_FXAA
	get_viewport().use_taa = true
	get_viewport().use_debanding = true
	get_viewport().mesh_lod_threshold = 0.0 # Force high LOD
	
	# DisplayServer.window_set_size(Vector2i(w, h)) # Not always needed for headless but good for windowed debug

func build_scene(data):
	setup_lighting(data)

	
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
	
	for i in range(32):
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
	var target = Vector3(0, 1.0, 0)
	
	var cam_data = {}
	if data.has("threejs_camera"): 
		cam_data = data["threejs_camera"]
		if cam_data.has("position"):
			var p = cam_data["position"]
			# Directly use ThreeJS position (assume meters/units match)
			pos = Vector3(float(p["x"]), float(p["y"]), float(p["z"]))
		
		cam.position = pos
		
		# Prioritize rotation if available
		if cam_data.has("rotation"):
			var r = cam_data["rotation"]
			# Directly apply rotation (Euler angles in radians)
			cam.rotation.x = float(r["x"])
			cam.rotation.y = float(r["y"])
			cam.rotation.z = float(r["z"])
			print("Camera Setup (ThreeJS): Pos=", pos, " Rotation=", cam.rotation)
		elif cam_data.has("target"):
			var t = cam_data["target"]
			target = Vector3(float(t["x"]), float(t["y"]), float(t["z"]))
			cam.look_at(target)
			print("Camera Setup (ThreeJS): Pos=", pos, " Target=", target)
		
	elif data.has("blender_camera"): 
		cam_data = data["blender_camera"]
		# Blender is Z-up. Godot is Y-up.
		# Blender (x, y, z) -> Godot (x, z, -y)? Or (x, z, y)?
		# Input Blender: x=12.6, y=-8.0, z=0.44.
		# We want Godot Y=0.44 (height). So Blender Z -> Godot Y.
		# We want Godot Z=8.0 (depth). Blender Y is -8.0. So Blender -Y -> Godot Z.
		# We want Godot X=12.6. Blender X=12.6.
		if cam_data.has("location"):
			var loc = cam_data["location"] # List [x, y, z]
			# Blender [x, y, z] -> Godot [x, z, -y]? No.
			# Godot X = Blender X
			# Godot Y = Blender Z
			# Godot Z = -Blender Y
			pos = Vector3(loc[0], loc[2], -loc[1])
			
		cam.position = pos
		
		# Rotation in Blender is standard Euler? 
		# It's easier to use Target if available.
		if data.has("blender_target") and data["blender_target"].has("location"):
			var t_loc = data["blender_target"]["location"]
			target = Vector3(t_loc[0], t_loc[2], -t_loc[1])
			cam.look_at(target)
		else:
			# Fallback rotation
			if cam_data.has("rotation_euler"):
				var rot = cam_data["rotation_euler"]
				cam.rotation = Vector3(rot[0], rot[2], -rot[1]) # Rough mapping
				
		print("Camera Setup (Blender): Pos=", pos, " Target=", target)

	if cam_data.has("fov"): cam.fov = float(cam_data["fov"])
		
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
	print("Building Architecture...")
	# Add a floor
	var floor_mesh = MeshInstance3D.new()
	var plane = PlaneMesh.new()
	plane.size = Vector2(100, 100)
	floor_mesh.mesh = plane
	floor_mesh.create_trimesh_collision()
	add_child(floor_mesh)
	print("Floor added.")

	if data.has("layers"):
		print("Found layers in architecture data.")
		for layer_id in data["layers"]:
			var layer = data["layers"][layer_id]
			print("Processing layer: ", layer_id)
			if layer.has("lines") and layer.has("vertices"):
				_build_layer_geometry(layer)
	elif data.has("lines") and data.has("vertices"):
		print("Found lines/vertices in root data.")
		_build_layer_geometry(data)
	else:
		print("Warning: No lines or vertices found in floor plan data.")
		
func _build_layer_geometry(layer_data):
	var csg = CSGCombiner3D.new()
	csg.use_collision = true
	add_child(csg)
	
	var lines = layer_data["lines"]
	var vertices = layer_data["vertices"]
	var scale_factor = 0.01 # Convert cm to meters
	
	print("Layer has ", lines.keys().size(), " lines.")
	
	# 1. Build Walls
	for line_id in lines:
		var line = lines[line_id]
		# Check if vertices exist
		if not line.has("vertices") or line["vertices"].size() < 2: continue
		
		var v1_id = str(line["vertices"][0])
		var v2_id = str(line["vertices"][1])
		
		# Check if vertices exist in the vertex list
		if not vertices.has(v1_id) or not vertices.has(v2_id): continue
			
		var v1_data = vertices[v1_id]
		var v2_data = vertices[v2_id]
		
		# Swapping Y and Z is common for 2D plans -> 3D
		# Assuming 2D plan is X, Y and height is Z (or Y in Godot)
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
			
		var wall = CSGBox3D.new()
		wall.size = Vector3(length, height, 0.2) # 20cm thickness
		wall.position = Vector3(center.x, height/2.0, center.z)
		# Calculate angle in X-Z plane
		var wall_angle = -atan2(diff.z, diff.x)
		wall.rotation.y = wall_angle
		csg.add_child(wall)
		# print("Added wall: ", line_id, " at ", wall.position)
		
	# 1b. Build Holes (Doors/Windows)
		if line.has("holes"):
			for hole in line["holes"]:
				var h_width = float(hole.get("width", 0)) * scale_factor
				var h_height = float(hole.get("height", 0)) * scale_factor
				var h_alt = float(hole.get("altitude", 0)) * scale_factor
				var h_dist = float(hole.get("offset", 0)) * scale_factor # JSON uses 'offset' often, fallback to 'dist'
				
				# Adjust height/altitude if simplified dictionary
				if hole.has("properties"):
					var props = hole["properties"]
					if props.has("width"): 
						var val = props["width"]
						h_width = (float(val["length"]) if typeof(val) == TYPE_DICTIONARY else float(val)) * scale_factor
					if props.has("height"): 
						var val = props["height"]
						h_height = (float(val["length"]) if typeof(val) == TYPE_DICTIONARY else float(val)) * scale_factor
					if props.has("altitude"): 
						var val = props["altitude"]
						h_alt = (float(val["length"]) if typeof(val) == TYPE_DICTIONARY else float(val)) * scale_factor
					if props.has("offset"): h_dist = float(props["offset"]) * scale_factor

				# Ensure valid dimensions
				if h_width < 0.01 or h_height < 0.01: continue
				
				var hole_csg = CSGBox3D.new()
				hole_csg.operation = CSGBox3D.OPERATION_SUBTRACTION
				hole_csg.size = Vector3(h_width, h_height, 0.4) # Thicker than wall to ensure cut
				
				# Position
				var dir = (p2 - p1).normalized()
				var h_center_pos = p1 + dir * (h_dist + h_width / 2.0)
				h_center_pos.y = h_alt + h_height / 2.0
				
				hole_csg.position = h_center_pos
				hole_csg.rotation.y = wall_angle
				
				csg.add_child(hole_csg)
				print("Added hole at: ", h_center_pos)

		# Apply Wall Material (Prioritize Inner)
		if line.has("inner_properties") and line["inner_properties"].has("material"):
			wall.material = create_material(line["inner_properties"]["material"])
		else:
			var def_mat = StandardMaterial3D.new()
			def_mat.albedo_color = Color(0.9, 0.9, 0.9)
			wall.material = def_mat

	# 2. Build Floors and Ceilings from Areas
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
			
			# Create Floor
			var floor_poly = CSGPolygon3D.new()
			floor_poly.polygon = polygon
			floor_poly.mode = CSGPolygon3D.MODE_DEPTH
			floor_poly.depth = 0.1 # Thin floor
			floor_poly.rotation.x = -PI / 2 # Rotate to lie flat on X-Z plane
			floor_poly.position.y = 0.0 # On ground
			
			# Material for Floor
			if area.has("floor_properties") and area["floor_properties"].has("material"):
				floor_poly.material = create_material(area["floor_properties"]["material"])
			else:
				var floor_mat = StandardMaterial3D.new()
				floor_mat.albedo_color = Color(0.8, 0.8, 0.8) # Light Grey
				floor_poly.material = floor_mat
			
			csg.add_child(floor_poly)
			
			# Create Ceiling
			var ceil_height = 280.0 * scale_factor # Default ceiling height
			
			# Determine height
			if area.has("ceiling_properties"):
				var cp = area["ceiling_properties"]
				if cp.has("height"):
					ceil_height = float(cp["height"]) * scale_factor
			elif area.has("properties") and area["properties"].has("height"):
				var h_prop = area["properties"]["height"]
				if typeof(h_prop) == TYPE_DICTIONARY and h_prop.has("length"):
					ceil_height = float(h_prop["length"]) * scale_factor
				else:
					ceil_height = float(h_prop) * scale_factor
			
			var ceil_poly = CSGPolygon3D.new()
			ceil_poly.polygon = polygon
			ceil_poly.mode = CSGPolygon3D.MODE_DEPTH
			ceil_poly.depth = 0.1
			ceil_poly.rotation.x = PI / 2 # Flip for ceiling? Or just position high
			# Rotating -PI/2 makes normal point UP. We want ceiling normal pointing DOWN?
			# Actually standard floor points up. Ceiling should point down. 
			# Rotation PI/2 (90 deg) makes it face down?
			# Let's stick to -PI/2 (facing up) for now but position it. 
			# Most renderers filter backfaces. If inside, we want to see it.
			# If Cull Mode is disabled, it's fine.
			ceil_poly.rotation.x = -PI / 2 
			ceil_poly.position.y = ceil_height
			
			# Ceiling Material
			if area.has("ceiling_properties") and area["ceiling_properties"].has("material"):
				var c_mat = create_material(area["ceiling_properties"]["material"])
				# c_mat.cull_mode = BaseMaterial3D.CULL_DISABLED # Ensure visible from below
				ceil_poly.material = c_mat
			else:
				var ceil_mat = StandardMaterial3D.new()
				ceil_mat.albedo_color = Color(0.95, 0.95, 0.95)
				ceil_poly.material = ceil_mat
			
			csg.add_child(ceil_poly)
			
			print("Added floor/ceiling for area: ", area_id, " Height: ", ceil_height)

func create_material(mat_data):
	var mat = StandardMaterial3D.new()
	
	if mat_data.has("color"):
		var c_str = str(mat_data["color"])
		if c_str.begins_with("#"):
			mat.albedo_color = Color(c_str)
	
	# Attempt to load textures
	# Note: This requires textures to be local paths or handled by Godot's resource loader capable of http (not default sync)
	# Assuming main.py has possibly downloaded these or we use the URL references if they are local file://
	
	if mat_data.has("mapUrl") and mat_data["mapUrl"] != null:
		var tex = load_texture_from_path(str(mat_data["mapUrl"]))
		if tex: mat.albedo_texture = tex
			
	if mat_data.has("normalUrl") and mat_data["normalUrl"] != null:
		var tex = load_texture_from_path(str(mat_data["normalUrl"]))
		if tex: 
			mat.normal_enabled = true
			mat.normal_texture = tex
			
	if mat_data.has("roughnessUrl") and mat_data["roughnessUrl"] != null:
		var tex = load_texture_from_path(str(mat_data["roughnessUrl"]))
		if tex: 
			mat.roughness_texture = tex
			mat.roughness_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_GREEN # Standard for packing? Or grayscale.
			
	if mat_data.has("repeat"):
		var r = mat_data["repeat"]
		if typeof(r) == TYPE_ARRAY and r.size() >= 2:
			mat.uv1_scale = Vector3(float(r[0]), float(r[1]), 1.0)
			
	return mat

func load_texture_from_path(path):
	if path == "" or path == "null" or path == "None": return null
	
	# If path is HTTP, we can't load synchronously easily without an HTTPRequest node yielding.
	# For this implementation, we check if it maps to a local file (downloaded by python script)
	# or if it is a local path.
	
	var local_path = path
	# Heuristic: Check if filename exists in specific asset folders?
	# Or assume path is absolute/relative.
	
	if FileAccess.file_exists(local_path):
		return load_image_texture(local_path)
		
	# Check if mapped from URL -> local filenames usually stored in 'textures' folder?
	# E.g. https://.../WoodFloor007_4K-PNG_LR_BaseColor.jpg -> ./textures/WoodFloor007_4K-PNG_LR_BaseColor.jpg
	var filename = path.get_file()
	var try_paths = [
		"res://textures/" + filename,
		"res://assets/" + filename,
		"./textures/" + filename,
		"textures/" + filename
	]
	
	for p in try_paths:
		if FileAccess.file_exists(p):
			return load_image_texture(p)
			
	print("Texture not found (skipped): ", path)
	return null

func load_image_texture(path):
	var img = Image.load_from_file(path)
	if img:
		return ImageTexture.create_from_image(img)
	return null
