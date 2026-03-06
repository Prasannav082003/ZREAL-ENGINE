"""
Material Optimizer for Photorealistic Rendering in Unreal Engine

This script analyzes imported materials and applies optimized PBR (Physically Based Rendering)
parameters based on material type detection (metal, wood, glass, fabric, etc.)
"""

import unreal

class MaterialOptimizer:
    """Optimizes materials for photorealistic rendering based on material type."""
    
    # Material type detection keywords
    MATERIAL_TYPES = {
        'metal': ['metal', 'steel', 'iron', 'aluminum', 'aluminium', 'brass', 'copper', 'chrome', 'gold', 'silver', 'bronze'],
        'wood': ['wood', 'timber', 'oak', 'pine', 'mahogany', 'plywood', 'walnut', 'maple', 'cherry', 'bamboo'],
        'fabric': ['fabric', 'cloth', 'textile', 'cotton', 'linen', 'curtain', 'drape', 'upholstery', 'velvet', 'silk'],
        'glass': ['glass', 'window', 'mirror', 'transparent', 'crystal'],
        'plastic': ['plastic', 'polymer', 'acrylic', 'vinyl', 'pvc'],
        'stone': ['stone', 'marble', 'granite', 'concrete', 'brick', 'tile', 'ceramic', 'porcelain'],
        'leather': ['leather', 'hide', 'suede'],
        'paint': ['paint', 'painted', 'wall', 'ceiling', 'floor']
    }
    
    # PBR parameters per material type
    # Values are based on real-world material properties
    PBR_PRESETS = {
        'metal': {
            'description': 'Metallic surfaces (steel, aluminum, brass, etc.)',
            'metallic': 1.0,      # Fully metallic
            'roughness': 0.2,     # Smooth but not mirror-like
            'specular': 0.5,      # Standard specular for metals
        },
        'wood': {
            'description': 'Wood surfaces (oak, pine, mahogany, etc.)',
            'metallic': 0.0,      # Non-metallic
            'roughness': 0.7,     # Moderately rough
            'specular': 0.3,      # Low specular
        },
        'fabric': {
            'description': 'Fabric/cloth materials (cotton, linen, curtains, etc.)',
            'metallic': 0.0,      # Non-metallic
            'roughness': 0.9,     # Very rough (diffuse)
            'specular': 0.1,      # Very low specular
        },
        'glass': {
            'description': 'Glass/transparent materials',
            'metallic': 0.0,      # Non-metallic
            'roughness': 0.0,     # Perfectly smooth
            'specular': 1.0,      # High specular (reflective)
            'opacity': 0.1,       # Mostly transparent
        },
        'plastic': {
            'description': 'Plastic materials (polymer, acrylic, etc.)',
            'metallic': 0.0,      # Non-metallic
            'roughness': 0.4,     # Moderately smooth
            'specular': 0.5,      # Medium specular
        },
        'stone': {
            'description': 'Stone/concrete materials (marble, granite, brick, etc.)',
            'metallic': 0.0,      # Non-metallic
            'roughness': 0.8,     # Rough surface
            'specular': 0.2,      # Low specular
        },
        'leather': {
            'description': 'Leather materials',
            'metallic': 0.0,      # Non-metallic
            'roughness': 0.6,     # Moderately rough
            'specular': 0.4,      # Medium specular
        },
        'paint': {
            'description': 'Painted surfaces (walls, ceilings, floors)',
            'metallic': 0.0,      # Non-metallic
            'roughness': 0.5,     # Semi-rough (matte paint)
            'specular': 0.3,      # Low-medium specular
        }
    }
    
    @staticmethod
    def detect_material_type(material_name, asset_name=""):
        """
        Detect material type from material name and asset name.
        
        Args:
            material_name: Name of the material
            asset_name: Name of the asset (for additional context)
            
        Returns:
            Material type string (e.g., 'metal', 'wood', 'glass') or 'default'
        """
        combined_name = f"{asset_name} {material_name}".lower()
        
        for mat_type, keywords in MaterialOptimizer.MATERIAL_TYPES.items():
            if any(keyword in combined_name for keyword in keywords):
                return mat_type
        
        return 'default'
    
    @staticmethod
    def get_material_preset(mat_type):
        """Get PBR preset for a material type."""
        return MaterialOptimizer.PBR_PRESETS.get(mat_type, None)
    
    @staticmethod
    def optimize_material_instance(material_instance, asset_name=""):
        """
        Optimize a material instance for photorealistic rendering.
        
        Args:
            material_instance: Unreal MaterialInstanceConstant to optimize
            asset_name: Name of the asset for context
            
        Returns:
            True if optimization was applied, False otherwise
        """
        if not material_instance:
            return False
        
        material_name = material_instance.get_name()
        
        # Detect material type
        mat_type = MaterialOptimizer.detect_material_type(material_name, asset_name)
        
        if mat_type == 'default':
            return False
        
        # Get PBR preset
        preset = MaterialOptimizer.get_material_preset(mat_type)
        if not preset:
            return False
        
        print(f"  🎨 Optimizing material: {material_name}")
        print(f"     Detected type: {mat_type} - {preset.get('description', '')}")
        
        try:
            # Apply scalar parameters to material instance
            if 'metallic' in preset:
                unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
                    material_instance, 'Metallic', preset['metallic']
                )
            
            if 'roughness' in preset:
                unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
                    material_instance, 'Roughness', preset['roughness']
                )
            
            if 'specular' in preset:
                unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
                    material_instance, 'Specular', preset['specular']
                )
            
            if 'opacity' in preset:
                unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
                    material_instance, 'Opacity', preset['opacity']
                )
            
            print(f"     ✅ Applied preset: Metallic={preset.get('metallic', 'N/A')}, "
                  f"Roughness={preset.get('roughness', 'N/A')}, "
                  f"Specular={preset.get('specular', 'N/A')}")
            
            return True
            
        except Exception as e:
            print(f"     ⚠️ Could not apply preset: {e}")
            return False


def optimize_all_materials_in_scene():
    """
    Optimize all materials in the current scene for photorealistic rendering.
    This function is called from the main render script.
    """
    print(f"\n{'='*60}")
    print(f"🎨 OPTIMIZING MATERIALS FOR PHOTOREALISM")
    print(f"{'='*60}")
    
    optimized_count = 0
    skipped_count = 0
    
    # Get all actors in the level
    all_actors = unreal.EditorLevelLibrary.get_all_level_actors()
    
    for actor in all_actors:
        # Only process static mesh actors
        if not isinstance(actor, unreal.StaticMeshActor):
            continue
        
        actor_name = actor.get_name()
        
        # Get static mesh component
        mesh_component = actor.get_component_by_class(unreal.StaticMeshComponent)
        if not mesh_component:
            continue
        
        # Get all materials on this mesh
        num_materials = mesh_component.get_num_materials()
        
        for i in range(num_materials):
            material = mesh_component.get_material(i)
            
            if not material:
                continue
            
            # Check if it's a material instance
            if isinstance(material, unreal.MaterialInstanceConstant):
                if MaterialOptimizer.optimize_material_instance(material, actor_name):
                    optimized_count += 1
                else:
                    skipped_count += 1
            else:
                # For non-instance materials, we can't easily modify parameters
                # Would need to create a material instance first
                skipped_count += 1
    
    print(f"\n{'='*60}")
    print(f"✅ Material Optimization Complete")
    print(f"   Optimized: {optimized_count} materials")
    print(f"   Skipped: {skipped_count} materials (no type detected or not instance)")
    print(f"{'='*60}\n")
    
    return optimized_count
