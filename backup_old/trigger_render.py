import requests
import json
import time
import sys
import os

# Configuration -- Try both localhost and 127.0.0.1
BASE_URLS = ["http://localhost:4451", "http://127.0.0.1:4451"]
API_KEY = "dummy-key-if-auth-enabled" 
PROJECT_ID = 529
GALLERY_ID = 2358
USER_ID = 0

# Sample Payload matching the user's log
payload = {
    "floor_plan_data": "{}",
    "blender_script": "print('test')",
    "fov": 45.0,
    "directional_light": {
        "intensity": 5.0,
        "position": {"x": 10, "y": 20, "z": 30},
        "color": {"r": 1.0, "g": 0.9, "b": 0.8},
        "cast_shadow": True
    },
    "aspect_ratio": "16:9",
    "render_quality": "4K",
    "timestamp": int(time.time()),
    "hdri_filename": "kloppenheim_06_puresky_4k.exr",
    "threejs_camera": {
        "position": {"x": 0, "y": -5, "z": 2},
        "rotation": {"x": 1.57, "y": 0, "z": 0},
        "target": {"x": 0, "y": 0, "z": 0},
        "fov": 45
    },
    "enable_status_updates": False
}

def trigger_render(base_url):
    print(f"🚀 Sending Render Request to {base_url}...")
    
    url = f"{base_url}/generate-4k-render/?ProjectId={PROJECT_ID}&Id={GALLERY_ID}&UserId={USER_ID}"
    
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        
        if response.status_code == 202:
            data = response.json()
            job_id = data.get("job_id")
            print(f"✅ Job Accepted! Job ID: {job_id}")
            return job_id
        else:
            print(f"❌ Failed to trigger render. Status: {response.status_code}")
            try:
                print(f"Response: {response.text}")
            except:
                pass
            return None
            
    except requests.exceptions.ConnectionError:
        print(f"❌ Connection Error: Is the server running on {base_url}?")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

if __name__ == "__main__":
    print("--- DIAGNOSTIC TRIGGER SCRIPT ---")
    for url in BASE_URLS:
        job_id = trigger_render(url)
        if job_id:
            print(f"\nSUCCESS! Server is responding on {url}")
            print("Please check your 'render_development.bat' terminal window.")
            print("You should see 'NEW RENDER REQUEST RECEIVED' logs there now.")
            break
    else:
        print("\n❌ FAILED: Could not connect to server on any local address.")
        print("Please check if 'render_development.bat' is actually running.")
