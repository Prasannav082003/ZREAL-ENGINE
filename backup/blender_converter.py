# filename: blender_converter.py
#
# --- Enhanced Converter with COMPLETE Frontend Manual Color/Texture Support ---
# This script prioritizes manual frontend changes for ALL surfaces and assets
# Falls back to MTL/texture downloads only when no manual changes are specified
#
# Usage:
# blender --background --python blender_converter.py -- <input.json> <output_glb>

import bpy
import bmesh
import json
import os
import sys
import math
import glob
import uuid
import datetime
import re
from mathutils import Vector

# --- FIX: Add script directory to Python path to find wall_builder.py ---
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.append(script_dir)
except NameError:
    script_dir = os.getcwd()
    if script_dir not in sys.path:
        sys.path.append(script_dir)

# This import will now work correctly if wall_builder.py is in the same directory
import wall_builder

########################################
# Helper function for dimension extraction
########################################
def get_dimension_value(prop_value, default=100):
    """
    Extract dimension value from properties that can be:
    - Array format: [60]
    - Object format: {length: 60}
    - Direct numeric: 60
    
    Args:
        prop_value: The property value to extract from
        default: Default value if extraction fails
    
    Returns:
        float: The extracted dimension value
    """
    if isinstance(prop_value, list) and len(prop_value) > 0:
        return float(prop_value[0])
    elif isinstance(prop_value, dict):
        return float(prop_value.get('length', default))
    elif isinstance(prop_value, (int, float)):
        return float(prop_value)
    else:
        return float(default)

########################################
# Logging utilities (prints + in-memory)
########################################
LOG_LINES = []
def log(s=""):
    txt = str(s)
    print(txt)
    LOG_LINES.append(txt)

def write_log_file(output_glb_path):
    try:
        folder = os.path.dirname(os.path.abspath(output_glb_path))
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        lf = os.path.join(folder, f"glb_export_report_{ts}_{str(uuid.uuid4())[:8]}.log")
        with open(lf, "w", encoding="utf-8") as f:
            f.write("\n".join(LOG_LINES))
        log(f"Saved export report to: {lf}")
    except Exception as e:
        log(f"❌ Could not write export log file: {e}")

ASSET_PROCESSING_REPORT = []

# --- SANITIZATION UTILITIES ---
def _sanitize_data_for_report(data):
    """Recursively sanitizes dictionaries and lists for the report (removing local paths)."""
    if isinstance(data, dict):
        return {k: _sanitize_data_for_report(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_sanitize_data_for_report(v) for v in data]
    elif isinstance(data, str):
        # Match Windows (C:\\...) and Linux (/...) absolute paths
        path_pattern = r'([a-zA-Z]:\\[^ \t\n\r"\'\(\)]+)|(/[a-zA-Z0-9._-]+/[^ \t\n\r"\'\(\)]+)'
        try:
            import re
            # If it's a full path, just take the basename
            if (len(data) > 3 and data[1:3] == ":\\") or data.startswith("/"):
                return os.path.basename(data)
            # Otherwise, use regex to replace embedded paths
            return re.sub(path_pattern, "[PATH]", data)
        except:
            return data
    else:
        return data

def save_asset_report(output_glb_path):
    """Saves the detailed asset processing report to a JSON file side-by-side with the GLB (Sanitized)."""
    try:
        folder = os.path.dirname(os.path.abspath(output_glb_path))
        basename = os.path.splitext(os.path.basename(output_glb_path))[0]
        report_path = os.path.join(folder, f"{basename}_asset_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            # Sanitize the entire report before saving to prevent leaking local paths
            sanitized_report = _sanitize_data_for_report(ASSET_PROCESSING_REPORT)
            json.dump(sanitized_report, f, indent=2)
        log(f"✅ Saved sanitized detail asset report to: {report_path}")
    except Exception as e:
        log(f"❌ Could not write asset report: {e}")

########################################
# Enhanced Color Conversion Functions
########################################
def parse_rgb_color(color_str):
    """Parse RGB/RGBA color strings like 'rgb(175, 46, 46)' or 'rgba(0,255,0, 1)'"""
    if not isinstance(color_str, str):
        return None
    
    color_str = color_str.strip().lower()
    
    # Handle RGB format: rgb(175, 46, 46)
    if color_str.startswith('rgb('):
        try:
            numbers = color_str[4:-1].split(',')
            if len(numbers) >= 3:
                r = int(numbers[0].strip()) / 255.0
                g = int(numbers[1].strip()) / 255.0
                b = int(numbers[2].strip()) / 255.0
                return (r, g, b, 1.0)
        except Exception:
            return None
    
    # Handle RGBA format: rgba(0,255,0, 1)
    elif color_str.startswith('rgba('):
        try:
            numbers = color_str[5:-1].split(',')
            if len(numbers) >= 4:
                r = int(numbers[0].strip()) / 255.0
                g = int(numbers[1].strip()) / 255.0
                b = int(numbers[2].strip()) / 255.0
                a = float(numbers[3].strip())
                return (r, g, b, a)
        except Exception:
            return None
    
    return None

def hex_to_rgb(hex_color):
    """Converts a hex color string like #RRGGBB to a tuple of (r, g, b) floats."""
    if not isinstance(hex_color, str):
        return (0.8, 0.8, 0.8)
    
    # First try RGB/RGBA parsing
    rgb_result = parse_rgb_color(hex_color)
    if rgb_result:
        return rgb_result[:3]  # Return only RGB components
    
    # Then try HEX parsing
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:  # Short hex #RGB
        hex_color = ''.join([c*2 for c in hex_color])
    elif len(hex_color) != 6:
        return (0.8, 0.8, 0.8) # Default grey
    
    try:
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return (r, g, b)
    except ValueError:
        return (0.8, 0.8, 0.8)

def parse_color_value(color_value):
    """Parse any color format and return (r, g, b, a)"""
    if not color_value:
        return (0.8, 0.8, 0.8, 1.0)
    
    # Try RGB/RGBA first
    rgb_result = parse_rgb_color(color_value)
    if rgb_result:
        return rgb_result
    
    # Try HEX (with or without #)
    if isinstance(color_value, str):
        clean_hex = color_value.strip().lstrip('#')
        if len(clean_hex) in (3, 6):
            # Check if valid hex chars
            try:
                int(clean_hex, 16)
                rgb = hex_to_rgb(color_value) # hex_to_rgb handles the stripping internally too
                return (rgb[0], rgb[1], rgb[2], 1.0)
            except ValueError:
                pass
    
    # Default
    return (0.8, 0.8, 0.8, 1.0)

########################################
# Texture path resolution
########################################
def resolve_texture_path(candidate_path, search_dirs):
    """Try to resolve candidate_path to an existing file in search_dirs with flexible matching."""
    if not candidate_path:
        return None
    # FIX: Handle single-character paths that are remnants of bad JSON parsing
    if len(str(candidate_path)) <= 2:
        return None
    if os.path.isabs(candidate_path) and os.path.exists(candidate_path):
        return os.path.abspath(candidate_path)
    if os.path.exists(candidate_path):
        return os.path.abspath(candidate_path)
    basename = os.path.basename(candidate_path)
    name_no_ext, ext = os.path.splitext(basename)
    for d in search_dirs:
        try:
            cand = os.path.join(d, basename)
            if os.path.exists(cand):
                return os.path.abspath(cand)
        except Exception:
            pass
    # Strategy 2: Fuzzy match with suffix stripping (LP/HP handling)
    clean_name = name_no_ext.lower()
    for suffix in ['_lp', '_lr', '_hp', '_hr', '_4k', '-png']:
        clean_name = clean_name.replace(suffix, '')
    
    # Remove generic trailing numbers or copy suffixes if needed, but be careful
    
    for d in search_dirs:
        try:
            if not os.path.exists(d): continue
            for f in os.listdir(d):
                f_lower = f.lower()
                # Check 1: Exact containment of original name
                if name_no_ext.lower() in f_lower:
                    candidate = os.path.join(d, f)
                    if os.path.isfile(candidate): return os.path.abspath(candidate)
                
                # Check 2: Containment of cleaned name (e.g. Bed_LP -> Bed matching Bed_HP)
                if len(clean_name) > 3 and clean_name in f_lower:
                     # Verify extension matches or is compatible
                     if f_lower.endswith(ext.lower()) or (ext.lower() == '.obj' and f_lower.endswith('.glb')):
                         candidate = os.path.join(d, f)
                         if os.path.isfile(candidate): return os.path.abspath(candidate)
        except Exception:
            pass
            
    patterns = [f"**/{name_no_ext}.*", f"**/*{name_no_ext}*.*"]
    for d in search_dirs:
        try:
            for p in patterns:
                for match in glob.glob(os.path.join(d, p), recursive=True):
                    if os.path.isfile(match):
                        return os.path.abspath(match)
        except Exception:
            pass
    try:
        return os.path.abspath(candidate_path)
    except Exception:
        return candidate_path

def ensure_image_for_export(image_path):
    """Load or reuse bpy.data.images for image_path and set deterministic name/filepath."""
    if not image_path:
        return None
    try:
        image_path = os.path.abspath(image_path)
        for img in bpy.data.images:
            try:
                fp = getattr(img, "filepath", "")
                if fp:
                    try:
                        if os.path.abspath(bpy.path.abspath(fp)) == image_path:
                            img.name = os.path.basename(image_path)
                            img["z_realty_source_path"] = image_path
                            try: img.reload()
                            except Exception: pass
                            return img
                    except Exception:
                        pass
            except Exception:
                continue
        try:
            img = bpy.data.images.load(image_path, check_existing=False)
        except Exception:
            try:
                img = bpy.data.images.load(image_path, check_existing=True)
            except Exception as e:
                log(f"  ❌ ensure_image_for_export: failed to load {image_path}: {e}")
                return None
        if img:
            img.name = os.path.basename(image_path)
            img.filepath = image_path
            img["z_realty_source_path"] = image_path
            try: img.reload()
            except Exception: pass
            return img
    except Exception as e:
        log(f"  ❌ ensure_image_for_export error: {e}")
        return None

def report_material_textures(mat):
    textures = []
    if not mat or not mat.use_nodes:
        return textures
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE':
            img = getattr(node, "image", None)
            if img:
                src = img.get("z_realty_source_path") or getattr(img, "filepath", getattr(img, "name", None))
                textures.append(src)
    return textures

########################################
# COMPLETE Manual Color/Texture Detection and Application
########################################

def has_manual_changes(material_data):
    """
    Check if material has manual changes from frontend.
    Returns True if manual color or texture is specified.
    """
    if not material_data:
        return False
    
    # Check for isColorEdited flag (explicit indicator)
    if material_data.get('isColorEdited') == True:
        return True
    
    # Check for manual color (ANY color format)
    if material_data.get('color'):
        color_val = material_data['color']
        if isinstance(color_val, str) and len(color_val.strip()) > 0:
            return True
    
    # Check for manual texture (mapUrl is used in JSON)
    if material_data.get('mapUrl'):
        map_val = material_data['mapUrl']
        if isinstance(map_val, str) and len(map_val.strip()) > 2:
            return True
    
    # Check for manual texture (map is also supported)
    if material_data.get('map'):
        map_val = material_data['map']
        if isinstance(map_val, str) and len(map_val.strip()) > 2:
            return True
    
    # Check for any texture maps
    texture_map_keys = ['normalUrl', 'roughnessUrl', 'metalnessUrl', 'aoUrl', 'emissionUrl', 'displacementUrl',
                        'normalMap', 'roughnessMap', 'metalnessMap', 'bumpMap', 'specularMap', 'envMap']
    for key in texture_map_keys:
        if material_data.get(key):
            val = material_data[key]
            if isinstance(val, str) and len(val.strip()) > 2:
                return True
    
    # Check for other manual properties
    manual_props = ['roughness', 'metalness', 'opacity', 'transparent']
    for prop in manual_props:
        if prop in material_data:
            return True
    
    return False

def create_material_from_manual_changes(name, manual_data, search_dirs=None):
    """
    Create material exclusively from manual frontend changes.
    Prioritizes this over any MTL/texture downloads.
    Supports ALL color formats and PBR maps.
    """
    if search_dirs is None: 
        search_dirs = [os.getcwd()]
    
    log(f"  🎨 Creating material from MANUAL frontend changes: '{name}'")
    
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Clear default nodes
    for node in list(nodes):
        nodes.remove(node)

    # Create basic nodes
    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (600, 0)
    bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    bsdf.location = (0, 0)
    links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])

    # --- Common Texture Coordinate Setup ---
    tex_coord = nodes.new(type='ShaderNodeTexCoord')
    tex_coord.location = (-1000, 0)
    mapping = nodes.new(type='ShaderNodeMapping')
    mapping.location = (-800, 0)
    links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])

    # Apply global scale
    # Check for repeat array first (used in JSON), then fall back to mapScaleU/mapScaleV
    texture_scale_u = 1.0
    texture_scale_v = 1.0
    
    if 'repeat' in manual_data:
        repeat = manual_data.get('repeat', [1, 1])
        if isinstance(repeat, list) and len(repeat) >= 2:
            texture_scale_u = float(repeat[0]) if repeat[0] else 1.0
            texture_scale_v = float(repeat[1]) if repeat[1] else 1.0
    else:
        texture_scale_u = manual_data.get('mapScaleU', manual_data.get('texture_scale_x', 1.0))
        texture_scale_v = manual_data.get('mapScaleV', manual_data.get('texture_scale_y', 1.0))
    
    mapping.inputs['Scale'].default_value = (texture_scale_u, texture_scale_v, 1.0)
    log(f"    ✅ Applied scale: U={texture_scale_u}, V={texture_scale_v}")

    # --- Helper to load and link a map ---
    def apply_map(map_keys, target_socket_name, is_non_color=False, y_pos=0, is_specular=False):
        # Find the first key that exists in manual_data
        path = None
        for k in map_keys:
            if manual_data.get(k):
                path = manual_data[k]
                break
        
        if path:
            # Handle empty or invalid paths
            if not path or (isinstance(path, str) and len(path.strip()) <= 2):
                return False
                
            resolved_path = resolve_texture_path(path, search_dirs)
            if resolved_path and os.path.exists(resolved_path):
                tex_node = nodes.new(type='ShaderNodeTexImage')
                tex_node.location = (-500, y_pos)
                image = ensure_image_for_export(resolved_path)
                if image:
                    tex_node.image = image
                    if is_non_color and not is_specular:
                        image.colorspace_settings.name = 'Non-Color'
                    
                    links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
                    
                    # Special handling for Normal Map
                    if target_socket_name == 'Normal':
                        normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                        normal_map_node.location = (-250, y_pos)
                        links.new(tex_node.outputs['Color'], normal_map_node.inputs['Color'])
                        links.new(normal_map_node.outputs['Normal'], bsdf.inputs['Normal'])
                    else:
                        # Connect to appropriate socket
                        if target_socket_name in bsdf.inputs:
                            links.new(tex_node.outputs['Color'], bsdf.inputs[target_socket_name])
                        elif target_socket_name == 'Specular':
                            # Handle specular separately if needed
                            pass
                        
                    log(f"    ✅ Applied map for {target_socket_name}: {os.path.basename(resolved_path)}")
                return True
        return False

    # Track which sockets are already connected
    connected_sockets = set()
    
    # 1. Base Color / Map (Priority: Mix > Map > Color)
    color_val = manual_data.get('color', '')
    is_color_edited = manual_data.get('isColorEdited', False)
    
    # Check if we have a texture map for base color
    # We need to capture the output of apply_map to know if we need to mix
    # So we'll modify the flow slightly to allow interception
    
    # Find texture path first
    map_keys = ['mapUrl', 'map', 'baseMap', 'albedoMap', 'diffuse']
    color_map_path = None
    for k in map_keys:
        if manual_data.get(k):
            color_map_path = manual_data[k]
            break
            
    resolved_color_map = resolve_texture_path(color_map_path, search_dirs) if color_map_path else None
    has_valid_color_map = resolved_color_map and os.path.exists(resolved_color_map)
    
    # Determine Color
    rgba_color = (0.8, 0.8, 0.8, 1.0)
    has_valid_color = False
    if color_val and isinstance(color_val, str) and len(color_val.strip()) > 0:
        rgba_color = parse_color_value(color_val)
        has_valid_color = True

    # LOGIC 1: Texture + Color Override (MULTIPLY/MIX)
    if has_valid_color_map and has_valid_color and is_color_edited:
        # Create Texture Node
        tex_node = nodes.new(type='ShaderNodeTexImage')
        tex_node.location = (-500, 300)
        image = ensure_image_for_export(resolved_color_map)
        if image:
            tex_node.image = image
            links.new(mapping.outputs['Vector'], tex_node.inputs['Vector'])
            
            # Create Mix Node (Multiply)
            # Try to use ShaderNodeMix (Newer) or ShaderNodeMixRGB (Older)
            mix_node = None
            input_a = None
            input_b = None
            output_res = None
            
            try:
                # Blender 3.4+
                mix_node = nodes.new(type='ShaderNodeMix')
                mix_node.data_type = 'RGBA'
                mix_node.blend_type = 'MULTIPLY'
                mix_node.inputs[0].default_value = 1.0 # Factor
                
                # Inputs for RGBA mode are often named 'A' and 'B'
                input_a = mix_node.inputs['A']
                input_b = mix_node.inputs['B']
                output_res = mix_node.outputs['Result']
                
            except (RuntimeError, KeyError, AttributeError, Exception):
                # Fallback to ShaderNodeMixRGB
                if mix_node: 
                    try: nodes.remove(mix_node)
                    except: pass
                
                log(f"    ⚠️ 'ShaderNodeMix' failed or missing, falling back to 'ShaderNodeMixRGB'")
                mix_node = nodes.new(type='ShaderNodeMixRGB')
                mix_node.blend_type = 'MULTIPLY'
                mix_node.inputs['Fac'].default_value = 1.0
                input_a = mix_node.inputs['Color1']
                input_b = mix_node.inputs['Color2']
                output_res = mix_node.outputs['Color']
            
            mix_node.location = (-200, 300)
            
            # Connect
            links.new(tex_node.outputs['Color'], input_a)
            input_b.default_value = rgba_color
            links.new(output_res, bsdf.inputs['Base Color'])
            
            log(f"    ✅ Applied MIXED color + texture: {os.path.basename(resolved_color_map)} * {color_val}")
            connected_sockets.add('Base Color')
            
    # LOGIC 2: Texture Only
    elif has_valid_color_map:
         apply_map(map_keys, 'Base Color', is_non_color=False, y_pos=300)
         connected_sockets.add('Base Color')
         
    # LOGIC 3: Color Only
    elif has_valid_color:
        bsdf.inputs['Base Color'].default_value = rgba_color
        log(f"    ✅ Applied manual color: {color_val} -> {rgba_color}")
        connected_sockets.add('Base Color')

    # 2. Roughness Map
    has_rough_map = apply_map(['roughnessMap', 'roughMap'], 'Roughness', 
                             is_non_color=True, y_pos=0)
    if has_rough_map:
        connected_sockets.add('Roughness')
    elif 'roughness' in manual_data:
        bsdf.inputs['Roughness'].default_value = float(manual_data['roughness'])
        connected_sockets.add('Roughness')

    # 3. Metallic Map
    has_metal_map = apply_map(['metalnessMap', 'metallicMap', 'metalMap'], 'Metallic', 
                             is_non_color=True, y_pos=-300)
    if has_metal_map:
        connected_sockets.add('Metallic')
    elif 'metalness' in manual_data:
        bsdf.inputs['Metallic'].default_value = float(manual_data['metalness'])
        connected_sockets.add('Metallic')

    # 4. Normal / Bump Map
    has_normal_map = apply_map(['bumpMap', 'normalMap', 'normal'], 'Normal', 
                              is_non_color=True, y_pos=-600)
    if has_normal_map:
        connected_sockets.add('Normal')

    # 5. Specular Map (if provided)
    apply_map(['specularMap', 'specular'], 'Specular', is_non_color=True, y_pos=-900, is_specular=True)

    # 6. Environment Map (if provided)
    apply_map(['envMap'], 'Emission', is_non_color=False, y_pos=-1200)

    # 7. Transparency
    opacity = manual_data.get('opacity', 1.0)
    transparent = manual_data.get('transparent', False)
    
    if float(opacity) < 1.0 or transparent:
        bsdf.inputs['Alpha'].default_value = float(opacity)
        mat.blend_method = 'BLEND'
        log(f"    ✅ Applied transparency: opacity={opacity}, transparent={transparent}")

    # If no base color was set, use default
    if 'Base Color' not in connected_sockets:
        bsdf.inputs['Base Color'].default_value = (0.8, 0.8, 0.8, 1.0)

    return mat

########################################
# Enhanced Material Builder with Manual Change Priority for ALL cases
########################################
TEXTURE_MAP_KEYWORDS = {
    'normal': 'Normal', 'normalmap': 'Normal', 'nrm': 'Normal', 'normaldx': 'Normal', 'normalgl': 'Normal', 'nx': 'Normal',
    'metallicroughness': 'PackedPBR', 'metalrough': 'PackedPBR', 'orm': 'PackedPBR',
    'basecolor': 'Base Color', 'color': 'Base Color', 'albedo': 'Base Color', 'diffuse': 'Base Color', 'diff': 'Base Color', 'kd': 'Base Color',
    'roughness': 'Roughness', 'rough': 'Roughness', 'rgh': 'Roughness',
    'metallic': 'Metallic', 'metalness': 'Metallic', 'metal': 'Metallic', 'pm': 'Metallic',
    'displacement': 'Displacement', 'disp': 'Displacement', 'height': 'Displacement', 'displacemnet': 'Displacement',
    'ao': 'Ambient Occlusion', 'occlusion': 'Ambient Occlusion', 'ambientocclusion': 'Ambient Occlusion',
    'specular': 'Specular', 'spec': 'Specular',
    'emissive': 'Emission', 'emission': 'Emission', 'ke': 'Emission',
    'alpha': 'Alpha', 'opacity': 'Alpha',
}

def create_material_from_paths(name, mat_info, is_wall_or_floor=False, search_dirs=None, manual_data=None):
    """
    Enhanced material creation that prioritizes manual frontend changes.
    Falls back to MTL/texture downloads only when no manual changes exist.
    """
    # PRIORITY 1: Check for manual changes from frontend
    if manual_data and has_manual_changes(manual_data):
        return create_material_from_manual_changes(name, manual_data, search_dirs)
    
    # PRIORITY 2: Fall back to original MTL/texture download logic
    if search_dirs is None: search_dirs = [os.getcwd()]
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    for n in list(nodes): nodes.remove(n)
    output_node = nodes.new(type='ShaderNodeOutputMaterial'); output_node.location = (400, 0)
    bsdf = nodes.new(type='ShaderNodeBsdfPrincipled'); bsdf.location = (0, 0)
    links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])

    maps, props, fallback_textures, fallback_color = {}, {}, [], None
    texture_scale_u = 1.0
    texture_scale_v = 1.0
    
    if not mat_info: log(f"  - create_material_from_paths: no mat_info for material '{name}'")
    elif isinstance(mat_info, dict):
        maps = mat_info.get('maps', {})
        props = mat_info.get('props', {})
        fallback_textures = mat_info.get('textures', []) or mat_info.get('textures_list', []) or []
        fallback_color = mat_info.get('fallback_color')
        
        # Extract texture scaling parameters
        texture_scale_u = mat_info.get('mapScaleU', mat_info.get('texture_scale_x', 1.0))
        texture_scale_v = mat_info.get('mapScaleV', mat_info.get('texture_scale_y', 1.0))
        
        if 'mapScale' in mat_info:
            scale_data = mat_info.get('mapScale', {})
            if isinstance(scale_data, dict):
                texture_scale_u = scale_data.get('U', texture_scale_u)
                texture_scale_v = scale_data.get('V', texture_scale_v)
    else:
        if isinstance(mat_info, str):
            if mat_info.strip().startswith('['):
                try:
                    parsed_list = json.loads(mat_info.replace("'", '"'))
                    fallback_textures = [p for p in parsed_list if isinstance(p, str)]
                except json.JSONDecodeError:
                    log(f"  ❌ Could not parse stringified list for material '{name}': {mat_info}")
                    fallback_textures = [mat_info]
            else:
                fallback_textures = [mat_info]
        elif isinstance(mat_info, list): fallback_textures = mat_info

    mapping_node, tex_coord_node = None, None
    if is_wall_or_floor or maps or fallback_textures:
        tex_coord_node = nodes.new(type='ShaderNodeTexCoord'); tex_coord_node.location = (-1000, 200)
        mapping_node = nodes.new(type='ShaderNodeMapping'); mapping_node.location = (-800, 200)
        try:
            if is_wall_or_floor:
                mapping_node.inputs['Rotation'].default_value[2] = math.radians(90)
                mapping_node.inputs['Scale'].default_value = (4.0 * texture_scale_u, 4.0 * texture_scale_v, 4.0)
            else:
                mapping_node.inputs['Scale'].default_value = (texture_scale_u, texture_scale_v, 1.0)
            links.new(tex_coord_node.outputs['UV'], mapping_node.inputs['Vector'])
        except: pass

    used_images = []

    def create_tex_node(image_path, map_settings=None, node_y=0):
        resolved = resolve_texture_path(image_path, search_dirs)
        if not resolved or not os.path.exists(resolved):
            log(f"  ❌ Texture file missing for mat '{name}': candidate='{image_path}' resolved='{resolved}'")
            return None, None
        image = ensure_image_for_export(resolved)
        if not image: return None, None
        tex_node = nodes.new(type='ShaderNodeTexImage'); tex_node.image = image
        tex_node.location = (-400, node_y)
        used_images.append(resolved)

        if map_settings and ('scale' in map_settings or 'offset' in map_settings):
            local_mapping = nodes.new(type='ShaderNodeMapping'); local_mapping.location = (-650, node_y)
            if tex_coord_node: links.new(tex_coord_node.outputs['UV'], local_mapping.inputs['Vector'])
            if 'scale' in map_settings and map_settings['scale']: local_mapping.inputs['Scale'].default_value = (map_settings['scale'][0], map_settings['scale'][1], 1.0)
            if 'offset' in map_settings and map_settings['offset']: local_mapping.inputs['Location'].default_value = (map_settings['offset'][0], map_settings['offset'][1], 0.0)
            links.new(local_mapping.outputs['Vector'], tex_node.inputs['Vector'])
        elif mapping_node: links.new(mapping_node.outputs['Vector'], tex_node.inputs['Vector'])
        return tex_node, image

    texture_paths_to_process = []
    processed_paths = set()
    if maps:
        for channel, entries in maps.items():
            for entry in entries:
                path = entry.get('path')
                if path and path not in processed_paths:
                    texture_paths_to_process.append(path); processed_paths.add(path)

    if isinstance(fallback_textures, str):
        try:
            parsed_list = json.loads(fallback_textures.replace("'", '"'))
            if isinstance(parsed_list, list):
                fallback_textures = parsed_list
            else:
                fallback_textures = []
        except (json.JSONDecodeError, TypeError):
            log(f"  ❌ Could not parse texture list for '{name}', treating as empty.")
            fallback_textures = []

    for path in fallback_textures:
        if path and path not in processed_paths:
            texture_paths_to_process.append(path); processed_paths.add(path)

    node_y = 300

    if is_wall_or_floor and texture_paths_to_process:
        base_color_path = None
        color_keywords = ['basecolor', 'color', 'diffuse', 'albedo']

        for path in texture_paths_to_process:
            fname_lower = os.path.basename(str(path)).lower()
            if any(k in fname_lower for k in color_keywords):
                base_color_path = path
                break

        if base_color_path:
            log(f"  - Prioritizing '{os.path.basename(base_color_path)}' as Base Color for wall/floor material '{name}'.")
            tex_node, image = create_tex_node(base_color_path, None, node_y)
            if tex_node:
                if 'Base Color' in bsdf.inputs and not bsdf.inputs['Base Color'].is_linked:
                    links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
                node_y -= 260

            texture_paths_to_process = [p for p in texture_paths_to_process if p != base_color_path]
        else:
            log(f"  - No specific base color texture found for wall/floor '{name}', using default assignment order.")

    for path in texture_paths_to_process:
        fname_lower = os.path.basename(str(path)).lower().replace('_', '')
        chosen_map = None
        for keyword, socket in TEXTURE_MAP_KEYWORDS.items():
            if keyword in fname_lower:
                chosen_map = socket; break
        if not chosen_map: chosen_map = 'Base Color'

        tex_node, image = create_tex_node(path, None, node_y); node_y -= 260
        if not tex_node: continue

        if chosen_map == 'Normal':
            image.colorspace_settings.name = 'Non-Color'
            norm_map_node = nodes.new(type='ShaderNodeNormalMap'); norm_map_node.location = (-200, tex_node.location.y)
            links.new(tex_node.outputs['Color'], norm_map_node.inputs['Color'])
            if not bsdf.inputs['Normal'].is_linked:
                links.new(norm_map_node.outputs['Normal'], bsdf.inputs['Normal'])
        elif chosen_map == 'PackedPBR':
            image.colorspace_settings.name = 'Non-Color'
            sep_node = nodes.new(type='ShaderNodeSeparateColor'); sep_node.location = (-200, tex_node.location.y)
            links.new(tex_node.outputs['Color'], sep_node.inputs['Color'])
            if 'Metallic' in bsdf.inputs and not bsdf.inputs['Metallic'].is_linked: links.new(sep_node.outputs['Red'], bsdf.inputs['Metallic'])
            if 'Roughness' in bsdf.inputs and not bsdf.inputs['Roughness'].is_linked: links.new(sep_node.outputs['Green'], bsdf.inputs['Roughness'])
            log(f"    - Interpreted '{os.path.basename(path)}' as Packed PBR (R->Metallic, G->Roughness)")
        elif chosen_map == 'Displacement':
            image.colorspace_settings.name = 'Non-Color'
            disp_node = nodes.new(type='ShaderNodeDisplacement'); disp_node.location = (150, tex_node.location.y)
            links.new(tex_node.outputs['Color'], disp_node.inputs['Height'])
            links.new(disp_node.outputs['Displacement'], output_node.inputs['Displacement'])
            mat.cycles.displacement_method = 'DISPLACEMENT'
        elif chosen_map == 'Alpha':
            image.colorspace_settings.name = 'Non-Color'
            if not bsdf.inputs['Alpha'].is_linked: links.new(tex_node.outputs['Color'], bsdf.inputs['Alpha'])
            mat.blend_method = 'BLEND'
        else:
            target_socket = chosen_map
            if target_socket not in bsdf.inputs: target_socket = 'Base Color'
            if target_socket != 'Base Color': image.colorspace_settings.name = 'Non-Color'
            if not bsdf.inputs[target_socket].is_linked:
                links.new(tex_node.outputs['Color'], bsdf.inputs[target_socket])

    if fallback_color and not bsdf.inputs['Base Color'].is_linked:
        rgba_color = parse_color_value(fallback_color)
        bsdf.inputs['Base Color'].default_value = rgba_color
        log(f"    - Applied fallback color {fallback_color} -> {rgba_color} to material '{name}'")

    if 'Kd' in props and not bsdf.inputs['Base Color'].is_linked:
        r,g,b = props.get('Kd', (0.8,0.8,0.8)); a = props.get('d', 1.0)
        if max(r,g,b) > 1.5: r,g,b = r/255.0, g/255.0, b/255.0
        bsdf.inputs['Base Color'].default_value = (r, g, b, a)
    if not bsdf.inputs['Roughness'].is_linked:
        if 'roughness' in props: bsdf.inputs['Roughness'].default_value = float(props['roughness'])
        elif 'Ns' in props: bsdf.inputs['Roughness'].default_value = 1.0 - min(1.0, float(props['Ns']) / 1000.0)
    if 'metallic' in props and not bsdf.inputs['Metallic'].is_linked: bsdf.inputs['Metallic'].default_value = float(props['metallic'])
    if not bsdf.inputs['Alpha'].is_linked:
        alpha_val = None
        if 'd' in props: alpha_val = float(props['d'])
        elif 'Tr' in props: alpha_val = 1.0 - float(props['Tr'])
        if alpha_val is not None:
            bsdf.inputs['Alpha'].default_value = alpha_val
            if alpha_val < 0.999: mat.blend_method = 'BLEND'

    mat["z_realty_used_textures"] = json.dumps(list(set(used_images)))
    log(f"  ✅ Material created: '{name}' | images: {len(used_images)} | scale: U={texture_scale_u}, V={texture_scale_v}")
    for p in used_images: log(f"     - {p}")
    return mat

########################################
# COMPLETE Wall Material Creation with Manual Change Support
########################################

def create_wall_material_with_manual_support(line_id, line_data, search_dirs):
    """
    Create wall materials with priority for manual frontend changes.
    Now properly reads from inner_properties.material and outer_properties.material.
    """
    materials = {}
    
    # Get inner and outer properties (these contain the material data)
    inner_props = line_data.get('inner_properties', {})
    outer_props = line_data.get('outer_properties', {})
    
    # Get material data from properties
    inner_material = inner_props.get('material', {})
    outer_material = outer_props.get('material', {})
    
    # Extract texture URLs from material
    inner_textures = []
    if inner_material.get('mapUrl'):
        inner_textures.append(inner_material.get('mapUrl'))
    if inner_material.get('normalUrl'):
        inner_textures.append(inner_material.get('normalUrl'))
    if inner_material.get('roughnessUrl'):
        inner_textures.append(inner_material.get('roughnessUrl'))
    if inner_material.get('metalnessUrl'):
        inner_textures.append(inner_material.get('metalnessUrl'))
    
    outer_textures = []
    if outer_material.get('mapUrl'):
        outer_textures.append(outer_material.get('mapUrl'))
    if outer_material.get('normalUrl'):
        outer_textures.append(outer_material.get('normalUrl'))
    if outer_material.get('roughnessUrl'):
        outer_textures.append(outer_material.get('roughnessUrl'))
    if outer_material.get('metalnessUrl'):
        outer_textures.append(outer_material.get('metalnessUrl'))
    
    # Extract colors (fallback if no textures)
    inner_color = inner_material.get('color', inner_props.get('color'))
    outer_color = outer_material.get('color', outer_props.get('color'))
    
    # Extract scaling parameters from material repeat
    inner_repeat = inner_material.get('repeat', [1, 1])
    inner_scale_u = float(inner_repeat[0]) if isinstance(inner_repeat, list) and len(inner_repeat) > 0 else 1.0
    inner_scale_v = float(inner_repeat[1]) if isinstance(inner_repeat, list) and len(inner_repeat) > 1 else 1.0
    
    outer_repeat = outer_material.get('repeat', [1, 1])
    outer_scale_u = float(outer_repeat[0]) if isinstance(outer_repeat, list) and len(outer_repeat) > 0 else 1.0
    outer_scale_v = float(outer_repeat[1]) if isinstance(outer_repeat, list) and len(outer_repeat) > 1 else 1.0
    
    # Prepare material info
    inner_mat_info = {}
    inner_manual_data = None
    
    if inner_textures and len(inner_textures) > 0:
        inner_mat_info = {
            'textures': inner_textures,
            'mapScaleU': inner_scale_u,
            'mapScaleV': inner_scale_v
        }
        log(f"  ✨ Found {len(inner_textures)} texture(s) for inner wall in line '{line_id}'. Using textures.")
    elif inner_color:
        inner_manual_data = {
            'color': inner_color,
            'roughness': 0.6,
            'mapScaleU': inner_scale_u,
            'mapScaleV': inner_scale_v
        }
        inner_mat_info = inner_manual_data
        log(f"  🎨 Using color for inner wall in line '{line_id}': {inner_color}")
    else:
        inner_manual_data = {
            'color': '#ffffff',
            'roughness': 0.6,
            'mapScaleU': inner_scale_u,
            'mapScaleV': inner_scale_v
        }
        inner_mat_info = inner_manual_data
        log(f"  ⚪ No texture/color found for inner wall in line '{line_id}', using default white.")
    
    outer_mat_info = {}
    outer_manual_data = None
    
    if outer_textures and len(outer_textures) > 0:
        outer_mat_info = {
            'textures': outer_textures,
            'mapScaleU': outer_scale_u,
            'mapScaleV': outer_scale_v
        }
        log(f"  ✨ Found {len(outer_textures)} texture(s) for outer wall in line '{line_id}'. Using textures.")
    elif outer_color:
        outer_manual_data = {
            'color': outer_color,
            'roughness': 0.6,
            'mapScaleU': outer_scale_u,
            'mapScaleV': outer_scale_v
        }
        outer_mat_info = outer_manual_data
        log(f"  🎨 Using color for outer wall in line '{line_id}': {outer_color}")
    else:
        outer_manual_data = {
            'color': '#ffffff',
            'roughness': 0.6,
            'mapScaleU': outer_scale_u,
            'mapScaleV': outer_scale_v
        }
        outer_mat_info = outer_manual_data
        log(f"  ⚪ No texture/color found for outer wall in line '{line_id}', using default white.")
    
    # Create materials
    mat_inner = create_material_from_paths(
        f"mat_{line_id}_inner", 
        inner_mat_info, 
        is_wall_or_floor=True, 
        search_dirs=search_dirs,
        manual_data=inner_manual_data
    )
    
    mat_outer = create_material_from_paths(
        f"mat_{line_id}_outer", 
        outer_mat_info, 
        is_wall_or_floor=True, 
        search_dirs=search_dirs,
        manual_data=outer_manual_data
    )
    
    if mat_inner: 
        materials[f"mat_{line_id}_inner"] = mat_inner
        if inner_textures:
            log(f"  ✅ Applied TEXTURE inner wall material for line '{line_id}'")
        elif inner_color:
            log(f"  ✅ Applied COLOR inner wall material for line '{line_id}'")
        else:
            log(f"  ✅ Applied DEFAULT inner wall material for line '{line_id}'")
    
    if mat_outer: 
        materials[f"mat_{line_id}_outer"] = mat_outer
        if outer_textures:
            log(f"  ✅ Applied TEXTURE outer wall material for line '{line_id}'")
        elif outer_color:
            log(f"  ✅ Applied COLOR outer wall material for line '{line_id}'")
        else:
            log(f"  ✅ Applied DEFAULT outer wall material for line '{line_id}'")
    
    return materials

########################################
# COMPLETE Floor/Ceiling Material Creation with Manual Change Support
########################################

def create_floor_ceiling_material_with_manual_support(area_id, area_data, surface_type, search_dirs):
    """
    Create floor or ceiling materials with priority for manual frontend changes.
    Now properly reads from floor_properties.material and ceiling_properties.material.
    """
    # 1. Get surface properties
    surface_properties = area_data.get(f'{surface_type}_properties', {})
    
    # 2. Get material data from surface properties
    material = surface_properties.get('material', {})
    
    # 3. Extract texture URLs from material
    texture_list = []
    if material.get('mapUrl'):
        texture_list.append(material.get('mapUrl'))
    if material.get('normalUrl'):
        texture_list.append(material.get('normalUrl'))
    if material.get('roughnessUrl'):
        texture_list.append(material.get('roughnessUrl'))
    if material.get('metalnessUrl'):
        texture_list.append(material.get('metalnessUrl'))
    if material.get('aoUrl'):
        texture_list.append(material.get('aoUrl'))
    
    has_texture = len(texture_list) > 0
    
    # 4. Extract color from material
    color = material.get('color', '#ffffff')
    
    # 5. Extract scaling parameters from material repeat
    repeat = material.get('repeat', [1, 1])
    scale_u = float(repeat[0]) if isinstance(repeat, list) and len(repeat) > 0 else 1.0
    scale_v = float(repeat[1]) if isinstance(repeat, list) and len(repeat) > 1 else 1.0
    
    # 6. Prepare material info
    surface_mat_info = {}
    manual_data = None
    
    if has_texture:
        log(f"  ✨ Found {len(texture_list)} texture(s) for {surface_type} in area '{area_id}'. [Priority 1: Texture]")
        log(f"     Scale X(U): {scale_u}, Y(V): {scale_v}")
        
        surface_mat_info = {
            'textures': texture_list,
            'mapScaleU': scale_u,
            'mapScaleV': scale_v,
            'props': {'roughness': 0.6}
        }
    else:
        # Use color as fallback
        manual_data = {
            'color': color if color else '#ffffff',
            'roughness': 0.6,
            'mapScaleU': scale_u,
            'mapScaleV': scale_v
        }
        surface_mat_info = manual_data
        log(f"  🎨 Using color for {surface_type} in area '{area_id}': {color}")
    
    # 7. Create the Material
    mat = create_material_from_paths(
        f"mat_{area_id}_{surface_type}", 
        surface_mat_info, 
        is_wall_or_floor=True, 
        search_dirs=search_dirs,
        manual_data=manual_data
    )
    
    return mat

########################################
# COMPLETE Asset Material Application with Manual Change Support
########################################

def apply_materials_to_obj_with_manual_support(obj_dict, mtl_data, item_data, search_dirs, context_name=""):
    """
    Apply materials to objects with priority for manual frontend changes.
    Falls back to MTL data when no manual changes exist.
    """
    if search_dirs is None: 
        search_dirs = [os.path.join(os.getcwd(), "asset_downloads"), os.getcwd()]
    
    log(f"  Applying materials for asset context: '{context_name}'")
    
    # Get manual material data from item_data
    manual_materials_data = item_data.get('materials', {}) if item_data else {}
    
    # FIX: Iterate over objects, not material names (obj_dict keys are object names, not material names)
    for obj_key, obj in obj_dict.items():
        if obj.type != 'MESH':
            continue
            
        # Check all material slots on this object
        log(f"    🔍 Object '{obj.name}' has {len(obj.data.materials)} material slots: {[m.name for m in obj.data.materials if m]}")
        for mat_slot_idx, mat_slot in enumerate(obj.data.materials):
            if not mat_slot:
                continue
                
            # Get the actual material name from the material slot
            actual_mat_name = mat_slot.name
            
            # Init report entry
            report_entry = {
                "context_id": context_name,
                "object_name": obj.name,
                "slot_index": mat_slot_idx,
                "original_material": actual_mat_name,
                "status": "skipped",
                "match_type": None,
                "is_color_edited": False,
                "applied_color": None,
                "applied_texture": None,
                "details": ""
            }

            # 1. Try Exact Match
            manual_mat_data = manual_materials_data.get(actual_mat_name)
            match_type = "exact" if manual_mat_data else None
            
            # 2. Try Prefix Match (Fix for 'wire_028149177.001' vs 'wire_028149177')
            if not manual_mat_data:
                # Check if the Blender material name starts with any key in the JSON
                # e.g., "wire_028149177.001" starts with "wire_028149177"
                for key, data in manual_materials_data.items():
                    if actual_mat_name.startswith(key) and len(key) > 0:
                        manual_mat_data = data
                        match_type = f"prefix_match_key_{key}"
                        log(f"    🔄 Matched material '{actual_mat_name}' to JSON key '{key}'")
                        break
            
            # 3. Try removing suffixes like .001, .002 etc.
            if not manual_mat_data:
                base_name = re.sub(r'\.\d+$', '', actual_mat_name)
                if base_name != actual_mat_name:
                    manual_mat_data = manual_materials_data.get(base_name)
                    if manual_mat_data:
                        match_type = f"suffix_removed_key_{base_name}"
                        log(f"    🔄 Matched material '{actual_mat_name}' to JSON key '{base_name}' (removed suffix)")
            
            # 4. Try case-insensitive matching
            if not manual_mat_data:
                for key, data in manual_materials_data.items():
                    if key.lower() == actual_mat_name.lower():
                        manual_mat_data = data
                        match_type = f"case_insensitive_key_{key}"
                        log(f"    🔄 Matched material '{actual_mat_name}' to JSON key '{key}' (case-insensitive)")
                        break
            
            # 5. [NEW] Try Object Name matching (Structure Fallback)
            # If the JSON key identifies the OBJECT (e.g. "goi", "pillow") rather than the MATERIAL ("Fabric")
            if not manual_mat_data:
                obj_name_lower = obj.name.lower()
                # Sort keys by length (descending) to match specific names before generic ones
                sorted_keys = sorted(manual_materials_data.keys(), key=len, reverse=True)
                for key in sorted_keys:
                    if len(key) < 3: continue # Skip very short keys to avoid false positives
                    if key.lower() in obj_name_lower:
                        manual_mat_data = manual_materials_data[key]
                        match_type = f"object_name_match_key_{key}"
                        log(f"    🔄 Matched object '{obj.name}' (material '{actual_mat_name}') to JSON key '{key}'")
                        break

            if manual_mat_data:
                report_entry["match_type"] = match_type
                # Ensure isColorEdited is treated as boolean, handling string "true"/"false" from JSON
                raw_ice = manual_mat_data.get('isColorEdited', False)
                is_color_edited = str(raw_ice).lower() == 'true' if isinstance(raw_ice, str) else bool(raw_ice)
                report_entry["is_color_edited"] = is_color_edited
                
                # Update manual data with corrected boolean to ensure downstream logic works
                manual_mat_data['isColorEdited'] = is_color_edited

            if manual_mat_data and has_manual_changes(manual_mat_data):
                # Apply manual material changes (HIGHEST PRIORITY)
                blender_mat = create_material_from_manual_changes(
                    f"mat_{context_name}_{actual_mat_name}_manual", 
                    manual_mat_data, 
                    search_dirs
                )
                # FIX: REPLACE the material slot instead of appending
                obj.data.materials[mat_slot_idx] = blender_mat
                
                # Log what was applied
                color_info = ""
                if manual_mat_data.get('color'):
                    color_info = f"color: {manual_mat_data['color']}"
                    report_entry["applied_color"] = manual_mat_data['color']
                elif manual_mat_data.get('mapUrl') or manual_mat_data.get('map'):
                    map_path = manual_mat_data.get('mapUrl') or manual_mat_data.get('map')
                    color_info = f"texture: {os.path.basename(map_path)}"
                    report_entry["applied_texture"] = os.path.basename(map_path) if map_path else None
                
                report_entry["status"] = "applied_manual"
                report_entry["details"] = color_info
                log(f"    🎨 Applied MANUAL material '{blender_mat.name}' to '{obj.name}' slot {mat_slot_idx} ({color_info})")
                
            else:
                # Fall back to MTL data only if no manual changes were found
                mat_info = mtl_data.get(actual_mat_name)
                if mat_info:
                    blender_mat = create_material_from_paths(
                        f"mat_{context_name}_{actual_mat_name}", 
                        mat_info, 
                        is_wall_or_floor=False, 
                        search_dirs=search_dirs
                    )
                    # FIX: REPLACE the material slot instead of appending
                    obj.data.materials[mat_slot_idx] = blender_mat
                    report_entry["status"] = "applied_downloaded"
                    report_entry["details"] = "from MTL"
                    log(f"    📥 Applied DOWNLOADED material '{blender_mat.name}' to object '{obj.name}' slot {mat_slot_idx} from MTL data.")
                else:
                    report_entry["status"] = "no_change"
                    report_entry["details"] = "no manual data or MTL found"

            # Add to global report
            ASSET_PROCESSING_REPORT.append(report_entry)

########################################
# OBJ loader & Advanced MTL parsing
########################################
def load_glb_manual(glb_path, name_prefix="ImportedGLB"):
    """
    Imports a GLB file into Blender and returns a dictionary of imported objects.
    GLB files contain meshes, materials, and textures all in one file.
    Handles GLB files with hierarchies properly to avoid duplicates.
    """
    if not os.path.exists(glb_path):
        log(f"  ❌ GLB file not found: {glb_path}")
        return {}
    
    try:
        # Store current object data blocks to identify newly imported objects
        # Use data blocks (meshes) instead of objects to avoid hierarchy issues
        meshes_before = set(bpy.data.meshes)
        
        # Import GLB file using Blender's glTF importer
        # The importer will create objects with their materials already applied
        bpy.ops.import_scene.gltf(filepath=glb_path)
        
        # Find newly imported meshes by comparing data blocks
        meshes_after = set(bpy.data.meshes)
        newly_imported_meshes = meshes_after - meshes_before
        
            # Find all objects that use these new meshes
        imported_objects = {}
        processed_meshes = set()
        processed_objects = set()  # Track objects to avoid duplicates
        
        for mesh in newly_imported_meshes:
            if mesh in processed_meshes:
                continue
            processed_meshes.add(mesh)
            
            # Find all objects using this mesh (check all collections, not just scene root)
            for obj in bpy.data.objects:
                if obj.type == 'MESH' and obj.data == mesh and obj not in processed_objects:
                    processed_objects.add(obj)
                    
                    # Rename with prefix to avoid conflicts
                    original_name = obj.name
                    obj.name = f"{name_prefix}_{original_name}"
                    
                    # Use object name as key (ensure uniqueness)
                    key = obj.name
                    counter = 1
                    while key in imported_objects:
                        key = f"{name_prefix}_{counter}"
                        counter += 1
                    
                    imported_objects[key] = obj
                    
                    # Apply smooth shading
                    for p in obj.data.polygons:
                        p.use_smooth = True
                    
                    # Apply auto smooth or weighted normal (same as OBJ import)
                    try:
                        if bpy.app.version < (4, 1, 0):
                            obj.data.use_auto_smooth = True
                            obj.data.auto_smooth_angle = math.radians(30)
                        else:
                            mod = obj.modifiers.new(name="WeightedNormal", type='WEIGHTED_NORMAL')
                            mod.keep_sharp = True
                    except Exception:
                        pass
        
        if len(imported_objects) > 0:
            log(f"  ✅ Imported GLB '{os.path.basename(glb_path)}' | created {len(imported_objects)} mesh object(s)")
            for key, obj in imported_objects.items():
                log(f"    - Object: {obj.name} (mesh: {obj.data.name})")
        else:
            log(f"  ⚠ GLB import '{os.path.basename(glb_path)}' returned no mesh objects")
        return imported_objects
    except Exception as e:
        log(f"  ❌ Error importing GLB {glb_path}: {e}")
        import traceback
        traceback.print_exc()
        return {}

def load_obj_manual(obj_path, name_prefix="ImportedObj"):
    if not os.path.exists(obj_path):
        log(f"  ❌ OBJ file not found: {obj_path}"); return {}
    verts, uvs, normals, faces_by_material = [], [], [], {'default': []}
    current_material = 'default'
    try:
        with open(obj_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                parts = line.strip().split()
                if not parts: continue
                if parts[0] == 'v': verts.append(tuple(map(float, parts[1:4])))
                elif parts[0] == 'vt': uvs.append(tuple(map(float, parts[1:3])))
                elif parts[0] == 'vn': normals.append(tuple(map(float, parts[1:4])))
                elif parts[0] == 'usemtl':
                    current_material = parts[1]
                    if current_material not in faces_by_material: faces_by_material[current_material] = []
                elif parts[0] == 'f':
                    face = []
                    for p in parts[1:]:
                        indices = p.split('/'); indices.extend(['0'] * (3 - len(indices)))
                        try: face.append(tuple(int(i) if i else 0 for i in indices))
                        except: face.append((0,0,0))
                    faces_by_material.setdefault(current_material, []).append(face)
    except Exception as e:
        log(f"  ❌ Error reading OBJ {obj_path}: {e}"); return {}

    created_objects = {}
    for mat_name, faces in faces_by_material.items():
        if not faces: continue
        mesh_verts, mesh_faces, vert_map, mesh_uvs = [], [], {}, []
        for face in faces:
            for i in range(1, len(face) - 1):
                tri = (face[0], face[i], face[i+1])
                tri_indices = []
                for v_idx, vt_idx, vn_idx in tri:
                    key = (v_idx, vt_idx, vn_idx)
                    if key not in vert_map:
                        if v_idx <= 0 or v_idx > len(verts): continue
                        vert_map[key] = len(mesh_verts)
                        v = verts[v_idx - 1]
                        mesh_verts.append((v[0], -v[2], v[1]))
                        mesh_uvs.append(uvs[vt_idx - 1] if vt_idx and 1 <= vt_idx <= len(uvs) else None)
                    tri_indices.append(vert_map[key])
                if len(tri_indices) == 3: mesh_faces.append(tri_indices)

        if not mesh_verts: continue
        mesh = bpy.data.meshes.new(name=f"{name_prefix}_{mat_name}")
        mesh.from_pydata(mesh_verts, [], mesh_faces)
        mesh.update()
        
        # --- FIX: Set Polygons to Smooth to remove faceting lines ---
        for p in mesh.polygons:
            p.use_smooth = True
            
        if any(u is not None for u in mesh_uvs):
            try:
                uv_layer = mesh.uv_layers.new(name='UVMap')
                for i, loop in enumerate(mesh.loops):
                    uv = mesh_uvs[loop.vertex_index] if loop.vertex_index < len(mesh_uvs) else None
                    if uv: uv_layer.data[i].uv = (uv[0], uv[1])
            except Exception as e:
                log(f"  ❌ Could not create UV layer for '{mesh.name}': {e}")
                
        obj = bpy.data.objects.new(name=mesh.name, object_data=mesh)
        bpy.context.collection.objects.link(obj)

        # --- FIX: Apply Auto Smooth Logic ---
        # This keeps round things round but keeps sharp edges (90 deg) sharp
        try:
            # For Blender versions < 4.1
            if bpy.app.version < (4, 1, 0):
                mesh.use_auto_smooth = True
                mesh.auto_smooth_angle = math.radians(30)
            else:
                # For Blender 4.1+ (where use_auto_smooth is removed), 
                # we add a Weighted Normal modifier which fixes shading artifacts excellently.
                mod = obj.modifiers.new(name="WeightedNormal", type='WEIGHTED_NORMAL')
                mod.keep_sharp = True
        except Exception:
            pass # Fallback if something fails, though unlikely

        created_objects[mat_name] = obj
    log(f"  ✅ Imported '{obj_path}' | created {len(created_objects)} mesh object(s)")
    return created_objects


def parse_mtl(mtl_path, search_dirs=None):
    materials = {}
    current_mat_name = None
    if search_dirs is None:
        search_dirs = [os.path.dirname(mtl_path) if mtl_path else os.getcwd(), os.path.join(os.getcwd(), "asset_downloads"), os.getcwd()]
    if not os.path.exists(mtl_path):
        log(f"  ❌ MTL file not found: {mtl_path}"); return {}

    texture_map_keys = ['map_Kd', 'map_Ks', 'map_Ka', 'map_Bump', 'bump', 'map_d', 'map_Pr', 'map_Pm', 'map_Ke', 'map_bump', 'map_disp', 'map_Disp']

    try:
        with open(mtl_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                parts = line.split()
                key = parts[0]
                if key == 'newmtl':
                    current_mat_name = parts[1]
                    materials[current_mat_name] = {'maps': {}, 'props': {}, 'textures': []}
                    continue
                if not current_mat_name: continue
                mat_entry = materials[current_mat_name]

                if key in ('Kd', 'Ks', 'Ka', 'Ke'):
                    try: mat_entry['props'][key] = tuple(float(x) for x in parts[1:4])
                    except: pass
                elif key in ('Ns', 'd', 'Tr', 'Ni', 'metallic', 'roughness', 'specular'):
                    try: mat_entry['props'][key.lower()] = float(parts[1])
                    except: pass
                elif key in texture_map_keys or key.startswith('map_') or key in ('bump', 'disp'):
                    tokens = parts[1:]; flags = {}; filename = None
                    i = 0
                    while i < len(tokens):
                        t = tokens[i]
                        if t == '-s' and i+2 < len(tokens):
                            try: flags['scale'] = (float(tokens[i+1]), float(tokens[i+2])); i += 3; continue
                            except: pass
                        elif t == '-o' and i+2 < len(tokens):
                            try: flags['offset'] = (float(tokens[i+1]), float(tokens[i+2])); i += 3; continue
                            except: pass
                        elif not t.startswith('-'): filename = t
                        i += 1

                    if filename:
                        filename = filename.strip('\"\'')
                        resolved = resolve_texture_path(filename, search_dirs)
                        channel_name = key.replace('map_', '').replace('Map_', '').strip()
                        map_data = {'path': filename, 'resolved': resolved, **flags}
                        mat_entry['maps'].setdefault(channel_name, []).append(map_data)
                        if resolved: mat_entry['textures'].append(resolved)
    except Exception as e:
        log(f"  ❌ Error parsing MTL {mtl_path}: {e}")
    return materials

########################################
# Ceiling creation with Manual Change Support
########################################

def create_central_light_for_area(area_id, area, vertices, ceiling_height=2.7):
    """
    Creates a central ceiling light for an area with 10cm gap from ceiling.
    """
    try:
        log(f"  Creating central light for area: {area_id}")
        
        scaled_area_verts = [{'x': vertices[vid]['x'] * 0.01, 'y': vertices[vid]['y'] * 0.01} for vid in area['vertices']]
        
        xs = [v['x'] for v in scaled_area_verts]
        ys = [v['y'] for v in scaled_area_verts]
        
        center_x = sum(xs) / len(xs)
        center_y = sum(ys) / len(ys)
        
        width = max(xs) - min(xs)
        depth = max(ys) - min(ys)
        
        light_height = ceiling_height - 0.70
        
        light_data = bpy.data.lights.new(name=f"CentralLight_{area_id}", type='AREA')
        light_data.energy = 500
        light_data.color = (1.0, 1.0, 1.0)
        light_data.size = 2.0
        
        light_obj = bpy.data.objects.new(name=f"CentralLight_{area_id}", object_data=light_data)
        bpy.context.collection.objects.link(light_obj)
        
        light_obj.location = (center_x, center_y, light_height)
        light_obj.rotation_euler = (0, 0, 0)
        
        log(f"  ✅ Created central light for area '{area_id}'")
        
        return light_obj
        
    except Exception as e:
        log(f"  ❌ Error creating central light for area '{area_id}': {e}")
        return None

def create_ceiling_for_area(area_id, area, vertices, search_dirs, ceiling_height=2.7, layer_altitude=0.0, is_top_floor=True):
    """
    Creates a ceiling for an area with manual change support.
    FIX: Creates a 3D solid ceiling to prevent light leaking.
    """
    try:
        # Calculate absolute ceiling height including layer altitude
        absolute_ceiling_height = layer_altitude + ceiling_height
        log(f"  Creating ceiling for area: {area_id} (Abs Height: {absolute_ceiling_height:.2f}m, Top Floor: {is_top_floor})")
        
        scaled_area_verts = [{'x': vertices[vid]['x'] * 0.01, 'y': vertices[vid]['y'] * 0.01} for vid in area['vertices']]
        
        # Create ceiling material with manual change support
        ceiling_mat = create_floor_ceiling_material_with_manual_support(
            area_id, area, 'ceiling', search_dirs
        )
        
        if not ceiling_mat:
            # Fallback to white material
            ceiling_mat = bpy.data.materials.new(name=f"mat_{area_id}_ceiling_fallback")
            ceiling_mat.use_nodes = True
            nodes = ceiling_mat.node_tree.nodes
            links = ceiling_mat.node_tree.links
            for node in list(nodes): nodes.remove(node)
            output = nodes.new('ShaderNodeOutputMaterial')
            bsdf = nodes.new('ShaderNodeBsdfPrincipled')
            links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
            bsdf.inputs['Base Color'].default_value = (1.0, 1.0, 1.0, 1.0)
        
        mesh = bpy.data.meshes.new(f"Ceiling_{area_id}")
        obj = bpy.data.objects.new(f"Ceiling_{area_id}", mesh)
        bpy.context.collection.objects.link(obj)
        
        # --- FIX: Create 3D Solid Ceiling ---
        thickness = 0.15  # 15cm thickness for better light blocking
        
        verts = []
        for v in scaled_area_verts:
            # Bottom face (visible) - place at exact ceiling height
            verts.append((v['x'], v['y'], absolute_ceiling_height))
            # Top face (roof)
            verts.append((v['x'], v['y'], absolute_ceiling_height + thickness))
            
        bm = bmesh.new()
        for v in verts:
            bm.verts.new(v)
        bm.verts.ensure_lookup_table()
        
        n = len(scaled_area_verts)
        v_seq = list(bm.verts) # Robust indexing
        
        # Bottom face - Points DOWN
        bm.faces.new([v_seq[i*2] for i in range(n)][::-1]) 
        # Top face - Points UP
        bm.faces.new([v_seq[i*2+1] for i in range(n)]) 
        
        # Side faces - Points OUT
        for i in range(n):
            v1, v2 = v_seq[i*2], v_seq[((i+1)%n)*2]
            v3, v4 = v_seq[((i+1)%n)*2+1], v_seq[i*2+1]
            bm.faces.new([v1, v2, v3, v4])
            
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            
        # IMPROVED UV MAPPING: Use Metric World Scale
        try: 
            uv_layer = bm.loops.layers.uv.new("UVMap")
        except: 
            uv_layer = bm.loops.layers.uv.active
        
        # Get texture scaling from properties
        surf_props = area.get('ceiling_properties', {})
        tex_scale_u = surf_props.get('mapScaleU', surf_props.get('texture_scale_x', 1.0))
        tex_scale_v = surf_props.get('mapScaleV', surf_props.get('texture_scale_y', 1.0))
        
        # Apply metric mapping
        for face in bm.faces:
            for loop in face.loops:
                u = loop.vert.co.x * tex_scale_u
                v = loop.vert.co.y * tex_scale_v
                loop[uv_layer].uv = (u, v)
        
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
        
        if ceiling_mat:
            obj.data.materials.append(ceiling_mat)
        
        light_obj = create_central_light_for_area(area_id, area, vertices, absolute_ceiling_height)
        
        log(f"  ✅ Created 3D ceiling for area '{area_id}' at height {absolute_ceiling_height}")

        # --- STEP: CREATE UMBRELLA ROOF (LIGHT SHIELD) ---
        shield_obj = None
        if is_top_floor:
            try:
                log(f"  ☂️ Creating Umbrella Roof (Light Shield) for area: {area_id}")
                shield_mesh = bpy.data.meshes.new(f"UmbrellaShield_{area_id}")
                shield_obj = bpy.data.objects.new(f"UmbrellaShield_{area_id}", shield_mesh)
                bpy.context.collection.objects.link(shield_obj)
                
                # Calculate oversized bounding box for the "umbrella"
                xs = [v['x'] for v in scaled_area_verts]
                ys = [v['y'] for v in scaled_area_verts]
                margin = 1.0 # 1.0 meter oversized margin in all directions
                min_x, max_x = min(xs) - margin, max(xs) + margin
                min_y, max_y = min(ys) - margin, max(ys) + margin
                
                shield_thickness = 0.20 # 20cm thick light shield
                shield_z_low = absolute_ceiling_height + thickness + 0.05 # 5cm gap above main ceiling
                shield_z_high = shield_z_low + shield_thickness
                
                # Create a simple box shield
                shield_verts = [
                    (min_x, min_y, shield_z_low), (max_x, min_y, shield_z_low),
                    (max_x, max_y, shield_z_low), (min_x, max_y, shield_z_low),
                    (min_x, min_y, shield_z_high), (max_x, min_y, shield_z_high),
                    (max_x, max_y, shield_z_high), (min_x, max_y, shield_z_high)
                ]
                
                bm_shield = bmesh.new()
                for v in shield_verts: bm_shield.verts.new(v)
                v_seq = list(bm_shield.verts) # Robust indexing
                
                # Bottom face (Down)
                bm_shield.faces.new([v_seq[0], v_seq[1], v_seq[2], v_seq[3]][::-1])
                # Top face (Up)
                bm_shield.faces.new([v_seq[4], v_seq[5], v_seq[6], v_seq[7]])
                # Side faces (Out)
                for i in range(4):
                    v_low1, v_low2 = v_seq[i], v_seq[(i+1)%4]
                    v_high2, v_high1 = v_seq[(i+1)%4+4], v_seq[i+4]
                    bm_shield.faces.new([v_low1, v_low2, v_high2, v_high1])
                    
                bmesh.ops.recalc_face_normals(bm_shield, faces=bm_shield.faces)
                    
                bm_shield.to_mesh(shield_mesh)
                bm_shield.free()
                
                # Dark matte material for the shield (block everything)
                shield_mat = bpy.data.materials.new(name=f"mat_shield_{area_id}")
                shield_mat.use_nodes = True
                shield_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.01, 0.01, 0.01, 1.0)
                shield_obj.data.materials.append(shield_mat)
                
                log(f"  ✅ Umbrella Roof Created: {min_x:.1f} to {max_x:.1f}, {min_y:.1f} to {max_y:.1f}")
                
            except Exception as e_shield:
                log(f"  ⚠️ Could not create Umbrella Roof: {e_shield}")
        else:
             log(f"  ☂️ Skipping Umbrella Roof (Not Top Floor) for area: {area_id}")

        return obj, light_obj, shield_obj
        
    except Exception as e:
        log(f"  ❌ Error creating ceiling for area '{area_id}': {e}")
        return None, None, None

def create_all_ceilings(areas, vertices, search_dirs, layer_altitude=0.0, is_top_floor=True):
    """
    Creates ceilings for all areas in the floor plan with central lights.
    """
    log(f"Creating ceilings for all areas (Altitude: {layer_altitude:.2f}m, Is Top Floor: {is_top_floor})...")
    ceiling_objects = []
    light_objects = []
    shield_objects = []
    
    for area_id, area in areas.items():
        ceiling_obj, light_obj, shield_obj = create_ceiling_for_area(area_id, area, vertices, search_dirs, layer_altitude=layer_altitude, is_top_floor=is_top_floor)
        if ceiling_obj:
            ceiling_objects.append(ceiling_obj)
        if light_obj:
            light_objects.append(light_obj)
        if shield_obj:
            shield_objects.append(shield_obj)
    
    log(f"✅ Created {len(ceiling_objects)} ceiling(s) with {len(light_objects)} central light(s) and {len(shield_objects)} umbrella shield(s)")
    return ceiling_objects, light_objects, shield_objects

########################################
# Lighting functions
########################################

def create_sunlight():
    """
    Creates a sunlight source for realistic outdoor illumination.
    """
    try:
        log("Creating sunlight source...")
        
        light_data = bpy.data.lights.new(name="Sunlight", type='SUN')
        light_data.energy = 5.0
        light_data.color = (1.0, 0.95, 0.9)
        
        light_obj = bpy.data.objects.new(name="Sunlight", object_data=light_data)
        bpy.context.collection.objects.link(light_obj)
        
        light_obj.location = (10.0, 10.0, 15.0)
        light_obj.rotation_euler = (math.radians(45), 0, math.radians(45))
        
        log("  ✅ Created sunlight source")
        return light_obj
        
    except Exception as e:
        log(f"  ❌ Error creating sunlight: {e}")
        return None

def create_180w_central_light():
    """
    Creates a powerful 180W central light for overall scene illumination.
    """
    try:
        log("Creating 180W central light...")
        
        scene_center = Vector((0, 0, 0))
        scene_size = 10.0
        
        try:
            min_coord = Vector((float('inf'), float('inf'), float('inf')))
            max_coord = Vector((float('-inf'), float('-inf'), float('-inf')))
            
            for obj in bpy.context.scene.objects:
                if obj.type == 'MESH':
                    world_vertices = [obj.matrix_world @ Vector(v) for v in obj.bound_box]
                    for v in world_vertices:
                        min_coord.x = min(min_coord.x, v.x)
                        min_coord.y = min(min_coord.y, v.y)
                        min_coord.z = min(min_coord.z, v.z)
                        max_coord.x = max(max_coord.x, v.x)
                        max_coord.y = max(max_coord.y, v.y)
                        max_coord.z = max(max_coord.z, v.z)
            
            if min_coord.x != float('inf'):
                scene_center = (min_coord + max_coord) / 2
                scene_size = max((max_coord - min_coord).length(), 10.0)
        except:
            pass
        
        light_height = scene_center.z + scene_size * 0.8
        
        light_data = bpy.data.lights.new(name="CentralLight_180W", type='POINT')
        light_data.energy = 180
        light_data.color = (1.0, 1.0, 1.0)
        light_data.shadow_soft_size = 2.0
        
        light_obj = bpy.data.objects.new(name="CentralLight_180W", object_data=light_data)
        bpy.context.collection.objects.link(light_obj)
        
        light_obj.location = (scene_center.x, scene_center.y, light_height)
        
        log("  ✅ Created 180W central light")
        return light_obj
        
    except Exception as e:
        log(f"  ❌ Error creating 180W central light: {e}")
        return None

########################################
# Global texture inference for untextured materials
########################################
ROLE_PATTERNS = {
    "basecolor": ["_diff", "_color", "_basecolor", "basecolor", "_albedo", "albedo", "diff"],
    "normal": ["_normalgl", "_normaldx", "_normal", "_nrm"],
    "roughness": ["_roughness", "_rough"],
    "metallic": ["_metallic", "_metal"],
    "ao": ["_ao", "ambientocclusion", "_ao_"],
    "displacement": ["_disp", "_displacement", "_height"]
}

def build_asset_index(root):
    index = {}
    if not os.path.isdir(root):
        return index
    for dirpath, dirs, files in os.walk(root):
        for f in files:
            name_noext = os.path.splitext(f.lower())[0]
            index.setdefault(name_noext, []).append(os.path.join(dirpath, f))
    return index

def find_texture_by_role(index, mat_name, role):
    mat_key = re.sub(r'[^a-z0-9]', '_', mat_name.lower())
    patterns = ROLE_PATTERNS.get(role, [])
    for p in patterns:
        candidate_key = mat_key + p
        if candidate_key in index: return index[candidate_key][0]
    for key, paths in index.items():
        if mat_key in key:
            for p in patterns:
                if p in key: return paths[0]
    return None

def assign_missing_textures_global(search_dirs):
    assets_dir = os.path.join(os.getcwd(), "asset_downloads")
    if assets_dir not in search_dirs: search_dirs.append(assets_dir)
    index = build_asset_index(assets_dir)
    log(f"Built global asset index with {len(index)} keys from: {assets_dir}")

    for mat in list(bpy.data.materials):
        if report_material_textures(mat): continue

        log(f"Attempting global texture inference for material '{mat.name}'")
        candidates = {
            "basecolor": find_texture_by_role(index, mat.name, "basecolor"),
            "normal": find_texture_by_role(index, mat.name, "normal"),
            "roughness": find_texture_by_role(index, mat.name, "roughness"),
            "metallic": find_texture_by_role(index, mat.name, "metallic"),
            "ao": find_texture_by_role(index, mat.name, "ao"),
            "displacement": find_texture_by_role(index, mat.name, "displacement"),
        }
        texture_paths = [p for p in candidates.values() if p]

        if not texture_paths:
            log(f"  ↧ No candidate textures found for '{mat.name}' in global index.")
            continue

        new_mat = create_material_from_paths(f"{mat.name}_inferred", texture_paths, is_wall_or_floor=False, search_dirs=search_dirs)

        for obj in bpy.context.scene.objects:
            if obj.type == 'MESH':
                for i, m in enumerate(obj.data.materials):
                    if m == mat:
                        obj.data.materials[i] = new_mat

        try: bpy.data.materials.remove(mat)
        except: pass
        log(f"  ✅ Replaced '{mat.name}' with inferred material '{new_mat.name}' using {len(texture_paths)} textures.")

########################################
# Main conversion flow with COMPLETE Manual Change Priority
########################################
def main():
    try:
        args = sys.argv[sys.argv.index("--") + 1:]
        input_json_path, output_glb_path = args[0], args[1]
    except (ValueError, IndexError):
        print("Usage: blender --background --python blender_converter.py -- <input.json> <output.glb>")
        sys.exit(1)

    log(f"Blender converter started. Input JSON: {input_json_path} -> Output GLB: {output_glb_path}")

    with open(input_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Global Setup
    clear_scene()
    SCALE = 0.01
    asset_download_dir = os.path.join(os.getcwd(), "asset_downloads")
    search_dirs = [asset_download_dir, os.getcwd()]

    # --- ROBUST DATA ACCESS LOGIC ---
    floor_plan_data = None
    if 'floor_plan_data' in data:
        log("Found 'floor_plan_data' key. Parsing its content.")
        floor_plan_data_str = data.get('floor_plan_data')
        try:
            floor_plan_data = json.loads(floor_plan_data_str)
        except (json.JSONDecodeError, TypeError):
            # Fallback: if it's already a dict (not stringified)
            if isinstance(floor_plan_data_str, dict):
                floor_plan_data = floor_plan_data_str
            else:
                log(f"Error: Could not parse the content of 'floor_plan_data'. Value was: {floor_plan_data_str}")
                sys.exit(1)
    elif 'layers' in data and 'unit' in data:
        log("Found 'layers' key at the root. Assuming this is the floor plan data.")
        floor_plan_data = data
    else:
        log("Error: Could not find 'floor_plan_data' or a valid 'layers' structure in the input JSON.")
        sys.exit(1)

    # Get all layers
    layers_dict = floor_plan_data.get('layers', {})
    if not layers_dict:
        log("Error: No layers found in floor plan data.")
        sys.exit(1)

    # Sort layers by altitude (if available) to determine top floor
    # Convert dict values to list and sort
    all_layers = []
    for lid, l_data in layers_dict.items():
        l_data['id'] = lid # Ensure ID is present
        all_layers.append(l_data)
    
    # Sort by altitude (ascending)
    all_layers.sort(key=lambda x: get_dimension_value(x.get('altitude', 0), default=0))
    
    # Calculate max altitude to identify top floor(s)
    max_altitude = 0
    if all_layers:
        max_altitude = max(get_dimension_value(l.get('altitude', 0), default=0) for l in all_layers)
    
    # Global containers
    all_wall_objects = {}
    
    log(f"Found {len(all_layers)} layers. Max altitude: {max_altitude}")

    # --- FILTER SELECTED FLOOR ---
    # Use showAllFloors / selectedLayer from floor_plan_data to determine which layers to render.
    show_all_floors = floor_plan_data.get('showAllFloors', True)
    selected_layer_id = floor_plan_data.get('selectedLayer', None)

    render_layers = []
    if show_all_floors:
        # showAllFloors is true -> render all layers
        render_layers = all_layers
        log(f"⚡ 'showAllFloors' is True. Rendering all {len(render_layers)} layer(s).")
    else:
        # showAllFloors is false -> check selectedLayer
        if selected_layer_id:
            render_layers = [l for l in all_layers if l.get('id') == selected_layer_id]
            if render_layers:
                log(f"⚡ 'showAllFloors' is False. Rendering only selected layer: '{selected_layer_id}'.")
            else:
                log(f"⚠️ selectedLayer '{selected_layer_id}' not found in layers. Falling back to all layers.")
                render_layers = all_layers
        else:
            log(f"⚠️ 'showAllFloors' is False but no 'selectedLayer' specified. Falling back to all layers.")
            render_layers = all_layers

    # Calculate global altitude offset to normalize selected floor to ground level (Z=0)
    global_altitude_offset_cm = 0
    if len(render_layers) < len(all_layers) and render_layers:
        # Single floor selected: shift it down to ground level
        global_altitude_offset_cm = min(get_dimension_value(l.get('altitude', 0), default=0) for l in render_layers)
        log(f"⚡ Normalization: Shifting all elements down by {global_altitude_offset_cm}cm to place selected floor(s) at ground level.")

    # --- MAIN GENERATION LOOP PER LAYER ---
    for layer in render_layers:
        layer_id = layer.get('id', 'unknown')
        # Calculate Layer Altitude in Meters (Apply Normalization)
        raw_altitude_cm = get_dimension_value(layer.get('altitude', 0), default=0)
        layer_altitude_cm = raw_altitude_cm - global_altitude_offset_cm
        layer_altitude = layer_altitude_cm * SCALE
        
        is_top_floor = (raw_altitude_cm >= max_altitude)
        
        log(f"\n{'='*60}")
        log(f"🏗️ PROCESSING LAYER: {layer_id} (Altitude: {layer_altitude:.2f}m, Top: {is_top_floor})")
        log(f"{'='*60}")
        
        vertices = layer.get('vertices') or {}
        lines = layer.get('lines') or {}
        areas = layer.get('areas') or {}
        items = layer.get('items') or {}
        holes = layer.get('holes') or {}
        log(f"  Vertices: {len(vertices)}, Lines: {len(lines)}, Areas: {len(areas)}, Items: {len(items)}, Holes: {len(holes)}")
        log(f"  Layer Keys: {list(layer.keys())}")
        if len(items) > 0:
            log(f"  Items found in layer {layer_id}: {list(items.keys())}")
        else:
            log(f"  ⚠️ No items found in layer {layer_id}")

        # --- STEP 1: Pre-create all wall materials for this layer ---
        log(f"  Creating wall materials for layer {layer_id}...")
        materials_map = {}
        for line_id, line in lines.items():
            try:
                line_materials = create_wall_material_with_manual_support(line_id, line, search_dirs)
                materials_map.update(line_materials)
            except Exception as e:
                log(f"    ❌ Could not create material for wall '{line_id}': {e}")

        # --- STEP 2: Build all walls and cut all holes using the advanced builder ---
        # Pass layer_altitude to build walls at correct height
        layer_walls = wall_builder.build_architecture(
            lines, vertices, holes, areas, materials_map, SCALE, base_altitude=layer_altitude
        )
        all_wall_objects.update(layer_walls)

        # --- STEP 3: Create Floors ---
        log(f"  Creating floors for layer {layer_id}...")
        for area_id, area in areas.items():
            try:
                scaled_area_verts = [{'x': vertices[vid]['x'] * SCALE, 'y': vertices[vid]['y'] * SCALE} for vid in area['vertices']]
                
                # Create floor material with manual change support
                floor_mat = create_floor_ceiling_material_with_manual_support(
                    area_id, area, 'floor', search_dirs
                )
                
                if not floor_mat:
                    # Fallback material
                    floor_mat = bpy.data.materials.new(name=f"mat_{area_id}_floor_fallback")
                    floor_mat.use_nodes = True
                    nodes = floor_mat.node_tree.nodes
                    links = floor_mat.node_tree.links
                    for node in list(nodes): nodes.remove(node)
                    output = nodes.new('ShaderNodeOutputMaterial')
                    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
                    links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
                    bsdf.inputs['Base Color'].default_value = (0.8, 0.8, 0.8, 1.0)
                
                # Create mesh manually with proper UVs
                # Pass layer_altitude as z_offset
                create_polygon_mesh(f"Floor_{area_id}", scaled_area_verts, floor_mat, layer_altitude, area.get('floor_properties', {}))
            except Exception as e: 
                log(f"    ❌ Error creating floor '{area_id}': {e}")

        # --- STEP 4: Create Ceilings ---
        log(f"  Creating ceilings for layer {layer_id}...")
        create_all_ceilings(areas, vertices, search_dirs, layer_altitude=layer_altitude, is_top_floor=is_top_floor)

        # --- STEP 7: Place Hole Assets (Doors/Windows) ---
        log(f"  🚪 Placing hole assets for layer {layer_id}...")
        for idx, (hole_id, hole) in enumerate(holes.items(), 1):
            hole_type = hole.get('type', 'Unknown')
            hole_name = hole.get('name', 'Unknown')
            
            try:
                line_id = hole.get('line')
                if line_id not in layer_walls:
                    log(f"    ⚠️  Skipping hole '{hole_id}': Wall '{line_id}' not found in current layer")
                    continue

                asset_urls = hole.get('asset_urls', {});
                glb_path = asset_urls.get('GLB_File_URL');
                obj_path = asset_urls.get('OBJ_File_URL');
                mtl_path = asset_urls.get('MTL_File_URL')
                
                # Prefer GLB over OBJ if available
                imported_objs = {}
                if glb_path and os.path.exists(glb_path):
                    # log(f"    → Using GLB asset for hole '{hole_id}': {os.path.basename(glb_path)}")
                    imported_objs = load_glb_manual(glb_path, name_prefix=hole_id)
                    
                    # Flatten GLB hierarchy for HOLES
                    all_mesh_objects = []
                    for obj in list(imported_objs.values()):
                        if obj.type == 'MESH': all_mesh_objects.append(obj)
                        all_mesh_objects.extend([c for c in obj.children_recursive if c.type == 'MESH'])
                    
                    # Process ALL mesh objects to apply transforms
                    for obj in all_mesh_objects:
                        bpy.context.view_layer.objects.active = obj
                        obj.select_set(True)
                        try:
                            # Bake rotation, scale AND location into the mesh data
                            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
                        except:
                            mesh = obj.data
                            matrix = obj.matrix_local
                            for vertex in mesh.vertices:
                                vertex.co = matrix @ vertex.co
                        
                        # Reset object transforms AFTER baking
                        obj.location = (0, 0, 0)
                        obj.rotation_euler = (0, 0, 0)
                        obj.scale = (1, 1, 1)
                        obj.select_set(False)

                    if imported_objs:
                        apply_materials_to_obj_with_manual_support(
                            imported_objs, {}, hole, search_dirs, context_name=hole_id
                        )
                elif obj_path and os.path.exists(obj_path):
                    # log(f"    → Using OBJ asset for hole '{hole_id}': {os.path.basename(obj_path)}")
                    imported_objs = load_obj_manual(obj_path, name_prefix=hole_id)
                    mtl_data = parse_mtl(mtl_path, search_dirs) if mtl_path and os.path.exists(mtl_path) else {}
                    
                    apply_materials_to_obj_with_manual_support(
                        imported_objs, mtl_data, hole, search_dirs, context_name=hole_id
                    )
                else:
                    # log(f"    ⚠ No valid asset found for hole '{hole_id}'")
                    continue

                if not imported_objs:
                    continue

                line_data = lines[line_id]
                v1, v2 = vertices[line_data['vertices'][0]], vertices[line_data['vertices'][1]]
                line_vec = Vector((v2['x'] - v1['x'], v2['y'] - v1['y'], 0))
                mid_point = Vector((v1['x'], v1['y'], 0)) + (line_vec * hole.get('offset', 0.5))
                wall_angle = math.atan2(line_vec.y, line_vec.x)

                parent = bpy.data.objects.new(f"{hole_id}_parent", None); bpy.context.collection.objects.link(parent)
                for obj in imported_objs.values(): obj.parent = parent

                props = hole.get('properties', {})
                raw_width = props.get('width', 100)
                raw_height = props.get('height', 100)
                # raw_altitude is usually relative to floor
                
                target_w = get_dimension_value(raw_width) * SCALE
                target_h = get_dimension_value(raw_height) * SCALE
                target_d = line_data['properties']['thickness']['length'] * SCALE
                
                min_c, max_c = get_hierarchy_bbox(parent); dims = max_c - min_c
                scale = Vector((target_w / dims.x if dims.x else 1, target_d / dims.y if dims.y else 1, target_h / dims.z if dims.z else 1))
                z_offset = min_c.z * scale.z # Offset to bring bottom of asset to 0

                asset_path = glb_path if glb_path else obj_path
                altitude_value_cm = get_dimension_value(props.get('altitude', 0), default=0)
                hole_altitude = altitude_value_cm * SCALE
                
                # FIXED: Add layer_altitude to total Z position
                absolute_z = layer_altitude + hole_altitude
                parent.location = (mid_point.x * SCALE, mid_point.y * SCALE, absolute_z - z_offset)
                
                hole_name_lower = ((hole_id or "") + " " + (hole.get('name', '') or "") + " " + (hole.get('type', '') or "") + " " + os.path.basename(asset_path or "")).lower()
                is_door = "door" in hole_name_lower
                is_window = "window" in hole_name_lower or "ventilator" in hole_name_lower
                
                if is_door or is_window:
                    parent.rotation_euler = (0, 0, wall_angle)
                else:
                    parent.rotation_euler = (0, 0, math.radians(180))
                parent.scale = scale
                # log(f"    ✅ Placed hole asset '{hole_id}'")
            except Exception as e:
                log(f"    ❌ Error placing asset for hole '{hole_id}': {e}")

        # --- STEP 8: Place Item Assets ---
        log(f"  📦 Placing item assets for layer {layer_id}...")
        for idx, (item_id, item) in enumerate(items.items(), 1):
            try:
                if not isinstance(item, dict): continue

                asset_urls = item.get('asset_urls', {})
                glb_path = asset_urls.get('GLB_File_URL')
                obj_path = asset_urls.get('OBJ_File_URL')
                mtl_path = asset_urls.get('MTL_File_URL')
                
                log(f"  GLB Path (from JSON): {glb_path if glb_path else 'None'}")
                log(f"  OBJ Path (from JSON): {obj_path if obj_path else 'None'}")
                
                # Try to resolve GLB path if it's relative or doesn't exist
                if glb_path:
                    if not os.path.exists(glb_path):
                        log(f"  🔍 GLB path doesn't exist at: {glb_path}")
                        log(f"     Trying to resolve in search directories...")
                        resolved_glb = resolve_texture_path(glb_path, search_dirs)
                        if resolved_glb and os.path.exists(resolved_glb):
                            log(f"  ✅ Resolved GLB to: {resolved_glb}")
                            glb_path = resolved_glb
                        else:
                            log(f"  ❌ Could not resolve GLB path. File not found in:")
                            for search_dir in search_dirs:
                                log(f"     - {search_dir}")
                            log(f"  ⚠ Will skip this item or try OBJ fallback if available")
                            glb_path = None  # Set to None so we can try OBJ fallback
                    else:
                        log(f"  ✅ GLB file found at: {glb_path}")
                
                log(f"  Final GLB Exists: {os.path.exists(glb_path) if glb_path else 'N/A'}")
                
                # Prefer GLB over OBJ if available
                imported_objs = {}
                if glb_path and os.path.exists(glb_path):
                    log(f"  → Using GLB asset for item '{item_id}': {os.path.basename(glb_path)}")
                    imported_objs = load_glb_manual(glb_path, name_prefix=item_id)
                    
                    # Flatten GLB hierarchy by applying all transforms to mesh data
                    # This ensures bounding box calculation uses actual geometry size
                    # IMPORTANT: Process ALL mesh objects, including nested children in the GLB hierarchy
                    all_mesh_objects = list(imported_objs.values())
                    
                    # Find all child mesh objects that might not be in imported_objs
                    for obj in list(imported_objs.values()):
                        # Get all children recursively
                        for child in obj.children_recursive:
                            if child.type == 'MESH' and child not in all_mesh_objects:
                                all_mesh_objects.append(child)
                    
                    # Process ALL mesh objects to apply transforms
                    for obj in all_mesh_objects:
                        # Make object active and select it
                        bpy.context.view_layer.objects.active = obj
                        obj.select_set(True)
                        
                        # Apply rotation, scale AND location to mesh data
                        # This bakes any GLB internal transforms into the mesh vertices
                        try:
                            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
                        except:
                            # If transform_apply fails, manually apply transforms to mesh
                            mesh = obj.data
                            matrix = obj.matrix_local
                            for vertex in mesh.vertices:
                                vertex.co = matrix @ vertex.co
                        
                        # Reset object transforms to identity
                        obj.location = (0, 0, 0)
                        obj.rotation_euler = (0, 0, 0)
                        obj.scale = (1, 1, 1)
                        obj.select_set(False)
                    
                    # GLB files already have materials, but we can still apply manual changes if needed
                    if imported_objs:
                        # Apply manual material changes if specified
                        apply_materials_to_obj_with_manual_support(
                            imported_objs, {}, item, search_dirs, context_name=item_id
                        )
                elif obj_path and os.path.exists(obj_path):
                    log(f"  → Using OBJ asset for item '{item_id}': {os.path.basename(obj_path)}")
                    imported_objs = load_obj_manual(obj_path, name_prefix=item_id)
                    mtl_data = parse_mtl(mtl_path, search_dirs) if mtl_path and os.path.exists(mtl_path) else {}
                    
                    # Apply materials with COMPLETE MANUAL CHANGE SUPPORT
                    apply_materials_to_obj_with_manual_support(
                        imported_objs, mtl_data, item, search_dirs, context_name=item_id
                    )
                else:
                    log(f"  ⚠ No valid asset found for item '{item_id}' (checked GLB and OBJ)")
                    continue
    
                if not imported_objs:
                    log(f"  ⚠ Failed to import assets for item '{item_id}'")
                    continue
    
                parent = bpy.data.objects.new(f"{item_id}_parent", None); bpy.context.collection.objects.link(parent)
                for obj in imported_objs.values(): obj.parent = parent
    
                props = item.get('properties', {})
                if not isinstance(props, dict):
                     log(f"  ❌ Item '{item_id}' has invalid properties format. Using default dimensions.")
                     props = {}
                
                # Get item type and name early for ceiling light detection
                item_type = item.get('type', '').lower()
                item_name = item.get('name', '').lower()
    
                # Extract dimensions with support for both array and object formats
                raw_width = props.get('width', 100)
                raw_depth = props.get('depth', 100)
                raw_height = props.get('height', 100)
                
                target_w = get_dimension_value(raw_width) * SCALE
                target_d = get_dimension_value(raw_depth) * SCALE
                target_h = get_dimension_value(raw_height) * SCALE
                
                # Check if dimensions are user-specified (not defaults)
                # Any non-default dimension is considered an explicit user choice
                # and should NOT be adjusted by automatic height limiting
                default_w = get_dimension_value(100) * SCALE
                default_h = get_dimension_value(100) * SCALE
                default_d = get_dimension_value(100) * SCALE
                
                is_user_resized = (abs(target_w - default_w) > 0.001 or 
                                  abs(target_h - default_h) > 0.001 or 
                                  abs(target_d - default_d) > 0.001)
                
                # Log dimension extraction for debugging
                log(f"  📏 Dimension extraction for '{item_id}':")
                log(f"     Raw width property: {raw_width} → {target_w/SCALE:.2f}cm")
                log(f"     Raw depth property: {raw_depth} → {target_d/SCALE:.2f}cm")
                log(f"     Raw height property: {raw_height} → {target_h/SCALE:.2f}cm")
                if is_user_resized:
                    log(f"     ✨ User-resized asset detected - will preserve exact dimensions")
                
                # Check if this is a ceiling-mounted light (use ALL meshes for bbox)
                is_ceiling_light = any(keyword in item_type or keyword in item_name for keyword in [
                    'hanging', 'chandelier', 'ceiling_light', 'ceilinglight', 'lamp', 'light'
                ]) and item.get('mounting_type', '').lower() == 'ceiling_mount'
                
                # Check if this is a decorative item that should include all meshes (flowerpot with flowers, curtains, etc.)
                # Curtains often have rod + fabric as separate meshes
                # Showers, bathtubs, and sinks often have separate faucets, handles, shower heads as decorative meshes
                # TVs have screen + bezel + mount as separate meshes
                is_decorative_with_parts = any(keyword in item_type.lower() or keyword in item_name.lower() for keyword in [
                    'flowerpot', 'flower_pot', 'plant', 'vase', 'curtain', 'drape', 
                    'shower', 'bathtub', 'bath_tub', 'sink', 'basin', 'toilet',
                    'tv', 'television', 'monitor',
                    'sofa', 'couch', 'chair', 'armchair', 'seat',
                    'table', 'desk', 'coffee_table', 'side_table', 'sidetable'
                ])
                
                # Check if this is a tall appliance (fridge, freezer) - these often have shelves/decorative elements
                # that shouldn't be filtered out during bounding box calculation
                is_tall_appliance_bbox = any(keyword in item_type.lower() or keyword in item_name.lower() for keyword in [
                    'fridge', 'refrigerat', 'freezer'
                ])
                
                # Include all meshes for ceiling lights, decorative items with multiple parts, and tall appliances
                include_all = is_ceiling_light or is_decorative_with_parts or is_tall_appliance_bbox
                min_c, max_c = get_hierarchy_bbox(parent, include_all_meshes=include_all)
                if include_all:
                    if is_ceiling_light:
                        log(f"  💡 Ceiling light detected - including all meshes for bounding box")
                    if is_decorative_with_parts:
                        log(f"  🌸 Multi-part item (decor/bathroom fixture) detected - including all meshes for bounding box")
                    if is_tall_appliance_bbox:
                        log(f"  🧊 Tall appliance detected - including all meshes for bounding box")
                dims = max_c - min_c
                
                # DEBUG: Log scaling calculation details
                log(f"  📐 Scaling calculation for '{item_id}':")
                log(f"     Target dimensions (from JSON): W={target_w:.4f}m, D={target_d:.4f}m, H={target_h:.4f}m")
                log(f"     Target dimensions (raw cm): W={props.get('width', {}).get('length', 100)}cm, D={props.get('depth', {}).get('length', 100)}cm, H={props.get('height', {}).get('length', 100)}cm")
                log(f"     GLB bounding box: W={dims.x:.4f}m, D={dims.y:.4f}m, H={dims.z:.4f}m")
                log(f"     GLB bounding box (raw): min={min_c}, max={max_c}")
                scale_x = target_w / dims.x if dims.x else 1.0
                scale_y = target_d / dims.y if dims.y else 1.0
                scale_z = target_h / dims.z if dims.z else 1.0
                log(f"     Calculated scale factors: X={scale_x:.4f}, Y={scale_y:.4f}, Z={scale_z:.4f}")
                
                # Check if width/depth might be swapped in GLB
                # If scale_x is very different from scale_y, try swapping
                scale_x_swapped = target_w / dims.y if dims.y else 1.0
                scale_y_swapped = target_d / dims.x if dims.x else 1.0
                
                # Calculate which mapping gives more consistent scaling
                diff_normal = abs(scale_x - scale_y)
                diff_swapped = abs(scale_x_swapped - scale_y_swapped)
                
                if diff_swapped < diff_normal and diff_swapped < 0.3:  # Swapped is more consistent
                    log(f"     ⚠ Detected possible width/depth swap in GLB!")
                    log(f"     Swapped scale factors: X={scale_x_swapped:.4f}, Y={scale_y_swapped:.4f}, Z={scale_z:.4f}")
                    log(f"     Using swapped mapping (GLB X→Depth, GLB Y→Width) with non-uniform scale")
                    # Swap the scale application: X gets depth scale, Y gets width scale, Z keeps its own scale
                    scale = Vector((scale_y_swapped, scale_x_swapped, scale_z))
                else:
                    # Use non-uniform scaling for width and depth (they often need different scales)
                    # But limit height scaling to avoid over-scaling (height measurements in GLB are often inaccurate)
                    # EXCEPTION: Don't apply height limiting for ceiling-mounted lights/chandeliers
                    avg_xy_scale = (scale_x + scale_y) / 2.0 if (scale_x and scale_y) else (scale_x or scale_y or 1.0)
                    
                    # Check if this is a ceiling-mounted light (hanging light, chandelier, ceiling light)
                    is_ceiling_light = any(keyword in item_type or keyword in item_name for keyword in [
                        'hanging', 'chandelier', 'ceiling_light', 'ceilinglight', 'lamp', 'light'
                    ]) and item.get('mounting_type', '').lower() == 'ceiling_mount'
                    
                    # Check if this is a frame, curtain, TV, or wall-mounted thin item
                    # These items typically have very different proportions (wide/tall but very thin)
                    is_thin_wall_item = any(keyword in item_type.lower() or keyword in item_name.lower() for keyword in [
                        'frame', 'picture', 'art', 'painting', 'mirror', 'curtain', 'drape',
                        'tv', 'television', 'monitor', 'screen'
                    ])
                    
                    # Check if this is a tall appliance or bathroom fixture (fridge, refrigerator, shower, etc.)
                    # These items are naturally tall and narrow, so they need independent height scaling
                    is_tall_appliance = any(keyword in item_type.lower() or keyword in item_name.lower() for keyword in [
                        'fridge', 'refrigerat', 'freezer', 'shower', 'bathtub', 'bath_tub'
                    ])
                    
                    # Limit height scaling: if it's too different from width/depth average, use the average instead
                    # This prevents over-scaling when height measurement is inaccurate
                    # SKIP THIS for:
                    # 1. User-resized assets (explicit user dimensions from frontend)
                    # 2. Ceiling lights, frames, curtains, and tall appliances (naturally different proportions)
                    if not is_user_resized and not is_ceiling_light and not is_thin_wall_item and not is_tall_appliance:
                        height_diff_ratio = abs(scale_z - avg_xy_scale) / avg_xy_scale if avg_xy_scale > 0 else 0
                        if height_diff_ratio > 0.2 or scale_z > 1.5:  # Height scale differs significantly or is too large
                            log(f"     Height scale ({scale_z:.4f}) differs significantly from width/depth average ({avg_xy_scale:.4f})")
                            log(f"     Limiting height scale to average ({avg_xy_scale:.4f}) to avoid over-scaling")
                            scale_z = avg_xy_scale
                    else:
                        if is_user_resized:
                            log(f"     ✅ User-resized - preserving exact user height scale ({scale_z:.4f})")
                        elif is_ceiling_light:
                            log(f"     💡 Ceiling light detected - preserving independent height scale ({scale_z:.4f})")
                        elif is_thin_wall_item:
                            log(f"     🖼️ Thin wall item (frame/curtain/TV) detected - preserving independent height scale ({scale_z:.4f})")
                        elif is_tall_appliance:
                            log(f"     🧊 Tall item (appliance/bathroom fixture) detected - preserving independent height scale ({scale_z:.4f})")
                    
                    log(f"     Using non-uniform scale: X={scale_x:.4f}, Y={scale_y:.4f}, Z={scale_z:.4f}")
                    scale = Vector((scale_x, scale_y, scale_z))
                
                # Determine ceiling height at item location for ceiling-mounted items
                ceiling_height = 2.4 # Global default fallback (240cm)
                found_area_id = None
                found_method = "Default"
                
                if is_ceiling_light:
                     # 1. Try exact polygon inclusion
                     for aid, area in areas.items():
                         if is_point_in_polygon(item.get('x',0), item.get('y',0), area.get('vertices', []), vertices):
                             ceiling_height = area.get('properties', {}).get('ceilingHeight', 240) * SCALE
                             found_area_id = aid
                             found_method = "Exact"
                             break
                     
                     # 2. If not found, try Closest Area (Fallback)
                     if not found_area_id and areas:
                         min_dist = float('inf')
                         closest_aid = None
                         item_x, item_y = item.get('x', 0), item.get('y', 0)
                         
                         for aid, area in areas.items():
                             # Calculate centroid using bounding box center as approx
                             a_verts = area.get('vertices', [])
                             if not a_verts: continue
                             
                             # Resolve vertices
                             poly_pts = []
                             for vid in a_verts:
                                 if isinstance(vid, (str, int)) and vid in vertices:
                                     poly_pts.append(vertices[vid])
                                 elif isinstance(vid, dict): # Handle dict structure if needed
                                     poly_pts.append(vid)
                             
                             if not poly_pts: continue
                             
                             cx = sum(v['x'] for v in poly_pts) / len(poly_pts)
                             cy = sum(v['y'] for v in poly_pts) / len(poly_pts)
                             
                             dist = ((item_x - cx)**2 + (item_y - cy)**2)**0.5
                             if dist < min_dist:
                                 min_dist = dist
                                 closest_aid = aid
                         
                         if closest_aid:
                             area = areas[closest_aid]
                             ceiling_height = area.get('properties', {}).get('ceilingHeight', 240) * SCALE
                             found_area_id = closest_aid
                             found_method = f"Closest ({min_dist:.1f} units)"
    
                # Calculate Z offset and Final Z position
                if is_ceiling_light:
                    # For ceiling items: Anchor TOP of object to CEILING HEIGHT
                    # Add slight OVERLAP (2cm) to ensure no light gap/visual gap
                    EMBED_OFFSET = 0.02 
                    z_offset_top = max_c.z * scale.z
                    final_z = ceiling_height - z_offset_top + EMBED_OFFSET
                    current_anchor = f"Ceiling ({found_method}, H={ceiling_height:.2f}m, Area={found_area_id}, Embed=+2cm)"
                else:
                    # For floor/wall items: Anchor BOTTOM of object to ALTITUDE
                    z_offset = min_c.z * scale.z
                    altitude_cm = get_dimension_value(props.get('altitude', 0), default=0)
                    altitude = altitude_cm * SCALE
                    final_z = layer_altitude + altitude - z_offset
                    current_anchor = f"Floor (Altitude: {(layer_altitude/SCALE + altitude_cm):.0f}cm)"
    
                # Apply manual Z adjustment if provided in item properties
                # This allows fine-tuning (increase/decrease) via 'z_offset' property
                # Default to +10cm for ceiling lights to ensure they embed into the ceiling (closing gaps)
                default_z = 25 if is_ceiling_light else 0
                manual_z_offset_cm = get_dimension_value(props.get('z_offset', default_z), default=default_z)
                
                if manual_z_offset_cm != 0:
                    manual_z_offset = manual_z_offset_cm * SCALE
                    final_z += manual_z_offset
                    current_anchor += f" + Manual Z Offset ({manual_z_offset_cm}cm)"
    
                # Debug logging
                log(f"  📍 Positioning '{item_id}' [{current_anchor}]:")
                if is_ceiling_light and not found_area_id:
                    log(f"     ⚠ WARNING: Could not find containing room for ceiling light! Using default height 2.4m")
                log(f"     Final Z position: {final_z:.4f}m")
                
                asset_path = glb_path if glb_path else obj_path
                parent.location = (item.get('x', 0) * SCALE, item.get('y', 0) * SCALE, final_z)
                
                # Apply +180 degrees to ALL items
                parent.rotation_euler.z = math.radians(item.get('rotation', 0)) + math.radians(180)
                log(f"  ✅ Applied rotation to item '{item_id}': JSON rotation + 180°")
                parent.scale = scale
                log(f"  ✅ Placed item asset '{item_id}' at position: ({item.get('x', 0) * SCALE:.2f}, {item.get('y', 0) * SCALE:.2f}, {altitude - z_offset:.2f})")
            except Exception as e: 
                log(f"  ❌ Error processing item '{item_id}': {e}")

    # --- STEP 5 & 6: Create GLOBAL Lights ---
    log(f"\n{'='*60}")
    log("💡 CREATING GLOBAL LIGHTING")
    log(f"{'='*60}")
    create_sunlight()
    create_180w_central_light()

    # === SUMMARY: FAILED/MISSING ASSETS ===
    log(f"\n{'='*60}")
    log(f"📋 ASSET LOADING SUMMARY")
    log(f"{'='*60}")
    
    failed_items = []
    # Iterate all layers for summary
    for layer in all_layers:
        items = layer.get('items', {})
        for item_id, item in items.items():
            if isinstance(item, dict):
                asset_urls = item.get('asset_urls', {})
                glb_path = asset_urls.get('GLB_File_URL')
                obj_path = asset_urls.get('OBJ_File_URL')
                
                has_glb = glb_path and os.path.exists(glb_path) if glb_path else False
                has_obj = obj_path and os.path.exists(obj_path) if obj_path else False
                
                if not has_glb and not has_obj:
                    item_name = item.get('name', item_id)
                    item_type = item.get('type', 'Unknown')
                    failed_items.append({
                        'id': item_id,
                        'name': item_name,
                        'type': item_type,
                        'glb_path': glb_path,
                        'obj_path': obj_path,
                        'layer': layer.get('id')
                    })
    
    if failed_items:
        log(f"⚠️  {len(failed_items)} ITEM(S) FAILED TO LOAD:")
        for failed in failed_items:
            log(f"  ❌ {failed['name']} (Type: {failed['type']})")
            log(f"     ID: {failed['id']}")
            log(f"     GLB Path: {failed['glb_path'] if failed['glb_path'] else 'None'}")
            log(f"     OBJ Path: {failed['obj_path'] if failed['obj_path'] else 'None'}")
    else:
        log(f"✅ All items loaded successfully!")
    
    # === FIX 2: CHECK FOR UNUSED DOWNLOADED ASSETS ===
    log(f"\n{'='*60}")
    log(f"🔍 CHECKING FOR UNUSED DOWNLOADED ASSETS")
    log(f"{'='*60}")
    
    # Get all GLB files in asset_downloads directory
    try:
        import glob
        asset_download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset_downloads")
        if os.path.exists(asset_download_dir):
            downloaded_glbs = glob.glob(os.path.join(asset_download_dir, "*.glb"))
            used_glb_paths = set()
            
            # Collect all GLB paths used in items and holes from ALL layers
            for layer in all_layers:
                l_items = layer.get('items') or {}
                for item in l_items.values():
                    if isinstance(item, dict):
                        glb_path = item.get('asset_urls', {}).get('GLB_File_URL', '')
                        if glb_path:
                            used_glb_paths.add(os.path.abspath(glb_path))
                
                l_holes = layer.get('holes') or {}
                for hole in l_holes.values():
                    if isinstance(hole, dict):
                        glb_path = hole.get('asset_urls', {}).get('GLB_File_URL', '')
                        if glb_path:
                            used_glb_paths.add(os.path.abspath(glb_path))
            
            # Find unused GLBs
            unused_glbs = []
            for glb_file in downloaded_glbs:
                abs_path = os.path.abspath(glb_file)
                if abs_path not in used_glb_paths:
                    unused_glbs.append(os.path.basename(glb_file))
            
            if unused_glbs:
                log(f"⚠️  WARNING: Found {len(unused_glbs)} downloaded GLB(s) NOT used in scene:")
                for glb_name in unused_glbs:
                    log(f"   - {glb_name}")
                    if 'curtain' in glb_name.lower():
                        log(f"     ⚠️  CURTAIN DETECTED: This curtain was downloaded but NOT in items list!")
                        log(f"     Fix: Add this curtain to the 'items' section of floor_plan_data JSON")
                log(f"  These assets were downloaded but not placed in the scene.")
                log(f"  This usually means they're referenced in the JSON but not in the items/holes lists.")
            else:
                log(f"✅ All downloaded GLB assets were used in the scene")
    except Exception as e:
        log(f"  ⚠️  Could not check for unused assets: {e}")
    
    # --- Final Scene Adjustments and Export ---
    log(f"\n{'='*60}")
    log(f"FINAL SCENE ADJUSTMENTS AND EXPORT")
    log(f"{'='*60}")
    log("Applying global Y-axis mirror to correct coordinate system.")
    scene_root = bpy.data.objects.new("SceneRoot", None); bpy.context.collection.objects.link(scene_root)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in bpy.context.scene.objects:
        if obj.parent is None and obj != scene_root: obj.select_set(True)
    if bpy.context.selected_objects:
        bpy.context.view_layer.objects.active = scene_root
        bpy.ops.object.parent_set(type='OBJECT', keep_transform=True)
    scene_root.scale.y = -1.0

    log("\n=== FINAL ASSET / MESH REPORT ===")
    mesh_count = sum(1 for obj in bpy.context.scene.objects if obj.type == 'MESH')
    light_count = sum(1 for obj in bpy.context.scene.objects if obj.type == 'LIGHT')
    log(f"Total meshes in scene: {mesh_count}")
    log(f"Total lights in scene: {light_count}")
    for m in bpy.data.materials:
        texs = report_material_textures(m)
        log(f"Material: '{m.name}' | Textures used: {len(texs)}")
        for t in texs: log(f"  - {t}")

    log("\nEnsuring all images are properly linked for export...")
    for img in list(bpy.data.images):
        src = img.get("z_realty_source_path")
        if src and os.path.exists(src):
            img.filepath = src; img.name = os.path.basename(src)
            try: img.reload()
            except: pass
        log(f"  Image: '{img.name}' -> '{getattr(img, 'filepath', 'Packed')}'")

    log("\nAttempting to assign textures for materials with no image maps...")
    try:
        assign_missing_textures_global(search_dirs)
    except Exception as e:
        log(f"  ❌ Global texture assignment step failed: {e}")

    # --- Sanitize Textures (Force Convert to PNG/JPG) ---
    log("\nSanitizing all images to ensure no WebP format remains...")
    sanitize_all_images(search_dirs)


    log(f"\nExporting scene to GLB: {output_glb_path}")
    try:
        # Use AUTO format to preserve PNG (no conversion = no WebP)
        # We've already converted everything to PNG above
        bpy.ops.export_scene.gltf(
            filepath=output_glb_path, 
            export_format='GLB', 
            export_apply=True, 
            export_image_format='AUTO'  # Use source format (PNG/JPEG)
        )
        log("Export complete.")
    except Exception as e:
        log(f"  ❌ Export failed: {e}"); write_log_file(output_glb_path); raise

    save_asset_report(output_glb_path)
    write_log_file(output_glb_path)
    log("Blender converter finished successfully.")

########################################
# Helper functions
########################################
def clear_scene():
    if bpy.context.active_object and bpy.context.active_object.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()
    for block in list(bpy.data.meshes):
        try: bpy.data.meshes.remove(block)
        except Exception: pass
    for block in list(bpy.data.materials):
        try: bpy.data.materials.remove(block)
        except Exception: pass
    for block in list(bpy.data.lights):
        try: bpy.data.lights.remove(block)
        except Exception: pass
    # keep images, we will manage them

def create_polygon_mesh(name, vertices, material, z_offset=0.0, props=None):
    """Creates a solid 3D slab for floors to prevent light leakage."""
    mesh = bpy.data.meshes.new(name); obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    
    # --- TIGHT SEAL: Make floor a solid slab that overlaps walls ---
    thickness = 0.15 # 15cm thick floor slab
    OVERLAP = 0.02   # 2cm upward overlap into walls
    
    verts = []
    for v in vertices:
        # Top face (visible) - place at exact floor height
        verts.append((v['x'], v['y'], z_offset))
        # Bottom face
        verts.append((v['x'], v['y'], z_offset - thickness))
        
    bm = bmesh.new()
    for v in verts: bm.verts.new(v)
    v_seq = list(bm.verts) # Robust indexing
    
    n = len(vertices)
    # Create faces with standard CCW winding
    # Top face (Points UP)
    bm.faces.new([v_seq[i*2] for i in range(n)]) 
    # Bottom face (Points DOWN)
    bm.faces.new([v_seq[i*2+1] for i in range(n)][::-1]) 
    
    # Side faces (Points OUT)
    for i in range(n):
        v1, v2 = v_seq[i*2], v_seq[((i+1)%n)*2]
        v3, v4 = v_seq[((i+1)%n)*2+1], v_seq[i*2+1]
        bm.faces.new([v1, v2, v3, v4])
    
    # Final normal pass for safety
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    
    # Ensure normals point up (Robust check)
    if len(mesh.polygons) > 0 and mesh.polygons[0].normal.z < 0:
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.flip_normals()
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='OBJECT')

    bm = bmesh.new(); bm.from_mesh(mesh)
    try: uv_layer = bm.loops.layers.uv.new("UVMap")
    except: uv_layer = bm.loops.layers.uv.active
    
    # IMPROVED UV MAPPING: Metric Scale
    tex_scale_u = 1.0
    tex_scale_v = 1.0
    if props:
        tex_scale_u = props.get('mapScaleU', props.get('texture_scale_x', 1.0))
        tex_scale_v = props.get('mapScaleV', props.get('texture_scale_y', 1.0))

    for face in bm.faces:
        for loop in face.loops:
            u = loop.vert.co.x * tex_scale_u
            v = loop.vert.co.y * tex_scale_v
            loop[uv_layer].uv = (u, v)
            
    bm.to_mesh(mesh); bm.free(); mesh.update()
    if material: obj.data.materials.append(material)
    log(f"  ✅ Created polygon mesh '{name}'")
    return obj

def asset_has_restricted_rotation(identifier=None, file_path=None):
    """
    Check if asset name contains compound words that should NOT rotate.
    Returns True if asset contains restricted names like 'walldecor', 'wallmounted', 'wallshelf'.
    These assets should have NO rotation applied.
    """
    s = ((identifier or "") + " " + (os.path.basename(file_path or ""))).lower()
    
    # List of restricted compound words that should NOT rotate
    restricted_patterns = [
        r'walldecor',      # WallDecor, wall_decor, etc.
        r'wallmounted',    # WallMounted, wall_mounted, etc.
        r'wallshelf',      # wallshelf, wall_shelf, etc.
        r'wall.*table',    # WallMountedTable, wall table, etc.
        r'wall.*shelf',    # wall shelf variations
        r'wall.*decor',    # wall decor variations
    ]
    
    return any(re.search(pattern, s) for pattern in restricted_patterns)



def sanitize_all_images(search_dirs):
    """
    NUCLEAR OPTION: Force ALL images to be saved as PNG files on disk.
    This ensures the GLTF exporter has NO opportunity to use WebP.
    """
    import tempfile
    
    # Create a persistent temp directory for this run
    temp_dir = os.path.join(tempfile.gettempdir(), "zrealty_texture_sanitize")
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
    
    converted_count = 0
    log(f"  🔍 Total images in bpy.data.images: {len(bpy.data.images)}")
    for img in list(bpy.data.images):
        try:
            log(f"  🖼️ Checking image: '{img.name}' (Source: {img.source}, Format: {img.file_format}, Path: {img.filepath})")
            
            # Metadata/Viewer skip
            if img.source == 'VIEWER':
                log(f"    ⏭️ Skipping '{img.name}': Source is VIEWER")
                continue

            # Force pixel data load to ensure has_data becomes True
            if not img.has_data:
                log(f"    📥 Forcing pixel load for '{img.name}'...")
                try:
                    # Accessing pixels forces Blender to load the image/packed data
                    _ = img.pixels[0]
                except Exception as load_err:
                    log(f"    ⚠️ Could not load pixels for '{img.name}': {load_err}")

            if not img.has_data:
                log(f"    ⏭️ Skipping '{img.name}': Still no data after load attempt")
                continue

            # Determine if it's WebP or Packed
            is_webp = img.filepath.lower().endswith('.webp') or img.file_format == 'WEBP'
            is_packed = img.packed_file is not None
            
            # If we've already converted this one in this session (path starts with temp_dir), skip
            if temp_dir in img.filepath:
                log(f"    ⏭️ Skipping '{img.name}': Already in temp sanitized path")
                continue

            # We want to convert if it's WEBP OR PACKED
            if not (is_webp or is_packed):
                log(f"    ⏭️ Skipping '{img.name}': Not WebP and not Packed")
                continue

            # FORCE CONVERSION
            log(f"    ⭐ Converting '{img.name}' (WebP={is_webp}, Packed={is_packed})...")
            
            # Generate a safe filename
            safe_name = "".join([c for c in img.name if c.isalnum() or c in (' ', '.', '_', '-')]).rstrip()
            if not safe_name: 
                safe_name = f"image_{uuid.uuid4().hex[:8]}"
            
            # Remove any existing extension and force PNG
            safe_name = os.path.splitext(safe_name)[0]
            fname = f"{safe_name}_{uuid.uuid4().hex[:6]}.png" # Add unique suffix to avoid collisions
            temp_path = os.path.join(temp_dir, fname)
            
            # Unpack if packed (to ensure we have disk data if needed, but save() works anyway)
            if img.packed_file:
                # SKIP UNPACKING TO AVOID CREATING 'textures' FOLDER IN ROOT
                # try: img.unpack(method='WRITE_LOCAL')
                # except: pass
                pass
            
            # Save as PNG
            img.file_format = 'PNG'
            img.filepath_raw = temp_path
            
            try:
                img.save()
                converted_count += 1
                log(f"    ✅ Successfully converted -> {temp_path}")
            except Exception as save_err:
                log(f"    ❌ Could not save '{img.name}': {save_err}")
                continue
            
            # Reload from the new path
            img.filepath = temp_path
            try: img.reload()
            except: pass
            
        except Exception as e:
            log(f"  ❌ Failed to process image '{img.name}': {e}")
    
    log(f"  📊 Sanitization complete: {converted_count} image(s) converted to PNG")


def asset_is_excluded_from_flip(identifier=None, file_path=None):
    """
    Check if asset should be excluded from 180-degree rotation.
    Only excludes if 'door', 'wall', or 'floor' appear as standalone words
    (not as part of compound words like 'WallDecor', 'WallMounted', 'wallshelf').
    """
    s = ((identifier or "") + " " + (os.path.basename(file_path or ""))).lower()
    
    # Use regex word boundaries to match only standalone words
    # This ensures "wall" in "WallDecor" or "wallshelf" won't match
    patterns = [
        r'\bdoor\b',  # Matches "door" as standalone word only
        r'\bwall\b',  # Matches "wall" as standalone word only (not "WallDecor", "wallshelf")
        r'\bfloor\b'  # Matches "floor" as standalone word only
    ]
    
    return any(re.search(pattern, s) for pattern in patterns)

def get_hierarchy_bbox(parent_obj, include_all_meshes=False):
    """
    Calculate bounding box from mesh geometry, excluding decorative elements.
    Filters out small meshes and decorative items (spheres, plates, vases, etc.)
    to get accurate dimensions for the main structure only.
    
    Args:
        parent_obj: The parent object whose hierarchy to measure
        include_all_meshes: If True, include ALL meshes without filtering (for chandeliers, lights)
    """
    min_c, max_c = Vector((float('inf'),)*3), Vector((float('-inf'),)*3)
    all_objects = [o for o in parent_obj.children_recursive if o.type == 'MESH']
    if parent_obj.type == 'MESH': all_objects.append(parent_obj)
    if not all_objects: return Vector((0,)*3), Vector((0,)*3)
    
    # For ceiling lights/chandeliers, include ALL meshes without filtering
    if include_all_meshes:
        log(f"  📦 Including ALL {len(all_objects)} meshes for bounding box (ceiling light/chandelier)")
        main_objects = all_objects
        # Calculate and return bbox immediately for all meshes
        for obj in main_objects:
            mesh = obj.data
            if not mesh.vertices: continue
            for v in mesh.vertices:
                world_pos = obj.matrix_world @ v.co
                min_c = Vector((min(min_c[i], world_pos[i]) for i in range(3)))
                max_c = Vector((max(max_c[i], world_pos[i]) for i in range(3)))
        return min_c, max_c
    
    # Filter out decorative elements (small meshes, decorative items)
    decorative_keywords = ['sphere', 'plate', 'vase', 'cup', 'bowl', 'flower', 'teapot', 'line', 'torus', 'chamfercyl', 'plane', 'box', 'loft']
    main_objects = []
    object_sizes = []
    
    for obj in all_objects:
        if not obj.data.vertices:
            continue
        
        mesh_name_lower = obj.data.name.lower()
        obj_name_lower = obj.name.lower()
        combined_name = mesh_name_lower + " " + obj_name_lower
        
        # Skip decorative elements by name
        if any(kw in combined_name for kw in decorative_keywords):
            continue
        
        # Calculate mesh size to filter out very small decorative pieces
        try:
            # Get local bounding box
            local_bbox = [Vector(v) for v in obj.bound_box]
            if local_bbox:
                # Calculate size in each dimension
                sizes = [
                    max([v[i] for v in local_bbox]) - min([v[i] for v in local_bbox])
                    for i in range(3)
                ]
                max_size = max(sizes)
                
                # Only include meshes larger than 0.5m (more conservative - main structure only)
                # This filters out small decorative pieces and medium-sized parts
                if max_size > 0.5:
                    # Also check if this mesh has reasonable height (not just a flat tabletop)
                    # Tabletops are usually very thin (Z < 0.1m), full tables have Z > 0.5m
                    height_size = sizes[2]  # Z dimension
                    # Prefer meshes with substantial height (likely full table with legs)
                    # But don't exclude tabletop if it's the only large mesh
                    if height_size > 0.3 or max_size > 1.5:  # Either has height or is very large
                        main_objects.append(obj)
                        object_sizes.append((max_size, obj, sizes))  # Store all dimensions
                    else:
                        # Store as secondary candidate (tabletop only)
                        object_sizes.append((max_size * 0.5, obj, sizes))  # Lower priority
        except Exception:
            # If calculation fails, skip the object
            pass
    
    # If we have multiple main objects, prefer ones with substantial height (full table, not just tabletop)
    if len(main_objects) > 1 or object_sizes:
        # Sort by size, but prioritize meshes with height > 0.3m (full table structure)
        # object_sizes format: (max_size, obj, [x_size, y_size, z_size])
        object_sizes.sort(reverse=True, key=lambda x: (x[2][2] if len(x) > 2 and x[2][2] > 0.3 else 0, x[0]))
        
        # Try to find a mesh with substantial height first
        selected_obj = None
        for size_data in object_sizes:
            if len(size_data) > 2:
                max_size, obj, sizes = size_data[0], size_data[1], size_data[2]
                height = sizes[2] if len(sizes) > 2 else 0
                # Prefer meshes with height > 0.3m (likely full table with legs)
                if height > 0.3:
                    selected_obj = obj
                    log(f"  📦 Selected mesh with height: '{obj.data.name}' (H={height:.4f}m)")
                    break
        
        # If no mesh with height found, use the largest one
        if not selected_obj and object_sizes:
            selected_obj = object_sizes[0][1]
            if len(object_sizes[0]) > 2:
                sizes = object_sizes[0][2]
                height = sizes[2] if len(sizes) > 2 else 0
                log(f"  ⚠ Using largest mesh (low height): '{selected_obj.data.name}' (H={height:.4f}m)")
            else:
                log(f"  ⚠ Using largest mesh: '{selected_obj.data.name}'")
        
        if selected_obj:
            # Use ALL main structure meshes (not just one) to get full bounding box
            # This ensures we capture tabletop + legs, not just the tabletop
            # Include all meshes with height > 0.3m OR all main_objects if they exist
            all_main_meshes = []
            for size_data in object_sizes:
                if len(size_data) > 2:
                    max_size, obj, sizes = size_data[0], size_data[1], size_data[2]
                    height = sizes[2] if len(sizes) > 2 else 0
                    # Include meshes with substantial height (full table structure)
                    if height > 0.3 or obj in main_objects:
                        all_main_meshes.append(obj)
            
            # If we found meshes with height, use those; otherwise use all main_objects
            if all_main_meshes:
                main_objects = list(set(all_main_meshes))  # Remove duplicates
                log(f"  📦 Using {len(main_objects)} main structure mesh(es) for full bounding box")
            else:
                main_objects = [selected_obj]
                log(f"  ⚠ Using single mesh (may be tabletop only)")
            
            # Log details about selected meshes
            try:
                for obj in main_objects:
                    local_bbox = [Vector(v) for v in obj.bound_box]
                    if local_bbox:
                        sizes = [
                            max([v[i] for v in local_bbox]) - min([v[i] for v in local_bbox])
                            for i in range(3)
                        ]
                        log(f"     - '{obj.data.name}': X={sizes[0]:.4f}m, Y={sizes[1]:.4f}m, Z={sizes[2]:.4f}m")
            except Exception as e:
                log(f"     Could not log mesh details: {e}")
    elif not main_objects:
        # If filtering removed everything, use all objects (fallback)
        main_objects = all_objects
        log(f"  ⚠ No main objects found after filtering, using all {len(all_objects)} objects for bounding box")
    else:
        log(f"  📦 Filtered bounding box: {len(main_objects)} main object(s) out of {len(all_objects)} total")
    
    # Calculate bounding box from main objects only
    for obj in main_objects:
        mesh = obj.data
        if not mesh.vertices:
            continue
        
        # Calculate bounding box from mesh vertices in WORLD space
        mw = obj.matrix_world
        for vertex in mesh.vertices:
            v = mw @ vertex.co
            min_c.x, min_c.y, min_c.z = min(min_c.x, v.x), min(min_c.y, v.y), min(min_c.z, v.z)
            max_c.x, max_c.y, max_c.z = max(max_c.x, v.x), max(max_c.y, v.y), max(max_c.z, v.z)
    
    return (Vector((0,)*3), Vector((0,)*3)) if float('inf') in min_c else (min_c, max_c)


def is_point_in_polygon(x, y, poly_verts, all_verts):
    """
    Ray-casting algorithm to check if point (x,y) is inside polygon defined by vertex IDs.
    """
    n = len(poly_verts)
    if n < 3: return False
    
    inside = False
    # Get coordinates for first vertex
    if isinstance(poly_verts[0], (str, int)): # If list of IDs
        try:
            p1 = all_verts[poly_verts[0]]
        except KeyError: return False
    else: # If list of objects/dicts
        p1 = poly_verts[0]
        
    p1x, p1y = p1['x'], p1['y']
    
    for i in range(n + 1):
        # Get coordinates for next vertex
        idx = i % n
        if isinstance(poly_verts[idx], (str, int)):
            try:
                p2 = all_verts[poly_verts[idx]]
            except KeyError: continue
        else:
            p2 = poly_verts[idx]
            
        p2x, p2y = p2['x'], p2['y']
        
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
        
    return inside

########################################
# Entry point
########################################
if __name__ == "__main__":
    main()