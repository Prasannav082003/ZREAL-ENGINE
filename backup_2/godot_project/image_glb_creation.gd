extends Node3D

# image_glb_creation.gd
# Handles the construction of the scene: Architecture (Walls, Floors, Ceilings) and Asset Loading.
var _tracked_assets = []
var day_render = true

func build_scene(data):
	if data.has("day_render"):
		day_render = data["day_render"]
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
	
	var sky_mat = ProceduralSkyMaterial.new()
	if day_render:
		sky_mat.sky_top_color = Color(0.35, 0.46, 0.71)
		sky_mat.sky_horizon_color = Color(0.64, 0.65, 0.67)
		sky_mat.ground_bottom_color = Color(0.10, 0.35, 0.08)  # deep grass green
		sky_mat.ground_horizon_color = Color(0.30, 0.52, 0.18)  # lighter meadow green
		env.ambient_light_energy = 1.0
		
		# If no light is provided, default to day light
		if not data.has("directional_light"):
			dir_light.light_energy = 1.0
			dir_light.position = Vector3(10, 20, 10)
			dir_light.look_at(Vector3.ZERO)
	else:
		sky_mat.sky_top_color = Color(0.02, 0.03, 0.05)
		sky_mat.sky_horizon_color = Color(0.05, 0.07, 0.1)
		sky_mat.ground_bottom_color = Color(0.01, 0.01, 0.02)
		sky_mat.ground_horizon_color = Color(0.05, 0.07, 0.1)
		env.ambient_light_energy = 0.1
		
		# If no light is provided, default to moonlight
		if not data.has("directional_light"):
			dir_light.light_energy = 0.1
			dir_light.light_color = Color(0.6, 0.7, 0.9)
			dir_light.position = Vector3(10, 20, 10)
			dir_light.look_at(Vector3.ZERO)

	env.sky.sky_material = sky_mat
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
	
	print("Layer has ", lines.size(), " lines.")
	
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
		
		# ── Structural wall (used for CSG hole cutting, no material needed) ────
		var wall = CSGBox3D.new()
		wall.size = Vector3(length, height, wall_thickness)
		wall.position = wall_pos
		wall.rotation.y = wall_angle
		csg.add_child(wall)
		
		# ── Face panel thickness: 1 cm so it sits snugly on the wall surface ──
		var face_t = max(wall_thickness * 0.05, 0.005)  # 5% of wall, min 5 mm
		
		# ── INNER face panel (visible from inside the room) ───────────────────
		# Offset half-thickness toward the inner side (+Z in local wall space)
		var inner_offset = (wall_thickness / 2.0 - face_t / 2.0)
		var inner_local  = Vector3(0, 0, inner_offset)
		var wall_basis   = Basis(Vector3.UP, wall_angle)
		var inner_world  = wall_pos + wall_basis * inner_local
		
		var inner_panel = CSGBox3D.new()
		inner_panel.size = Vector3(length, height, face_t)
		inner_panel.position = inner_world
		inner_panel.rotation.y = wall_angle
		
		if line.has("inner_properties") and line["inner_properties"].has("material"):
			inner_panel.material = create_material(line["inner_properties"]["material"])
		else:
			var def_mat = StandardMaterial3D.new()
			def_mat.albedo_color = Color(0.9, 0.9, 0.9)
			inner_panel.material = def_mat
		csg.add_child(inner_panel)
		
		# ── OUTER face panel (visible from outside the building) ──────────────
		# Offset half-thickness toward the outer side (-Z in local wall space)
		var outer_offset = -(wall_thickness / 2.0 - face_t / 2.0)
		var outer_local  = Vector3(0, 0, outer_offset)
		var outer_world  = wall_pos + wall_basis * outer_local
		
		var outer_panel = CSGBox3D.new()
		outer_panel.size = Vector3(length, height, face_t)
		outer_panel.position = outer_world
		outer_panel.rotation.y = wall_angle
		
		if line.has("outer_properties") and line["outer_properties"].has("material"):
			outer_panel.material = create_material(line["outer_properties"]["material"])
		elif line.has("inner_properties") and line["inner_properties"].has("material"):
			# Fall back to inner material if no outer defined
			outer_panel.material = create_material(line["inner_properties"]["material"])
		else:
			var def_mat = StandardMaterial3D.new()
			def_mat.albedo_color = Color(0.9, 0.9, 0.9)
			outer_panel.material = def_mat
		csg.add_child(outer_panel)

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
				hole_csg.size = Vector3(h_width, h_height, wall_thickness + 0.2) # Thicker than wall to ensure cut
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
					
					# Asset Material Override (Color and Scale/Repeat)
					if item.has("materials") and typeof(item["materials"]) == TYPE_DICTIONARY:
						var item_mats = item["materials"]
						var meshes = _get_all_meshes(node)
						for m in meshes:
							if not m.mesh: continue
							for i in range(m.mesh.get_surface_count()):
								var mat = m.get_active_material(i)
								if mat and mat is StandardMaterial3D:
									var m_name = mat.resource_name
									if m_name == null or m_name == "": continue
									
									for key in item_mats:
										if key in m_name or m_name in key:
											var mat_opt = item_mats[key]
											if typeof(mat_opt) == TYPE_DICTIONARY:
												var new_mat = mat.duplicate()
												var modified = false
												
												if mat_opt.get("isColorEdited", false):
													var c_str = str(mat_opt.get("color", "ffffff")).strip_edges()
													if c_str.length() >= 3 and not c_str.begins_with("#"):
														c_str = "#" + c_str
													if c_str.is_valid_html_color():
														new_mat.albedo_color = Color(c_str)
													new_mat.albedo_texture = null
													modified = true
													print("Overrode color for material: ", m_name, " in asset: ", item_id, " to color: ", c_str)
												
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
													print("Overrode UV scale for material: ", m_name, " in asset: ", item_id, " to: ", new_mat.uv1_scale)
													
												if modified:
													m.set_surface_override_material(i, new_mat)
					
					print("Loaded asset: ", model_path, " final scale: ", final_scale)
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
	var is_color_edited = mat_data.get("isColorEdited", false)
	
	if not is_color_edited:
		var map_url = ""
		if mat_data.has("mapUrl") and mat_data["mapUrl"] != null and str(mat_data["mapUrl"]) != "":
			map_url = str(mat_data["mapUrl"])
		
		if map_url != "":
			var tex = load_texture_from_path(map_url)
			if tex:
				mat.albedo_texture = tex
				# Enable triplanar to ensure proper scaling on CSG walls/floors
				mat.uv1_triplanar = true
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
	
	# --- UV Scale / Repeat ---
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
