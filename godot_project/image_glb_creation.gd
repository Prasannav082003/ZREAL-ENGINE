extends Node3D

# image_glb_creation.gd
# Handles the construction of the scene: Architecture (Walls, Floors, Ceilings) and Asset Loading.
var _tracked_assets = []

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
	# setup_camera(data) - Moved to render_image.gd

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

func parse_vec3(d):
	if typeof(d) == TYPE_DICTIONARY:
		return Vector3(d.get("x", 0), d.get("y", 0), d.get("z", 0))
	return Vector3.ZERO

func build_architecture(data):
	print("Building Architecture...")

	if data.has("layers"):
		print("Found layers in architecture data.")
		for layer_id in data["layers"]:
			var layer = data["layers"][layer_id]
			print("Processing layer: ", layer_id)
			var layer_alt = 0.0
			if layer.has("altitude"):
				var alt_val = layer["altitude"]
				if typeof(alt_val) == TYPE_DICTIONARY and alt_val.has("length"):
					layer_alt = float(alt_val["length"]) * 0.01
				else:
					layer_alt = float(alt_val) * 0.01
					
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
		wall.position = Vector3(center.x, layer_altitude + height/2.0, center.z)
		# Calculate angle in X-Z plane
		var wall_angle = -atan2(diff.z, diff.x)
		wall.rotation.y = wall_angle
		csg.add_child(wall)
		
		# Apply Wall Material (Prioritize Inner)
		if line.has("inner_properties") and line["inner_properties"].has("material"):
			var mat = create_material(line["inner_properties"]["material"])
			wall.material = mat
		else:
			var def_mat = StandardMaterial3D.new()
			def_mat.albedo_color = Color(0.9, 0.9, 0.9)
			wall.material = def_mat

		# 1b. Build Holes (Doors/Windows) AND INSTANTIATE ASSETS
		if line.has("holes"):
			for hole_id in line["holes"]:
				var hid = str(hole_id)
				if not all_holes.has(hid): continue
				var hole = all_holes[hid]
				
				var h_width = float(hole.get("width", 0)) * scale_factor
				var h_height = float(hole.get("height", 0)) * scale_factor
				var h_alt = float(hole.get("altitude", 0)) * scale_factor
				
				# Get properties if available (override)
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

				# Determine offset (normalized 0-1)
				var offset_ratio = 0.5
				if hole.has("offset"):
					offset_ratio = float(hole["offset"])
				elif hole.has("properties") and hole["properties"].has("offset"):
					offset_ratio = float(hole["properties"]["offset"])
					
				# Calculate Position
				# Linear interpolation between p1 and p2
				var hole_pos_xz = p1.lerp(p2, offset_ratio)
				
				var h_center_pos = hole_pos_xz
				h_center_pos.y = layer_altitude + h_alt + h_height / 2.0
				
				# Ensure valid dimensions
				if h_width < 0.01 or h_height < 0.01: continue
				
				# Create Hole Cutter (Subtractive CSG)
				var hole_csg = CSGBox3D.new()
				hole_csg.operation = CSGBox3D.OPERATION_SUBTRACTION
				hole_csg.size = Vector3(h_width, h_height, 0.4) # Thicker than wall to ensure cut
				hole_csg.position = h_center_pos
				hole_csg.rotation.y = wall_angle
				csg.add_child(hole_csg)
				print("Added hole cutter at: ", h_center_pos)
				
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
									asset_node.position = h_center_pos
									# Reset Y to bottom of hole for standard assets?
									# Usually door/window assets origin is at bottom center.
									# Hole cutter uses center. 
									# h_center_pos.y is center of hole.
									# asset_node.position.y should be h_center_pos.y - h_height/2.0 ?
									# Let's try adjusting only if it looks wrong. Most arch assets are bottom-pivot.
									asset_node.position.y = layer_altitude + h_alt
									
									asset_node.rotation.y = wall_angle
									# Check for flip
									if hole.get("flipX", false):
										asset_node.scale.x = -1
									if hole.get("flipZ", false):
										asset_node.scale.z = -1
										
									# Re-scale asset to fit hole? 
									# Arch assets usually come in correct size or we trust the scale.
									# If we need to stretch:
									# var a_aabb = asset_node.get_aabb() # Requires MeshInstance logic
									
									print("Loaded hole asset: ", model_path)
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
			var floor_depth = 0.1
			if area.has("floor_properties") and area["floor_properties"].has("thickness"):
				floor_depth = float(area["floor_properties"]["thickness"]) * scale_factor
			
			var floor_poly = CSGPolygon3D.new()
			floor_poly.polygon = polygon
			floor_poly.mode = CSGPolygon3D.MODE_DEPTH
			floor_poly.depth = floor_depth
			floor_poly.rotation.x = PI / 2 # Rotate to lie flat on X-Z plane, align +Y(2D) to +Z(3D)
			floor_poly.position.y = layer_altitude - floor_depth # Floor is below altitude (surface at 0)
			
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
			
			var ceil_depth = 0.1
			if area.has("ceiling_properties") and area["ceiling_properties"].has("thickness"):
				ceil_depth = float(area["ceiling_properties"]["thickness"]) * scale_factor

			ceil_poly.depth = ceil_depth
			ceil_poly.rotation.x = PI / 2 
			ceil_poly.position.y = layer_altitude + ceil_height
			
			# Ceiling Material
			if area.has("ceiling_properties") and area["ceiling_properties"].has("material"):
				var c_mat = create_material(area["ceiling_properties"]["material"])
				ceil_poly.material = c_mat
			else:
				var ceil_mat = StandardMaterial3D.new()
				ceil_mat.albedo_color = Color(0.95, 0.95, 0.95)
				ceil_poly.material = ceil_mat
			
			csg.add_child(ceil_poly)
			
			print("Added floor/ceiling for area: ", area_id, " Height: ", ceil_height)

func load_assets(data):
	if data.has("layers"):
		var layers_dict = data["layers"]
		for layer_id in layers_dict:
			var layer = layers_dict[layer_id]
			var layer_alt = 0.0
			
			if layer.has("altitude"):
				var alt_val = layer["altitude"]
				if typeof(alt_val) == TYPE_DICTIONARY and alt_val.has("length"):
					layer_alt = float(alt_val["length"]) * 0.01
				else:
					layer_alt = float(alt_val) * 0.01

			if layer.has("items"):
				_load_layer_items(layer["items"], layer_alt)
			elif layer.has("assets"):
				_load_layer_items(layer["assets"], layer_alt)
	elif data.has("assets"):
		_load_layer_items(data["assets"], 0.0)
	elif data.has("items"):
		_load_layer_items(data["items"], 0.0)

func _load_layer_items(items, layer_altitude):
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
					
					var sx = 1.0; var sy = 1.0; var sz = 1.0
					if item.has("scaleX"): sx = float(item["scaleX"])
					if item.has("scaleY"): sy = float(item["scaleY"])
					if item.has("scaleZ"): sz = float(item["scaleZ"])
					
					node.scale = Vector3(sx, sz, sy)
						
					print("Loaded asset: ", model_path)
			else:
				print("Failed to load GLTF: ", model_path, " Error: ", error)
		
		if model_path != "":
			_tracked_assets.append({
				"type": "item_asset",
				"id": item.get("id", "unknown"),
				"name": item.get("name", "unknown"),
				"path": model_path,
				"position": item.get("x", 0), # Store original coords
			})
		else:
			pass

func create_material(mat_data):
	var mat = StandardMaterial3D.new()
	
	# Always disable backface culling
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	
	# --- Parse Color ---
	var base_color = Color(0.9, 0.9, 0.9)
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
	var map_url = ""
	if mat_data.has("mapUrl") and mat_data["mapUrl"] != null and str(mat_data["mapUrl"]) != "":
		map_url = str(mat_data["mapUrl"])
	
	if map_url != "":
		var tex = load_texture_from_path(map_url)
		if tex:
			mat.albedo_texture = tex
			# print("  Material Texture Applied: ", map_url.get_file())
		else:
			# print("  Material Texture Missing: ", map_url.get_file(), " (Using color ", base_color, ")")
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
	
	# --- UV Repeat ---
	if mat_data.has("repeat"):
		var r = mat_data["repeat"]
		if typeof(r) == TYPE_ARRAY and r.size() >= 2:
			var su = float(r[0]) if float(r[0]) > 0 else 1.0
			var sv = float(r[1]) if float(r[1]) > 0 else 1.0
			mat.uv1_scale = Vector3(su, sv, 1.0)
		
	return mat

func _resolve_local_texture(url: String) -> String:
	# Try to find a locally downloaded texture corresponding to a URL.
	if url == "" or url == "null": return ""
	
	# If already a local path and exists - use it directly
	if FileAccess.file_exists(url): return url
	
	# Extract filename from URL
	var filename = url.get_file()
	if filename == "": return ""
	
	# Search in common texture directories
	var search_dirs = [
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
	
	# Direct file check
	if FileAccess.file_exists(path):
		return load_image_texture(path)
	
	# Resolve via helper (URL -> local filename search)
	var local = _resolve_local_texture(path)
	if local != "":
		return load_image_texture(local)
		
	# If it's an HTTP URL that isn't downloaded yet, skip silently (color fallback is already set)
	if path.begins_with("http"):
		return null
		
	print("Texture not found (skipped): ", path)
	return null

func load_image_texture(path):
	var img = Image.load_from_file(path)
	if img:
		return ImageTexture.create_from_image(img)
	return null
