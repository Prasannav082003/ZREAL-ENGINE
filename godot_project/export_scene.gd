extends "res://image_glb_creation.gd"

# export_scene.gd
# Main entry point for rendering into an editable .tscn file

func _ready():
	print("Godot Exporter Started (Export Scene as .tscn)")
	
	var args = OS.get_cmdline_args()
	if OS.has_method("get_cmdline_user_args"):
		args.append_array(OS.get_cmdline_user_args())
	
	var input_json_path = ""
	var output_path = ""
	
	for i in range(args.size()):
		var arg = args[i]
		if arg.ends_with(".json"):
			input_json_path = arg
		elif arg.ends_with(".tscn") or arg.ends_with(".scn"):
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
	
	build_scene(data) # From image_glb_creation.gd
	# We don't bother setting up viewport render constraints since we are saving the tree nodes.
	setup_camera_for_export(data)
	
	# Pack the scene and save it
	call_deferred("export_to_tscn", output_path)

func setup_camera_for_export(data):
	var cam = Camera3D.new()
	cam.name = "MainCamera"
	var pos = Vector3(0, 1.5, 5)
	
	if data.has("threejs_camera") and typeof(data["threejs_camera"]) == TYPE_DICTIONARY:
		var p = data["threejs_camera"].get("position", {})
		if typeof(p) == TYPE_DICTIONARY:
			pos = Vector3(float(p.get("x",0)), float(p.get("y",1.5)), float(p.get("z",5)))
			
	cam.position = pos
	add_child(cam)

func load_assets(data):
	super.load_assets(data)

func export_to_tscn(output_path):
	print("Packing Scene...")
	
	# Strip dynamic EXR panorama before saving to prevent crash!
	for child in get_children():
		if child is WorldEnvironment and child.environment and child.environment.sky and child.environment.sky.sky_material is PanoramaSkyMaterial:
			print("Stripping dynamically loaded EXR PanoramaSkyMaterial to prevent save crash...")
			var proc_sky = ProceduralSkyMaterial.new()
			child.environment.sky.sky_material = proc_sky
			
	# All dynamic nodes must have scene_file_path empty and be owned by the root
	# for PackedScene to serialize them. This ensures runtime-built mesh instances
	# get saved directly.
	_set_owner_recursive(self, self)
	
	var packed_scene = PackedScene.new()
	print("Packing result...")
	# Remove the exporter script itself so the saved scene doesn't have broken dependencies
	self.set_script(null)
	var result = packed_scene.pack(self)
	
	if result == OK:
		print("Attempting to save to: ", output_path)
		var local_path = ProjectSettings.localize_path(output_path)
		print("Localized path: ", local_path)
		var save_err = ResourceSaver.save(packed_scene, local_path)
		print("Save completed with err code: ", save_err)
		
		if save_err == OK:
			print("Success: Scene saved to ", local_path)
		else:
			print("Error: Could not save .tscn file. Error Code: ", save_err)
	else:
		print("Error: Could not pack scene. Error Code: ", result)
		
	get_tree().quit(0)

func _set_owner_recursive(node: Node, new_owner: Node):
	if node != new_owner:
		node.owner = new_owner
	for child in node.get_children():
		_set_owner_recursive(child, new_owner)
