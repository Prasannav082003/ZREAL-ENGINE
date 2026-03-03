extends Node3D

# video_glb_creation.gd
# Scene builder for VIDEO rendering.
# Mirrors image_glb_creation.gd logic but handles the VIDEO JSON format:
#   - floor_plan_data has lines/vertices/holes/areas/items as ARRAYS (each with an "id" field)
#   - Camera is driven per-frame by video_render.gd via threejs_camera_data
#   - All floor-plan coordinates are in centimetres (multiply by 0.01 for Godot metres)

var _tracked_assets = []
var day_render = true

# ─────────────────────────────────────────────────────────────────
# Entry-point (called by video_render.gd)
# ─────────────────────────────────────────────────────────────────
func build_scene(data):
	if data.has("day_render"):
		day_render = data["day_render"]
	setup_lighting(data)

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

	build_architecture(geom_data)
	load_assets(geom_data)

# ─────────────────────────────────────────────────────────────────
# Lighting  (mirrors image_glb_creation.gd — ProceduralSkyMaterial, NO SunSky)
# ─────────────────────────────────────────────────────────────────
func setup_lighting(data):
	var dir_light = DirectionalLight3D.new()
	dir_light.name = "Sun"
	dir_light.shadow_enabled = true
	dir_light.shadow_bias = 0.05

	# Set a good default angle FIRST so the light always illuminates from above.
	# Any per-data overrides below will replace this if needed.
	dir_light.rotation_degrees = Vector3(-45.0, 45.0, 0.0)  # upper-right, mid-morning look

	if data.has("directional_light"):
		var l = data["directional_light"]
		dir_light.light_energy = l.get("intensity", 1.0)
		if l.has("position"):
			var lp = parse_vec3(l["position"])
			if lp.length() > 0.001:
				# Override the default angle with the caller-supplied position
				dir_light.look_at_from_position(lp, Vector3.ZERO)
			# else: keep the default rotation_degrees set above

	add_child(dir_light)

	var env = Environment.new()
	env.background_mode = Environment.BG_SKY
	env.sky = Sky.new()

	# NOTE: SunSky (PhysicalSkyMaterial) is NOT used here.
	# Both image and video renders use ProceduralSkyMaterial for full day/night control.
	var sky_mat = ProceduralSkyMaterial.new()
	if day_render:
		print("[VideoGLB] setup_lighting: DAY render")
		sky_mat.sky_top_color         = Color(0.35, 0.46, 0.71)  # deep blue zenith
		sky_mat.sky_horizon_color     = Color(0.64, 0.65, 0.67)  # light grey horizon
		# Ground colours: rich grass green so the ground looks like a natural lawn.
		sky_mat.ground_bottom_color   = Color(0.10, 0.35, 0.08)  # deep grass green
		sky_mat.ground_horizon_color  = Color(0.30, 0.52, 0.18)  # lighter meadow green
		sky_mat.sun_angle_max         = 30.0
		sky_mat.sky_energy_multiplier = 1.0
		env.ambient_light_energy      = 1.0

		# If no external light is provided, default to bright day light from upper-right
		if not data.has("directional_light"):
			dir_light.light_energy = 1.0
			# position + look_at also works; rotation_degrees already set above as fallback
			dir_light.position = Vector3(10, 20, 10)
			dir_light.look_at(Vector3.ZERO)
	else:
		print("[VideoGLB] setup_lighting: NIGHT render")
		sky_mat.sky_top_color         = Color(0.02, 0.03, 0.05)
		sky_mat.sky_horizon_color     = Color(0.05, 0.07, 0.1)
		sky_mat.ground_bottom_color   = Color(0.01, 0.01, 0.02)
		sky_mat.ground_horizon_color  = Color(0.05, 0.07, 0.1)
		sky_mat.sky_energy_multiplier = 0.15
		env.ambient_light_energy      = 0.1

		# If no external light is provided, default to dim moonlight
		if not data.has("directional_light"):
			dir_light.light_energy = 0.1
			dir_light.light_color  = Color(0.6, 0.7, 0.9)
			dir_light.position = Vector3(10, 20, 10)
			dir_light.look_at(Vector3.ZERO)

	env.sky.sky_material     = sky_mat
	env.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	env.tonemap_mode         = Environment.TONE_MAPPER_FILMIC
	env.tonemap_exposure     = 1.0

	var world_env = WorldEnvironment.new()
	world_env.environment = env
	add_child(world_env)

# ─────────────────────────────────────────────────────────────────
# Parse vec3 helper
# ─────────────────────────────────────────────────────────────────
func parse_vec3(d):
	if typeof(d) == TYPE_DICTIONARY:
		return Vector3(d.get("x", 0), d.get("y", 0), d.get("z", 0))
	return Vector3.ZERO

# ─────────────────────────────────────────────────────────────────
# Architecture — converts arrays to lookup dicts then calls shared builder
# ─────────────────────────────────────────────────────────────────
func build_architecture(data):
	print("[VideoGLB] Building Architecture...")

	# The video JSON has lines/vertices/holes/areas/items as FLAT ARRAYS
	# each element has an "id" field. We convert them to dicts for the builder.
	var layer_data = _arrays_to_dicts(data)

	if not layer_data.has("lines") or not layer_data.has("vertices"):
		print("[VideoGLB] WARNING: No lines/vertices found in floor_plan_data.")
		return

	print("[VideoGLB] Lines: ", layer_data["lines"].size(),
		"  Vertices: ", layer_data["vertices"].size(),
		"  Holes: ", layer_data.get("holes", {}).size(),
		"  Areas: ", layer_data.get("areas", {}).size())

	_build_layer_geometry(layer_data, 0.0)

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
		out["items"] = raw_i   # keep as array — _load_layer_items handles both
	elif typeof(raw_i) == TYPE_DICTIONARY:
		out["items"] = raw_i

	return out

# ─────────────────────────────────────────────────────────────────
# Core geometry builder — identical logic to image_glb_creation.gd
# ─────────────────────────────────────────────────────────────────
func _build_layer_geometry(layer_data, layer_altitude):
	var csg = CSGCombiner3D.new()
	csg.use_collision = true
	add_child(csg)

	var lines = layer_data["lines"]
	var vertices = layer_data["vertices"]
	var scale_factor = 0.01  # cm → metres

	var all_holes = {}
	if layer_data.has("holes"):
		all_holes = layer_data["holes"]

	print("[VideoGLB] Layer has ", lines.size(), " lines.")

	# 1. Build Walls
	for line_id in lines:
		var line = lines[line_id]
		if not line.has("vertices") or line["vertices"].size() < 2:
			continue

		var v1_id = str(line["vertices"][0])
		var v2_id = str(line["vertices"][1])

		if not vertices.has(v1_id) or not vertices.has(v2_id):
			continue

		var v1_data = vertices[v1_id]
		var v2_data = vertices[v2_id]

		var p1 = Vector3(float(v1_data["x"]), 0.0, float(v1_data["y"])) * scale_factor
		var p2 = Vector3(float(v2_data["x"]), 0.0, float(v2_data["y"])) * scale_factor

		var diff = p2 - p1
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

		# ── Extend wall at both ends to fill 90° corner gaps ───────────────────
		# Adds wall_thickness/2 at each end so CSG union merges corners cleanly.
		var corner_length = length + wall_thickness

		# ── Structural wall in CSGCombiner gets OUTER material ──────────────────
		# From outside the building, the exterior face of this wall is visible.
		# Doors/windows are cut through it by SUBTRACTION CSGBox3D nodes below.
		var wall = CSGBox3D.new()
		wall.size = Vector3(corner_length, height, wall_thickness)
		wall.position = wall_pos
		wall.rotation.y = wall_angle

		if line.has("outer_properties") and line["outer_properties"].has("material"):
			wall.material = create_material(line["outer_properties"]["material"])
		elif line.has("inner_properties") and line["inner_properties"].has("material"):
			wall.material = create_material(line["inner_properties"]["material"])
		else:
			var def_mat = StandardMaterial3D.new()
			def_mat.albedo_color = Color(0.9, 0.9, 0.9)
			def_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
			wall.material = def_mat
		csg.add_child(wall)

		# ── Inner face panel as MeshInstance3D at scene root ────────────────────
		# NOT a CSGBox3D child of the combiner — bypasses CSG so faces survive.
		# Shows inner_properties material (visible from inside the room).
		var inner_mat_data = null
		if line.has("inner_properties") and line["inner_properties"].has("material"):
			inner_mat_data = line["inner_properties"]["material"]
		elif line.has("outer_properties") and line["outer_properties"].has("material"):
			inner_mat_data = line["outer_properties"]["material"]

		if inner_mat_data != null:
			var face_t    = 0.001  # 1 mm overlay — just beats z-fighting
			var wall_basis = Basis(Vector3.UP, wall_angle)
			var inner_offset = wall_thickness / 2.0 + face_t / 2.0
			var inner_world  = wall_pos + wall_basis * Vector3(0, 0, inner_offset)

			var inner_mesh = MeshInstance3D.new()
			var bm = BoxMesh.new()
			bm.size = Vector3(corner_length, height, face_t)
			inner_mesh.mesh = bm
			inner_mesh.position = inner_world
			inner_mesh.rotation.y = wall_angle
			inner_mesh.material_override = create_material(inner_mat_data)
			add_child(inner_mesh)  # scene root, NOT csg combiner

		# 1b. Holes (Doors / Windows)
		if line.has("holes"):
			for hole_id in line["holes"]:
				var hid = str(hole_id)
				if not all_holes.has(hid):
					continue
				var hole = all_holes[hid]

				var h_width = get_dimension_value(hole.get("width", 0)) * scale_factor
				var h_height = get_dimension_value(hole.get("height", 0)) * scale_factor
				var h_alt = get_dimension_value(hole.get("altitude", 0)) * scale_factor

				if hole.has("properties"):
					var props = hole["properties"]
					if props.has("width"):
						h_width = get_dimension_value(props["width"]) * scale_factor
					if props.has("height"):
						h_height = get_dimension_value(props["height"]) * scale_factor
					if props.has("altitude"):
						h_alt = get_dimension_value(props["altitude"]) * scale_factor

				var offset_ratio = 0.5
				if hole.has("offset"):
					offset_ratio = float(hole["offset"])
				elif hole.has("properties") and hole["properties"].has("offset"):
					offset_ratio = float(hole["properties"]["offset"])

				var hole_pos_xz = p1.lerp(p2, offset_ratio)
				var h_center_pos = hole_pos_xz
				h_center_pos.y = layer_altitude + h_alt + h_height / 2.0

				if h_width < 0.01 or h_height < 0.01:
					continue

				# CSG hole cutter
				var hole_csg = CSGBox3D.new()
				hole_csg.operation = CSGBox3D.OPERATION_SUBTRACTION
				hole_csg.size = Vector3(h_width, h_height, wall_thickness + 0.2)
				hole_csg.position = h_center_pos
				hole_csg.rotation.y = wall_angle
				csg.add_child(hole_csg)
				print("[VideoGLB] Added hole cutter at: ", h_center_pos)

				# Asset for hole
				if hole.has("asset_urls"):
					var urls = hole["asset_urls"]
					var model_path = ""
					if urls.has("GLB_File_URL") and urls["GLB_File_URL"] != null:
						model_path = str(urls["GLB_File_URL"])
					elif urls.has("glb_Url") and urls["glb_Url"] != null:
						model_path = str(urls["glb_Url"])

					if model_path != "":
						if not FileAccess.file_exists(model_path):
							if FileAccess.file_exists("res://" + model_path):
								model_path = "res://" + model_path
							elif FileAccess.file_exists("./" + model_path):
								model_path = "./" + model_path

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

									var flip_x = 1.0
									var flip_z = 1.0
									if hole.get("flipX", false):
										flip_x = -1.0
									if hole.get("flipZ", false):
										flip_z = -1.0

									asset_node.scale = Vector3(scale_x * flip_x, scale_y, scale_z * flip_z)
									print("[VideoGLB] Loaded hole asset: ", model_path, " scale: ", asset_node.scale)
							else:
								print("[VideoGLB] Failed to load hole GLTF: ", model_path, " Error: ", error)

						if model_path != "":
							_tracked_assets.append({
								"type": "hole_asset",
								"id": hole.get("id", "unknown"),
								"name": hole.get("name", "unknown"),
								"path": model_path
							})

	# 2. Build Floors and Ceilings from Areas
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
					var x = float(v["x"]) * scale_factor
					var y = float(v["y"]) * scale_factor
					polygon.append(Vector2(x, y))

			if polygon.size() < 3:
				continue

			# Floor — thickness=0 is common in the video JSON, enforce minimum
			var floor_depth = 0.1
			if area.has("floor_properties") and typeof(area["floor_properties"]) == TYPE_DICTIONARY:
				var fp2 = area["floor_properties"]
				if fp2.has("thickness"):
					var t = float(fp2["thickness"]) * scale_factor
					if t > 0.001:
						floor_depth = t
			if floor_depth < 0.05:
				floor_depth = 0.05

			var floor_poly = CSGPolygon3D.new()
			floor_poly.polygon = polygon
			floor_poly.mode = CSGPolygon3D.MODE_DEPTH
			floor_poly.depth = floor_depth
			floor_poly.rotation.x = PI / 2
			floor_poly.position.y = layer_altitude - floor_depth

			if area.has("floor_properties") and typeof(area["floor_properties"]) == TYPE_DICTIONARY and area["floor_properties"].has("material") and typeof(area["floor_properties"]["material"]) == TYPE_DICTIONARY:
				floor_poly.material = create_material(area["floor_properties"]["material"])
			else:
				var floor_mat = StandardMaterial3D.new()
				floor_mat.albedo_color = Color(0.8, 0.8, 0.8)
				floor_poly.material = floor_mat

			csg.add_child(floor_poly)

			# Ceiling — height may be raw number (cm) OR dict {length: N}
			var ceil_height = 280.0 * scale_factor  # Default

			if area.has("ceiling_properties") and typeof(area["ceiling_properties"]) == TYPE_DICTIONARY:
				var cp = area["ceiling_properties"]
				if cp.has("height"):
					var ch = cp["height"]
					if typeof(ch) == TYPE_DICTIONARY and ch.has("length"):
						var hf = float(ch["length"]) * scale_factor
						if hf > 0.1: ceil_height = hf
					elif typeof(ch) == TYPE_INT or typeof(ch) == TYPE_FLOAT:
						var hf = float(ch) * scale_factor
						if hf > 0.1: ceil_height = hf
					elif typeof(ch) == TYPE_STRING and ch.is_valid_float():
						var hf = float(ch) * scale_factor
						if hf > 0.1: ceil_height = hf

			# Fallback to area.properties.height
			if ceil_height < 0.1 and area.has("properties") and typeof(area["properties"]) == TYPE_DICTIONARY:
				var h_prop = area["properties"].get("height", null)
				if h_prop != null:
					if typeof(h_prop) == TYPE_DICTIONARY and h_prop.has("length"):
						var hf = float(h_prop["length"]) * scale_factor
						if hf > 0.1: ceil_height = hf
					elif typeof(h_prop) == TYPE_INT or typeof(h_prop) == TYPE_FLOAT:
						var hf = float(h_prop) * scale_factor
						if hf > 0.1: ceil_height = hf

			if ceil_height < 0.1:
				ceil_height = 280.0 * scale_factor

			var ceil_depth = 0.1
			if area.has("ceiling_properties") and typeof(area["ceiling_properties"]) == TYPE_DICTIONARY:
				var cp = area["ceiling_properties"]
				if cp.has("thickness"):
					var t = float(cp["thickness"]) * scale_factor
					if t > 0.001: ceil_depth = t
			if ceil_depth < 0.05:
				ceil_depth = 0.05

			var ceil_poly = CSGPolygon3D.new()
			ceil_poly.polygon = polygon
			ceil_poly.mode = CSGPolygon3D.MODE_DEPTH
			ceil_poly.depth = ceil_depth
			ceil_poly.rotation.x = PI / 2
			ceil_poly.position.y = layer_altitude + ceil_height

			if area.has("ceiling_properties") and typeof(area["ceiling_properties"]) == TYPE_DICTIONARY and area["ceiling_properties"].has("material") and typeof(area["ceiling_properties"]["material"]) == TYPE_DICTIONARY:
				ceil_poly.material = create_material(area["ceiling_properties"]["material"])
			else:
				var ceil_mat = StandardMaterial3D.new()
				ceil_mat.albedo_color = Color(0.95, 0.95, 0.95)
				ceil_poly.material = ceil_mat

			csg.add_child(ceil_poly)
			print("[VideoGLB] Floor/Ceiling area: ", area_id, " floor_depth=", floor_depth, " ceil_h=", ceil_height, " ceil_depth=", ceil_depth)

# ─────────────────────────────────────────────────────────────────
# Asset loading — mirrors image_glb_creation.gd load_assets / _load_layer_items
# ─────────────────────────────────────────────────────────────────
func load_assets(data):
	# Video JSON has items at root of floor_plan_data (already unwrapped)
	if data.has("items"):
		_load_layer_items(data["items"], 0.0)
	elif data.has("assets"):
		_load_layer_items(data["assets"], 0.0)

func _load_layer_items(items, layer_altitude):
	for item_id in items:
		# Handle Dictionary (item_id is key) and Array (item_id IS the item)
		var item
		if typeof(items) == TYPE_DICTIONARY:
			item = items[item_id]
		else:
			item = item_id  # Array iteration: item_id IS the item
		if typeof(item) != TYPE_DICTIONARY:
			continue

		var model_path = ""

		# Prioritise local paths (set by main.py asset localization)
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

		if model_path != "" and FileAccess.file_exists(model_path):
			var glTF = GLTFDocument.new()
			var glTFState = GLTFState.new()
			var error = glTF.append_from_file(model_path, glTFState)
			if error == OK:
				var node = glTF.generate_scene(glTFState)
				if node:
					add_child(node)

					# Position: floor-plan X → Godot X, floor-plan Y → Godot Z, altitude → Godot Y
					var px = float(item.get("x", 0)) * 0.01
					var py = float(item.get("y", 0)) * 0.01  # floor-plan Y → Godot Z
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

					# Scale — same smart logic as image_glb_creation.gd
					var props = {}
					if item.has("properties") and typeof(item["properties"]) == TYPE_DICTIONARY:
						props = item["properties"]

					var raw_width = props.get("width", 100)
					var raw_depth = props.get("depth", 100)
					var raw_height_p = props.get("height", 100)

					var target_w = get_dimension_value(raw_width) * 0.01
					var target_d = get_dimension_value(raw_depth) * 0.01
					var target_h = get_dimension_value(raw_height_p) * 0.01

					var default_size = get_dimension_value(100) * 0.01
					var is_user_resized = (abs(target_w - default_size) > 0.001 or abs(target_h - default_size) > 0.001 or abs(target_d - default_size) > 0.001)

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

					# Material override (color / UV scale per material surface)
					if item.has("materials") and typeof(item["materials"]) == TYPE_DICTIONARY:
						var item_mats = item["materials"]
						var meshes = _get_all_meshes(node)
						for m in meshes:
							if not m.mesh:
								continue
							for i in range(m.mesh.get_surface_count()):
								var mat = m.get_active_material(i)
								if mat and mat is StandardMaterial3D:
									var m_name = mat.resource_name
									if m_name == null or m_name == "":
										continue
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

												var scale_u = new_mat.uv1_scale.x
												var scale_v = new_mat.uv1_scale.y
												var changed_scale = false

												if mat_opt.has("repeat"):
													var r = mat_opt["repeat"]
													if typeof(r) == TYPE_ARRAY and r.size() >= 2:
														var ru = float(r[0])
														var rv = float(r[1])
														if ru > 0: scale_u = ru
														if rv > 0: scale_v = rv
														changed_scale = true

												if mat_opt.has("scale"):
													var s = mat_opt["scale"]
													if typeof(s) == TYPE_ARRAY and s.size() >= 2:
														var su2 = float(s[0])
														var sv2 = float(s[1])
														if su2 > 0: scale_u = su2
														if sv2 > 0: scale_v = sv2
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

												if modified:
													m.set_surface_override_material(i, new_mat)

					print("[VideoGLB] Loaded asset: ", model_path, " final scale: ", final_scale)
			else:
				print("[VideoGLB] Failed to load GLTF: ", model_path, " Error: ", error)

		if model_path != "":
			_tracked_assets.append({
				"type": "item_asset",
				"id": item.get("id", "unknown"),
				"name": item.get("name", "unknown"),
				"path": model_path,
				"position": item.get("x", 0)
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
	var aabb := AABB()
	var first = true
	var meshes = _get_all_meshes(node)
	if meshes.size() == 0:
		return AABB(Vector3.ZERO, Vector3.ONE)
	for mesh_inst in meshes:
		var xform = node.global_transform.affine_inverse() * mesh_inst.global_transform
		var mesh_aabb = xform * mesh_inst.get_aabb()
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

# ─────────────────────────────────────────────────────────────────
# create_material — identical to image_glb_creation.gd (with normalUrl/roughnessUrl)
# ─────────────────────────────────────────────────────────────────
func create_material(mat_data):
	var mat = StandardMaterial3D.new()
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED

	# ── Base colour ────────────────────────────────────────────────────────────
	var base_color = Color(0.9, 0.9, 0.9)
	if mat_data.has("color"):
		var c_str = str(mat_data["color"]).strip_edges()
		if c_str.length() >= 3 and not c_str.begins_with("#"):
			c_str = "#" + c_str
		if c_str.is_valid_html_color():
			base_color = Color(c_str)
		else:
			print("[VideoGLB] Warning: Invalid color string: ", c_str)
	mat.albedo_color = base_color

	# ── Textures (skip when a custom colour has been explicitly set) ────────────
	# isColorEdited=true means the user picked a custom paint colour — never
	# override it with the default texture map.
	var is_color_edited = mat_data.get("isColorEdited", false)

	if not is_color_edited:
		var map_url = ""
		if mat_data.has("mapUrl") and mat_data["mapUrl"] != null and str(mat_data["mapUrl"]) != "":
			map_url = str(mat_data["mapUrl"])
		elif mat_data.has("texture_urls") and typeof(mat_data["texture_urls"]) == TYPE_ARRAY and mat_data["texture_urls"].size() > 0:
			map_url = str(mat_data["texture_urls"][0])

		if map_url != "":
			var tex = load_texture_from_path(map_url)
			if tex:
				mat.albedo_texture = tex
				mat.uv1_triplanar = true

		if mat_data.has("normalUrl") and mat_data["normalUrl"] != null and str(mat_data["normalUrl"]) != "":
			var tex = load_texture_from_path(str(mat_data["normalUrl"]))
			if tex:
				mat.normal_enabled = true
				mat.normal_texture = tex
				mat.uv1_triplanar = true

		if mat_data.has("roughnessUrl") and mat_data["roughnessUrl"] != null and str(mat_data["roughnessUrl"]) != "":
			var tex = load_texture_from_path(str(mat_data["roughnessUrl"]))
			if tex:
				mat.roughness_texture = tex
				mat.roughness_texture_channel = BaseMaterial3D.TEXTURE_CHANNEL_GREEN
				mat.uv1_triplanar = true

	# ── UV Scale / Repeat ──────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────────
# Texture loading — identical to image_glb_creation.gd
# ─────────────────────────────────────────────────────────────────
func _resolve_local_texture(url: String) -> String:
	if url == "" or url == "null": return ""

	# Normalize Windows backslashes → forward slashes (Godot FileAccess requires /)
	var normalized = url.replace("\\", "/")

	# Direct hit
	if FileAccess.file_exists(normalized): return normalized

	var filename = normalized.get_file()
	if filename == "": return ""

	# asset_downloads is the primary local texture cache
	var project_root = ProjectSettings.globalize_path("res://")
	var parent_dir = project_root.get_base_dir()  # one level up from godot_project/
	var asset_downloads = parent_dir.path_join("asset_downloads")

	var search_dirs = [
		asset_downloads,
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

	# Normalize Windows backslashes → forward slashes
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
