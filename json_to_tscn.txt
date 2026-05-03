import os
import sys
import json
import argparse
import subprocess
from scene_optimizer import SceneOptimizer

# Import the asset localizer from the main backend engine
from main import _download_and_localize_assets

def main():
    parser = argparse.ArgumentParser(description="Convert JSON to Godot .tscn scene")
    parser.add_argument("--input", "-i", required=True, help="Input JSON file path")
    parser.add_argument("--output", "-o", required=True, help="Output .tscn file path")
    parser.add_argument("--no-optimize", action="store_true", help="Skip scene culling/optimization")
    
    args = parser.parse_args()
    
    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)
    
    if not os.path.exists(input_path):
        print(f"Error: Input file missing: {input_path}")
        sys.exit(1)
        
    # Read the input payload
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)
        
    print("Localizing assets from remote URLs (this might take a moment)...")
    
    # Check if payload contains floor_plan_data or is just the floor plan itself
    if isinstance(payload, dict) and "floor_plan_data" in payload:
        fp_data = payload["floor_plan_data"]
        scene_data = json.loads(fp_data) if isinstance(fp_data, str) else fp_data
        
        # Download all azure blobs into asset_downloads and inject local references
        localized_scene = _download_and_localize_assets(scene_data, use_high_res=True, logger=None)
        
        # Wrap it back up
        payload["floor_plan_data"] = json.dumps(localized_scene) if isinstance(fp_data, str) else localized_scene
    else:
        # If the root is exactly the floor plan list/dict
        payload = _download_and_localize_assets(payload, use_high_res=True, logger=None)
        
    final_payload = payload
    
    if not args.no_optimize:
        print("Optimizing scene...")
        optimizer = SceneOptimizer()
        final_payload = optimizer.optimize(payload)
    else:
        print("Skipping scene optimization.")
        
    # Create temp directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    godot_project_dir = os.path.join(project_dir, "godot_project")
    temp_json_dir = os.path.join(godot_project_dir, "input_json")
    os.makedirs(temp_json_dir, exist_ok=True)
    
    # Write temp file for Godot to read
    temp_json_path = os.path.join(temp_json_dir, "temp_export_input.json")
    with open(temp_json_path, "w", encoding="utf-8") as f:
        json.dump(final_payload, f, indent=2)
        
    # Create output dir
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Godot binary
    godot_bin = os.path.join(project_dir, "godot", "bin", "godot.windows.editor.x86_64.console.exe")
    if not os.path.exists(godot_bin):
        print(f"Godot binary not found at: {godot_bin}")
        sys.exit(1)
        
    print(f"Launching Godot to build scene: {output_path}")
    
    cmd = [
        godot_bin,
        "--headless",
        "--path", godot_project_dir,
        "res://export_scene.tscn",
        "--",
        temp_json_path,
        output_path
    ]
    
    result = subprocess.run(cmd, cwd=godot_project_dir)
    
    if result.returncode == 0 and os.path.exists(output_path):
        print(f"\n======================================")
        print(f"SUCCESS! Scene exported to: {output_path}")
        print(f"You can now open this file in Godot GUI.")
        print(f"======================================")
    else:
        print(f"\nFAILED to export scene. Return code: {result.returncode}")
        
if __name__ == "__main__":
    main()
