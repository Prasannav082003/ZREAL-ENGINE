# filename: main.py
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Body, Depends, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Set, Dict, Any
from typing_extensions import Annotated
from pathlib import Path
import os
import sys
import json
import uuid
import hashlib
import subprocess
import requests
import tempfile
from urllib.parse import urlparse
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, wait
import shlex
import re
import glob
from app.database import models, session
from app.services import auth_service
from app.services.render_logger import RenderLogger
from threading import Lock, Thread
import queue
import signal
import asyncio
import logging
from dotenv import load_dotenv

# AI Render Service - Moved to app/services/ai_render_service.py
from app.services.ai_render_service import get_ai_render_service

# Load environment variables from .env file
load_dotenv()


map_lock = Lock()

# --- CONSOLE COLORS ---
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_RESET = "\033[0m"

# --- ASSET & TEXTURE MASTERY API CONFIGURATION ---
ASSETS_AND_TEXTURE_API_KEY = "zrsk_beta_8a1d4c7e6f2b9a5d3c1e0f8b6a4d"
ASSETS_AND_TEXTURE_API_HEADER_NAME = "ZRealtyServiceApiKey"
ASSET_ENDPOINT = "http://216.48.178.133:4050/api/v1/AssetMaster/GetAllAssets3D"
TEXTURE_ENDPOINT = "http://216.48.178.133:4050/api/v1/TextureMaster/GetAllTextureLibraries"

# --- BASE DIRECTORY ---
BASE_DIR = Path(__file__).resolve().parent

# AI Render Service Initialization
ai_service = get_ai_render_service(BASE_DIR)

# Global Cache for Master Data
_CACHED_ASSETS_MAP = {}
_CACHED_TEXTURES_MAP = {}
_CACHED_ASSET_URL_TO_HP_MAP = {}  # Map: Any GLB URL -> its HP GLB URL
_CACHED_TEXTURE_URL_TO_HP_MAP = {} # Map: Any Texture URL -> its HR Texture URL
_MASTER_DATA_FETCHED = False
_MASTER_DATA_LOCK = Lock()

def _fetch_master_data_from_api():
    """
    Fetches Asset and Texture master data from the API and populates the global caches.
    This should be called once at the start of the download process.
    """
    global _CACHED_ASSETS_MAP, _CACHED_TEXTURES_MAP, _CACHED_ASSET_URL_TO_HP_MAP, _MASTER_DATA_FETCHED
    
    with _MASTER_DATA_LOCK:
        if _MASTER_DATA_FETCHED:
            return

        print(f"\n{COLOR_BLUE}--- Fetching Master Data from API... ---{COLOR_RESET}")
        headers = {
            ASSETS_AND_TEXTURE_API_HEADER_NAME: ASSETS_AND_TEXTURE_API_KEY,
            "Content-Type": "application/json"
        }
        
        # 1. Fetch Assets
        try:
            print(f"  Requesting Assets from: {ASSET_ENDPOINT}")
            # verify=False to avoid SSL issues with some internal/QA certs
            resp = requests.get(ASSET_ENDPOINT, headers=headers, verify=False, timeout=60)
            if resp.status_code == 200:
                assets_list = resp.json()
                if isinstance(assets_list, list):
                    # Map ID -> Asset Object
                    _CACHED_ASSETS_MAP = {item.get('asset3D_Id'): item for item in assets_list if 'asset3D_Id' in item}
                    
                    # --- NEW: Build URL-to-HP mapping ---
                    _CACHED_ASSET_URL_TO_HP_MAP = {}
                    for item in assets_list:
                        hp_url = item.get('highPoly_Glb')
                        if not hp_url or not isinstance(hp_url, str):
                            continue
                        
                        # Normalize HP URL
                        norm_hp = hp_url.strip().replace("\\", "/")
                        if norm_hp.startswith("/"): norm_hp = norm_hp[1:]
                        
                        # Assets to map to this HP
                        potential_keys = [
                            item.get('lowPoly_Glb'),
                            item.get('mediumPoly_Glb'),
                            item.get('highPoly_Glb')
                        ]
                        for k in potential_keys:
                            if k and isinstance(k, str):
                                norm_k = k.strip().replace("\\", "/")
                                if norm_k.startswith("/"): norm_k = norm_k[1:]
                                # Store exact relative path (e.g. glb-assets/...)
                                _CACHED_ASSET_URL_TO_HP_MAP[norm_k] = norm_hp
                                # Store filename only (fallback)
                                _CACHED_ASSET_URL_TO_HP_MAP[os.path.basename(norm_k)] = norm_hp
                                # Store with full blob prefix if missing
                                if not norm_k.startswith("http"):
                                     full_u = f"https://zrealtystoragedev.blob.core.windows.net/{norm_k}"
                                     _CACHED_ASSET_URL_TO_HP_MAP[full_u] = norm_hp
                    
                    print(f"  {COLOR_GREEN}[OK] Loaded {len(_CACHED_ASSETS_MAP)} assets and indexed {len(_CACHED_ASSET_URL_TO_HP_MAP)} URLs.{COLOR_RESET}")
                    # DEBUG: Check for specific ID 147
                    if 147 in _CACHED_ASSETS_MAP or "147" in _CACHED_ASSETS_MAP:
                        print(f"  {COLOR_GREEN}[OK] Asset ID 147 successfully loaded in cache.{COLOR_RESET}")
                    else:
                        print(f"  {COLOR_RED}[X] Asset ID 147 NOT found in cache.{COLOR_RESET}")

                else:
                    print(f"  {COLOR_RED}[X] Asset response was not a list.{COLOR_RESET}")
            else:
                if resp.status_code == 404:
                    print(f"  {COLOR_YELLOW}[!] Asset fetch failed: 404 Not Found (Master data not available){COLOR_RESET}")
                else:
                    error_text = resp.text[:100] if resp.text and not resp.text.strip().startswith("<") else "Unknown Error"
                    print(f"  {COLOR_RED}[X] Asset fetch failed: {resp.status_code} - {error_text}{COLOR_RESET}")
        except Exception as e:
            print(f"  {COLOR_RED}[X] Asset fetch exception: {e}{COLOR_RESET}")

        # 2. Fetch Textures
        try:
            print(f"  Requesting Textures from: {TEXTURE_ENDPOINT}")
            resp = requests.get(TEXTURE_ENDPOINT, headers=headers, verify=False, timeout=60)
            if resp.status_code == 200:
                textures_list = resp.json()
                if isinstance(textures_list, list):
                    # Map ID -> Texture Object
                    _CACHED_TEXTURES_MAP = {item.get('textureLibrary_Id'): item for item in textures_list if 'textureLibrary_Id' in item}
                    
                    # --- NEW: Build Texture URL mapping ---
                    _CACHED_TEXTURE_URL_TO_HP_MAP = {}
                    for item in textures_list:
                        hr_urls = item.get('high_Resolution_Url')
                        lr_urls = item.get('low_Resolution_Url')
                        
                        if hr_urls and isinstance(hr_urls, list):
                            # Map everything to HR list
                            potential_sources = []
                            if hr_urls: potential_sources.extend(hr_urls)
                            if lr_urls and isinstance(lr_urls, list): potential_sources.extend(lr_urls)
                            
                            for s in potential_sources:
                                if s and isinstance(s, str):
                                    norm_s = s.strip().replace("\\", "/")
                                    # Texture Library maps to a list of HR URLs
                                    _CACHED_TEXTURE_URL_TO_HP_MAP[norm_s] = hr_urls
                    
                    print(f"  {COLOR_GREEN}[OK] Loaded {len(_CACHED_TEXTURES_MAP)} textures and indexed {len(_CACHED_TEXTURE_URL_TO_HP_MAP)} urls.{COLOR_RESET}")
                else:
                    print(f"  {COLOR_RED}[X] Texture response was not a list.{COLOR_RESET}")
            else:
                if resp.status_code == 404:
                     print(f"  {COLOR_YELLOW}[!] Texture fetch failed: 404 Not Found (Master data not available){COLOR_RESET}")
                else:
                    error_text = resp.text[:100] if resp.text and not resp.text.strip().startswith("<") else "Unknown Error"
                    print(f"  {COLOR_RED}[X] Texture fetch failed: {resp.status_code} - {error_text}{COLOR_RESET}")
        except Exception as e:
            print(f"  {COLOR_RED}[X] Texture fetch exception: {e}{COLOR_RESET}")
            
        _MASTER_DATA_FETCHED = True

def _resolve_asset_url(asset_id, show_details=False):
    """
    Looks up asset_id in the cache and returns the best available GLB URL (HighPoly preferred).
    """
    if not asset_id:
        return None
        
    # Robust lookup: Try as is, then as int, then as str
    asset = _CACHED_ASSETS_MAP.get(asset_id)
    if not asset:
        try:
            asset = _CACHED_ASSETS_MAP.get(int(asset_id))
        except (ValueError, TypeError):
            pass
    if not asset:
        asset = _CACHED_ASSETS_MAP.get(str(asset_id))

    if not asset:
        if show_details:
             print(f"  {COLOR_YELLOW}Warning: Asset ID {asset_id} not found in master data (Checked int/str).{COLOR_RESET}")
        return None
        
    # Priority: High Poly ONLY (User Request)
    hp_url = asset.get('highPoly_Glb')

    if show_details:
        print(f"DEBUG: Resolving Asset {asset_id}. Found HP URL: {hp_url}")

    # If keys are None or empty string, treat as empty
    hp_url = hp_url if (hp_url and isinstance(hp_url, str) and hp_url.strip()) else None

    # Force High Poly only
    final_url = hp_url
    
    if final_url:
        return final_url
    return None

def _resolve_hp_url_from_url(url: str, show_details=False) -> Any:
    """
    Given a URL or path, looks up if it matches:
    1. Any known asset's GLB URL -> returns HP GLB URL (str)
    2. Any known texture's URL -> returns list of HR URLs (list[str])
    """
    if not url or not isinstance(url, str):
        return None
        
    norm_url = url.strip().replace("\\", "/")
    if norm_url.startswith("/"): norm_url = norm_url[1:]
    
    # NEW: Handle spaces in URL (decode %20)
    if '%' in norm_url:
        from urllib.parse import unquote
        norm_url_decoded = unquote(norm_url)
    else:
        norm_url_decoded = norm_url

    # 1. Check Assets Map (GLBs)
    # Try exact match first
    hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(norm_url)
    
    # Try decoded match (e.g. "Office Table_LP.glb" vs "Office%20Table_LP.glb")
    if not hp_found:
        hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(norm_url_decoded)

    # NEW: Handle spaces in URL -> Try ENCODED match (Space -> %20)
    # The Map might store keys as proper URLs (with %20), but input has spaces.
    if not hp_found and ' ' in norm_url:
        from urllib.parse import quote
        # Quote paths but not slashes/colons generally, though for just filename/path component
        # a simple replace for space is safest given the context of GLB filenames.
        norm_url_encoded = norm_url.replace(" ", "%20")
        hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(norm_url_encoded)
    # Try decoded match (e.g. "Office Table_LP.glb" vs "Office%20Table_LP.glb")
    if not hp_found:
        hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(norm_url_decoded)
    
    if not hp_found:
        path_part = urlparse(url).path
        if path_part.startswith("/"): path_part = path_part[1:]
        hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(path_part)
        
        # Try decoded path part
        if not hp_found:
            path_part_decoded = unquote(path_part) if '%' in path_part else path_part
            hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(path_part_decoded)
        
    if hp_found:
        full_hp_url = _convert_glb_path_to_url(hp_found)
        if show_details:
            print(f"  {COLOR_GREEN}✓ Resolved Asset {os.path.basename(url)} -> {os.path.basename(full_hp_url)} via URL match{COLOR_RESET}")
        return full_hp_url
        
    # 2. Check Textures Map (Images)
    hr_list = _CACHED_TEXTURE_URL_TO_HP_MAP.get(norm_url)
    if not hr_list:
        # Also try just the filename/path part for textures? 
        # Usually textures have long paths. Let's try direct norm_url first.
        # If no match, try without SAS if present
        if '?' in norm_url:
            base_norm = norm_url.split('?')[0]
            hr_list = _CACHED_TEXTURE_URL_TO_HP_MAP.get(base_norm)

    if hr_list and isinstance(hr_list, list):
        if show_details:
             print(f"  {COLOR_GREEN}✓ Resolved Texture {os.path.basename(url)} -> HR List ({len(hr_list)} items) via URL match{COLOR_RESET}")
        return hr_list
        
    return None

def _resolve_texture_urls(texture_id, show_details=False):
    """
    Looks up texture_id and returns a list of High Resolution URLs.
    """
    if not texture_id:
        return []
        
    texture = _CACHED_TEXTURES_MAP.get(texture_id)
    if not texture:
        if show_details:
             print(f"  {COLOR_YELLOW}Warning: Texture ID {texture_id} not found in master data.{COLOR_RESET}")
        return []
        
    # Try High Resolution first
    urls = texture.get('high_Resolution_Url')
    if not urls:
        urls = texture.get('low_Resolution_Url')
        
    if urls and isinstance(urls, list):
         return [u for u in urls if isinstance(u, str) and u.strip()]
    return []


# --- RENDER QUEUE SYSTEM ---
# Queue to hold render jobs (processes one at a time to avoid render conflicts)
render_queue = queue.Queue()
queue_lock = Lock()
active_jobs = {}  # Track job status: {job_id: {"status": "queued|processing|completed|failed", "position": int}}
is_shutting_down = False  # Flag to track shutdown state

# --- GLOBAL STATUS CACHE (Recommended Python Implementation) ---
# Optimized for high-frequency polling
CURRENT_STATUS = {
    "is_busy": False,
    "last_updated": time.time(),
    "active_job": None
}

def _update_global_status(job_id=None, status=None, step=None, progress=None, message=None, project_id=None, gallery_id=None, job_type=None):
    """
    Updates the lightweight global status dictionary for O(1) polling.
    """
    global CURRENT_STATUS
    CURRENT_STATUS["last_updated"] = time.time()
    
    # Determine if busy based on status
    is_active_state = status and status.lower() in ['processing', 'starting', 'rendering', 'encoding', 'uploading', 'initializing', 'queued']
    
    if is_active_state:
        CURRENT_STATUS["is_busy"] = status.lower() != 'queued' # Queued doesn't mean busy engine necessarily, but "processing" does.
        # Actually prompt says "Busy (Job Running)". Queued is waiting.
        
        if status.lower() == 'processing' or CURRENT_STATUS["is_busy"]:
             CURRENT_STATUS["is_busy"] = True

        # Only update active_job details if we have them and it's an active state
        if job_id and CURRENT_STATUS["is_busy"]:
            # If we don't have an active job or it's a different one, start fresh
            if not CURRENT_STATUS["active_job"] or CURRENT_STATUS["active_job"].get("id") != job_id:
                  CURRENT_STATUS["active_job"] = {
                    "id": job_id,
                    "project_id": project_id,
                    "gallery_id": gallery_id,
                    "status": status,
                    "step": step or "Initializing",
                    "progress": progress if progress is not None else 0,
                    "message": message or "Starting...",
                    "started_at": datetime.now().strftime("%H:%M:%S"),
                    "type": job_type if job_type else "image"
                }
            else:
                # Update existing
                current = CURRENT_STATUS["active_job"]
                if status: current["status"] = status
                if step: current["step"] = step
                if progress is not None: current["progress"] = progress
                if message: current["message"] = message
                # Don't overwrite IDs if not provided
                if project_id: current["project_id"] = project_id
                if gallery_id: current["gallery_id"] = gallery_id
                if job_type: current["type"] = job_type
                
    elif status and status.lower() in ['completed', 'failed', 'error', 'cancelled']:
        CURRENT_STATUS["is_busy"] = False
        # Preserve active_job info on failure so monitor can read it
        if CURRENT_STATUS["active_job"]:
             CURRENT_STATUS["active_job"]["status"] = status
             if message: CURRENT_STATUS["active_job"]["message"] = message
             if step: CURRENT_STATUS["active_job"]["step"] = step
             if progress is not None: CURRENT_STATUS["active_job"]["progress"] = progress
    elif status is None and job_id is None:
        # Reset
        CURRENT_STATUS["is_busy"] = False
        CURRENT_STATUS["active_job"] = None


def _build_ai_job_snapshot(job_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
    """Return a monitor-friendly snapshot of an AI render job."""
    status = str(job.get("status") or "queued").lower()
    progress = int(job.get("progress", 0) or 0)
    queued_at = job.get("queued_at")
    started_at = job.get("started_at")
    completed_at = job.get("completed_at")
    failed_at = job.get("failed_at")
    details = job.get("details") or {}
    if not isinstance(details, dict):
        details = {"raw": details}

    return {
        "job_id": job_id,
        "type": job.get("type") or job.get("render_type") or "ai render",
        "display_type": job.get("display_type") or "ai render",
        "render_type": job.get("render_type") or "AI_RENDER",
        "status": status,
        "project_id": job.get("project_id", 0),
        "gallery_id": job.get("gallery_id", 0),
        "user_id": job.get("user_id", 0),
        "progress": progress,
        "message": job.get("message") or "AI render job queued.",
        "current_step": job.get("current_step") or ("completed" if status == "completed" else status),
        "queued_at": queued_at,
        "started_at": started_at,
        "completed_at": completed_at,
        "failed_at": failed_at,
        "error": job.get("error"),
        "image_url": job.get("image_url"),
        "output_filename": job.get("output_filename"),
        "input_filename": job.get("input_filename"),
        "details": details,
        "queue_source": job.get("queue_source", "External AI Engine"),
        "is_ai_render": True,
        "engine_name": "External 4K + AI Engine",
        "engine_url": os.getenv("AI_RENDER_ENGINE_URL", "embedded://external-4k-ai-main"),
        "estimated_eta_minutes": job.get("estimated_eta_minutes"),
        "estimated_wait_minutes": job.get("estimated_wait_minutes"),
        "completed": status == "completed",
        "failed": status in {"failed", "error"},
    }


def _get_ai_jobs_snapshot() -> List[Dict[str, Any]]:
    with queue_lock:
        snapshot = [
            _build_ai_job_snapshot(job_id, dict(job))
            for job_id, job in active_jobs.items()
        ]

    def _sort_key(item: Dict[str, Any]):
        status_rank = 0 if item["status"] in {"processing", "queued"} else 1
        queued_at = item.get("queued_at") or ""
        started_at = item.get("started_at") or ""
        return (status_rank, queued_at, started_at, item["job_id"])

    snapshot.sort(key=_sort_key)

    queued = sum(1 for job in snapshot if job["status"] == "queued")
    processing = sum(1 for job in snapshot if job["status"] == "processing")
    completed = sum(1 for job in snapshot if job["status"] == "completed")
    failed = sum(1 for job in snapshot if job["status"] in {"failed", "error"})
    queued_jobs = [job for job in snapshot if job["status"] == "queued"]

    for idx, job in enumerate(queued_jobs, start=1):
        job["queue_position"] = idx
        if job.get("estimated_eta_minutes") is None:
            job["estimated_eta_minutes"] = round(max(1.0, idx * 2.0), 1)
        job["estimated_wait_minutes"] = job["estimated_eta_minutes"]

    return snapshot


def _graceful_shutdown(signum, frame):
    """
    Graceful shutdown handler - sends error status to all active jobs.
    Called when Ctrl+C or terminal close is detected.
    """
    global is_shutting_down
    
    if is_shutting_down:
        return  # Already shutting down
    
    is_shutting_down = True
    
    print(f"\n{COLOR_YELLOW}{'='*80}")
    print(f"⚠️  SHUTDOWN SIGNAL RECEIVED - Initiating graceful shutdown...")
    print(f"{'='*80}{COLOR_RESET}\n")
    
    # 1. Stop accepting new jobs
    print(f"🛑 Stopping new job acceptance...")
    
    # Get current queue status
    with queue_lock:
        queued_jobs = [(job_id, info) for job_id, info in active_jobs.items() if info.get('status') == 'queued']
        processing_jobs = [(job_id, info) for job_id, info in active_jobs.items() if info.get('status') == 'processing']
        total_jobs = len(queued_jobs) + len(processing_jobs)
    
    # Print queue summary
    print(f"\n📊 Current Queue Status:")
    print(f"   Total Active Jobs: {total_jobs}")
    print(f"   Queued Jobs: {len(queued_jobs)}")
    print(f"   Processing Jobs: {len(processing_jobs)}")
    
    if total_jobs == 0:
        print(f"\n{COLOR_GREEN}✓ Queue: NULL (No active jobs to cancel){COLOR_RESET}\n")
    else:
        print(f"\n📋 Job Details:")
    
    # 2. Send error status to all queued jobs
    if queued_jobs:
        print(f"\n📤 Sending error status to {len(queued_jobs)} queued job(s)...")
        for job_id, job_info in queued_jobs:
            job_type = job_info.get('type', 'IMAGE').upper()
            project_id = job_info.get('project_id', 'N/A')
            gallery_id = job_info.get('gallery_id', 'N/A')
            print(f"   → Job: {job_id[:8]}... | Type: {job_type} | Project: {project_id} | Gallery: {gallery_id}")
            try:
                _send_status_update(
                    project_id=job_info.get('project_id', 0),
                    gallery_id=job_info.get('gallery_id', 0),
                    user_id=0,
                    job_id=job_id,
                    status="error",
                    step="shutdown",
                    progress=0,
                    message="Sorry, the rendering process was interrupted.",
                    render_type=job_type
                )
                print(f"     ✓ Status 4 (Error) sent successfully")
            except Exception as e:
                print(f"     ⚠️  Failed to send status: {e}")
            
            # UPDATE LOCAL STATE
            with queue_lock:
                if job_id in active_jobs:
                    active_jobs[job_id]['status'] = 'failed'
                    active_jobs[job_id]['error'] = 'Server shutdown - job cancelled'
                    active_jobs[job_id]['failed_at'] = datetime.now().isoformat()
                    active_jobs[job_id]['message'] = 'Sorry, the rendering process was interrupted.'
    
    # 3. Send error status to currently processing job
    if processing_jobs:
        print(f"\n📤 Sending error status to {len(processing_jobs)} processing job(s)...")
        for job_id, job_info in processing_jobs:
            job_type = job_info.get('type', 'IMAGE').upper()
            project_id = job_info.get('project_id', 'N/A')
            gallery_id = job_info.get('gallery_id', 'N/A')
            print(f"   → Job: {job_id[:8]}... | Type: {job_type} | Project: {project_id} | Gallery: {gallery_id}")
            try:
                _send_status_update(
                    project_id=job_info.get('project_id', 0),
                    gallery_id=job_info.get('gallery_id', 0),
                    user_id=0,
                    job_id=job_id,
                    status="error",
                    step="shutdown",
                    progress=0,
                    message="Sorry, the rendering process was interrupted.",
                    render_type=job_type
                )
                print(f"     ✓ Status 4 (Error) sent successfully")
            except Exception as e:
                print(f"     ⚠️  Failed to send status: {e}")
            
            # UPDATE LOCAL STATE to ensure monitoring dashboard sees the failure immediately
            with queue_lock:
                if job_id in active_jobs:
                    active_jobs[job_id]['status'] = 'failed'
                    active_jobs[job_id]['error'] = 'Server shutdown - render interrupted'
                    active_jobs[job_id]['failed_at'] = datetime.now().isoformat()
                    active_jobs[job_id]['message'] = 'Sorry, the rendering process was interrupted.'

    
    # 4. Signal queue worker to stop
    print(f"Stopping render worker queue...")
    try:
        render_queue.put(None)  # Poison pill
        ai_service.shutdown()
    except:
        pass
    
    print(f"\n{COLOR_GREEN}✅ Graceful shutdown complete{COLOR_RESET}")
    print(f"{'='*80}\n")
    
    # Don't call sys.exit() - let the server shut down naturally
    # This prevents SystemExit from propagating through async operations

FAILED_INPUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "failed_inputs")
os.makedirs(FAILED_INPUTS_DIR, exist_ok=True)

# Maximum number of render attempts before marking a job as permanently failed
MAX_RENDER_ATTEMPTS = 3

def _save_failed_input(job_data: dict, error_msg: str, attempt: int):
    """
    Saves the failed render input JSON and error details to the failed_inputs/ folder.
    Files are named based on user_id and project_id for easy identification.
    """
    try:
        user_id = job_data.get('user_id', 'unknown')
        project_id = job_data.get('project_id', 'unknown')
        gallery_id = job_data.get('gallery_id', 'unknown')
        job_id = job_data.get('job_id', 'unknown')
        job_type = job_data.get('type', 'unknown')
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')

        # Base name: user_{user_id}_project_{project_id}_{timestamp}
        base_name = f"user_{user_id}_project_{project_id}_{timestamp_str}"

        # 1. Save the input JSON payload
        input_json_path = os.path.join(FAILED_INPUTS_DIR, f"{base_name}_input.json")
        payload_obj = job_data.get('payload')
        if payload_obj is not None:
            # Pydantic model → dict if possible
            try:
                payload_dict = payload_obj.dict() if hasattr(payload_obj, 'dict') else payload_obj
            except Exception:
                payload_dict = str(payload_obj)
            with open(input_json_path, 'w', encoding='utf-8') as f:
                json.dump(payload_dict, f, indent=2, ensure_ascii=False, default=str)
        else:
            with open(input_json_path, 'w', encoding='utf-8') as f:
                json.dump({"error": "payload was None"}, f, indent=2)

        # 2. Save the error details text file
        error_txt_path = os.path.join(FAILED_INPUTS_DIR, f"{base_name}_error.txt")
        with open(error_txt_path, 'w', encoding='utf-8') as f:
            f.write(f"{'='*60}\n")
            f.write(f"RENDER FAILURE REPORT\n")
            f.write(f"{'='*60}\n\n")
            f.write(f"Timestamp      : {datetime.now().isoformat()}\n")
            f.write(f"Job ID         : {job_id}\n")
            f.write(f"Job Type       : {job_type.upper()}\n")
            f.write(f"User ID        : {user_id}\n")
            f.write(f"Project ID     : {project_id}\n")
            f.write(f"Gallery ID     : {gallery_id}\n")
            f.write(f"Total Attempts : {attempt}/{MAX_RENDER_ATTEMPTS}\n")
            f.write(f"\n{'─'*60}\n")
            f.write(f"ERROR DETAILS:\n")
            f.write(f"{'─'*60}\n")
            f.write(f"{error_msg}\n")
            f.write(f"\n{'='*60}\n")

        print(f"{COLOR_YELLOW}📁 Failed input saved to: {FAILED_INPUTS_DIR}{COLOR_RESET}")
        print(f"   📄 Input JSON : {os.path.basename(input_json_path)}")
        print(f"   📄 Error File : {os.path.basename(error_txt_path)}")

    except Exception as save_err:
        print(f"{COLOR_RED}⚠️  Could not save failed input: {save_err}{COLOR_RESET}")


def render_queue_worker():
    """
    Background worker thread that processes render jobs one at a time from the queue.
    This prevents multiple render instances from running simultaneously.

    RETRY PIPELINE:
      • Each job is attempted up to MAX_RENDER_ATTEMPTS (3) times.
      • On attempts 1 and 2: failure is logged but NO error status is sent to
        the external API. The job is silently retried.
      • On the final attempt (3rd): if it still fails, the error status IS sent,
        the input JSON and failure reason are saved to failed_inputs/, and
        the worker moves on to the next job.
    """
    print(f"\n{'='*80}\n🔄 RENDER QUEUE WORKER STARTED (with retry pipeline: max {MAX_RENDER_ATTEMPTS} attempts)\n{'='*80}\n")
    
    while True:
        try:
            # Get next job from queue (blocks until a job is available)
            job_data = render_queue.get()
            
            if job_data is None:  # Poison pill to stop the worker
                print(f"{COLOR_YELLOW}🛑 Queue worker received shutdown signal{COLOR_RESET}")
                break
            
            # Check if shutting down
            if is_shutting_down:
                print(f"{COLOR_YELLOW}⚠️  Skipping job due to shutdown{COLOR_RESET}")
                render_queue.task_done()
                continue
            
            job_id = job_data['job_id']
            job_type = job_data['type']  # 'image' or 'video'
            
            # Update job status to processing
            with queue_lock:
                if job_id in active_jobs:
                    active_jobs[job_id]['status'] = 'processing'
                    active_jobs[job_id]['started_at'] = datetime.now().isoformat()
                    active_jobs[job_id]['progress'] = 0
                    active_jobs[job_id]['current_step'] = 'starting'
                    active_jobs[job_id]['message'] = 'Starting render process'
            
            # UPDATE GLOBAL STATUS
            _update_global_status(
                job_id=job_id, 
                status='processing',
                step='Starting render process',
                progress=0,
                message='Initializing engine...',
                project_id=job_data.get('project_id'),
                gallery_id=job_data.get('gallery_id'),
                job_type=job_type
            )

            
            print(f"\n{'='*80}\n🎬 PROCESSING JOB FROM QUEUE\n{'='*80}")
            print(f"Job ID: {job_id}")
            print(f"Type: {job_type.upper()}")
            print(f"Queue Size: {render_queue.qsize()} remaining")
            print(f"Max Attempts: {MAX_RENDER_ATTEMPTS}")
            print(f"{'='*80}\n")
            
            # ── RETRY LOOP ──────────────────────────────────────────────
            job_succeeded = False
            last_error_msg = ""
            
            for attempt in range(1, MAX_RENDER_ATTEMPTS + 1):
                is_final_attempt = (attempt == MAX_RENDER_ATTEMPTS)
                
                try:
                    if attempt > 1:
                        print(f"\n{'─'*60}")
                        print(f"🔄 RETRY ATTEMPT {attempt}/{MAX_RENDER_ATTEMPTS} for Job {job_id}")
                        print(f"{'─'*60}\n")
                        
                        # Brief pause before retry to let resources settle
                        time.sleep(3)
                        
                        # Reset job status for retry
                        with queue_lock:
                            if job_id in active_jobs:
                                active_jobs[job_id]['status'] = 'processing'
                                active_jobs[job_id]['progress'] = 0
                                active_jobs[job_id]['current_step'] = f'retry_{attempt}'
                                active_jobs[job_id]['message'] = f'Retrying render (attempt {attempt}/{MAX_RENDER_ATTEMPTS})...'
                        
                        _update_global_status(
                            job_id=job_id,
                            status='processing',
                            step=f'Retry attempt {attempt}/{MAX_RENDER_ATTEMPTS}',
                            progress=0,
                            message=f'Retrying render (attempt {attempt}/{MAX_RENDER_ATTEMPTS})...',
                            project_id=job_data.get('project_id'),
                            gallery_id=job_data.get('gallery_id'),
                            job_type=job_type
                        )
                    
                    # Execute the appropriate render function
                    # suppress_error_status=True on non-final attempts:
                    #   → prevents sending error status to API during retries
                    #   → re-raises exception so retry loop can catch it
                    # suppress_error_status=False on final attempt:
                    #   → normal error handling (sends error status to API)
                    suppress = not is_final_attempt
                    
                    if job_type == 'image':
                        _background_render_task(
                            job_data['project_id'],
                            job_data['gallery_id'],
                            job_data['payload'],
                            job_id,
                            job_data['user_id'],
                            suppress_error_status=suppress
                        )
                    elif job_type == 'video':
                        _background_video_render_task(
                            job_data['project_id'],
                            job_data['gallery_id'],
                            job_data['payload'],
                            job_id,
                            job_data['user_id'],
                            suppress_error_status=suppress
                        )
                    
                    # If we reach here, the render succeeded
                    job_succeeded = True
                    
                    # Mark job as completed
                    with queue_lock:
                        if job_id in active_jobs:
                            active_jobs[job_id]['status'] = 'completed'
                            active_jobs[job_id]['completed_at'] = datetime.now().isoformat()
                            active_jobs[job_id]['progress'] = 100
                            active_jobs[job_id]['current_step'] = 'completed'
                            active_jobs[job_id]['message'] = 'Render completed successfully'
                            if attempt > 1:
                                active_jobs[job_id]['message'] = f'Render completed successfully (after {attempt} attempts)'
                    
                    # UPDATE GLOBAL STATUS - FINISHED
                    _update_global_status(job_id=job_id, status='completed')

                    if attempt > 1:
                        print(f"\n{COLOR_GREEN}✅ Job {job_id} completed successfully on attempt {attempt}/{MAX_RENDER_ATTEMPTS}{COLOR_RESET}\n")
                    else:
                        print(f"\n{COLOR_GREEN}✅ Job {job_id} completed successfully{COLOR_RESET}\n")
                    
                    break  # Exit retry loop on success
                    
                except Exception as e:
                    last_error_msg = str(e)
                    
                    if not is_final_attempt:
                        # ── NOT THE FINAL ATTEMPT: log but DO NOT send fail status ──
                        print(f"\n{COLOR_YELLOW}⚠️  Job {job_id} failed on attempt {attempt}/{MAX_RENDER_ATTEMPTS}: {last_error_msg}{COLOR_RESET}")
                        print(f"{COLOR_YELLOW}   → Will retry ({MAX_RENDER_ATTEMPTS - attempt} attempt(s) remaining)...{COLOR_RESET}\n")
                        # Do NOT send status update — silent retry
                        continue
                    else:
                        # ── FINAL ATTEMPT FAILED: send fail status + save to failed_inputs ──
                        print(f"\n{COLOR_RED}{'='*80}")
                        print(f"❌ Job {job_id} PERMANENTLY FAILED after {MAX_RENDER_ATTEMPTS} attempts")
                        print(f"   Last error: {last_error_msg}")
                        print(f"{'='*80}{COLOR_RESET}\n")
                        
                        # Mark job as failed
                        with queue_lock:
                            if job_id in active_jobs:
                                active_jobs[job_id]['status'] = 'failed'
                                active_jobs[job_id]['error'] = last_error_msg
                                active_jobs[job_id]['failed_at'] = datetime.now().isoformat()
                                active_jobs[job_id]['progress'] = 0
                                active_jobs[job_id]['current_step'] = 'failed'
                                active_jobs[job_id]['message'] = f'Render failed after {MAX_RENDER_ATTEMPTS} attempts: {last_error_msg}'
                                active_jobs[job_id]['total_attempts'] = MAX_RENDER_ATTEMPTS
                        
                        # UPDATE GLOBAL STATUS - FAILED (only on final attempt)
                        _update_global_status(job_id=job_id, status='failed')
                        
                        # Save the failed input JSON + error details to failed_inputs/
                        import traceback
                        full_error_msg = f"{last_error_msg}\n\nFull Traceback:\n{traceback.format_exc()}"
                        _save_failed_input(job_data, full_error_msg, attempt)
                        
                        print(f"{COLOR_RED}❌ Job {job_id} failed permanently. Moving to next job.{COLOR_RESET}\n")
            
            # ── End of retry loop ────────────────────────────────────────
            render_queue.task_done()

                
        except Exception as e:
            print(f"{COLOR_RED}❌ Queue worker error: {str(e)}{COLOR_RESET}")
            import traceback
            traceback.print_exc()

# Start the queue worker thread when the application starts
queue_worker_thread = Thread(target=render_queue_worker, daemon=True)
queue_worker_thread.start()

# --- AI RENDER UTILITY FUNCTIONS ---

# AI Worker logic moved to AIRenderService

# --- GRACEFUL SHUTDOWN HANDLERS ---
# We use FastAPI's shutdown event instead of manual signal handling
# to avoid conflicts with Uvicorn's signal handling.



print(f"{COLOR_GREEN}✅ Graceful shutdown handlers registered via FastAPI{COLOR_RESET}")
print(f"   Press Ctrl+C to gracefully shutdown the server\n")

# --- DATABASE INITIALIZATION ---
# Automatically create tables if they don't exist in the render database
try:
    print(f"📊 Initializing database: render database")
    print(f"   Database URL: {session.DATABASE_URL}")
    models.Base.metadata.create_all(bind=session.engine)
    print(f"✅ Database tables ready (created or already exist)")
except Exception as e:
    print(f"⚠️  Warning: Could not initialize database tables: {e}")
    print(f"   You may need to run 'python init_database.py' manually")
    # Don't fail the application startup if database is not available
    # Some endpoints may still work without database

# --- APP INITIALIZATION ---
app = FastAPI(title="Z_Realty 4K Render Backend",
docs_url="/docs",
openapi_url="/openapi.json",
root_path="/render")

# --- LOGGING CONFIGURATION ---
class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Filter out 200 OK responses for /queue-status to reduce noise
        return record.getMessage().find("GET /queue-status") == -1

@app.on_event("startup")
async def configure_logging():
    # Filter out /queue-status endpoint logs from uvicorn access logger
    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
    
    # --- AI Render Service Setup ---
    ai_service.setup(active_jobs, queue_lock, _send_status_update, _update_global_status)
    ai_service.upload_to_blob_fallback = _upload_to_blob_storage

@app.on_event("shutdown")
def shutdown_event():
    """
    Handle application shutdown.
    """
    _graceful_shutdown(None, None)

# --- Include Auth Router ---
from app.routers import auth
app.include_router(auth.router, prefix="/auth", tags=["authentication"])

# --- API Key Authentication Dependency ---
# This ensures only authenticated users with valid API keys can access render endpoints
CurrentUser = Annotated[models.User, Depends(auth_service.get_user_from_api_key)]

# --- CONFIGURATION ---
# --- CONFIGURATION ---
ASSET_DOWNLOAD_DIR = os.getenv("ASSET_DOWNLOAD_DIR", "asset_downloads")
GLB_OUTPUT_DIR = os.getenv("GLB_OUTPUT_DIR", "output_glb")
RENDER_OUTPUT_DIR = os.getenv("RENDER_OUTPUT_DIR", "output_renders")
RUNTIME_DIR = os.path.join(tempfile.gettempdir(), "zrealty_backend_runtime")
ASSET_URL_MAP_FILE = os.path.join(ASSET_DOWNLOAD_DIR, "asset_url_map.json")
ENABLE_VIDEO_CULLING = True  # Set to False to skip VideoSceneOptimizer for video renders

# --- GODOT CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Check for both mono and non-mono versions
# Only use mono exe if GodotSharp assemblies directory actually exists
GODOT_BIN_DIR = os.path.join(SCRIPT_DIR, "godot", "bin")
GODOT_SHARP_DIR = os.path.join(GODOT_BIN_DIR, "GodotSharp")
DEFAULT_GODOT_EXE_MONO = os.path.join(GODOT_BIN_DIR, "godot.windows.editor.x86_64.mono.console.exe")
DEFAULT_GODOT_EXE_PLAIN = os.path.join(GODOT_BIN_DIR, "godot.windows.editor.x86_64.console.exe")

# Prefer plain (non-mono) exe first; only use mono if GodotSharp assemblies are present
if os.path.exists(DEFAULT_GODOT_EXE_PLAIN):
    DEFAULT_GODOT_EXE = DEFAULT_GODOT_EXE_PLAIN
elif os.path.exists(DEFAULT_GODOT_EXE_MONO) and os.path.isdir(GODOT_SHARP_DIR):
    DEFAULT_GODOT_EXE = DEFAULT_GODOT_EXE_MONO
elif os.path.exists(DEFAULT_GODOT_EXE_MONO):
    # Mono exe exists but GodotSharp is missing - use it anyway but warn
    print(f"{COLOR_YELLOW}⚠️  WARNING: Mono Godot exe found but GodotSharp directory is missing at: {GODOT_SHARP_DIR}")
    print(f"   This may cause '.NET assemblies not found' errors.{COLOR_RESET}")
    DEFAULT_GODOT_EXE = DEFAULT_GODOT_EXE_MONO
else:
    DEFAULT_GODOT_EXE = DEFAULT_GODOT_EXE_PLAIN  # Will fail later with a clear "file not found"

GODOT_EXE = os.getenv("GODOT_EXE", DEFAULT_GODOT_EXE)
GODOT_PROJECT_PATH = os.getenv("GODOT_PROJECT_PATH", os.path.join(SCRIPT_DIR, "godot_project"))

# --- CREATE DIRECTORIES ---
os.makedirs(ASSET_DOWNLOAD_DIR, exist_ok=True)
os.makedirs(GLB_OUTPUT_DIR, exist_ok=True)
os.makedirs(RENDER_OUTPUT_DIR, exist_ok=True)
os.makedirs(RUNTIME_DIR, exist_ok=True)

# --- API & TOKENS ---
# --- API & TOKENS ---
SAS_TOKEN = os.getenv("AZURE_SAS_TOKEN", "sv=2024-11-04&ss=bfqt&srt=sco&sp=rwdlacupiytfx&se=2026-11-18T20:34:53Z&st=2025-09-12T12:19:53Z&spr=https,http&sig=KNQs7rhe81AeQfnd%2BS4QMPWWo55VbNICTufFVYe5KhA%3D")
UPLOAD_API_URL = (
    os.getenv("UPLOAD_API_URL", "").strip()
    or os.getenv("AI_CALLBACK_API_URL", "").strip()
    or "http://216.48.178.133:4050/api/v1/Project/UploadProjectGalleryAI"
)
VIDEO_UPLOAD_API_URL = (
    os.getenv("VIDEO_UPLOAD_API_URL", "").strip()
    or os.getenv("AI_VIDEO_CALLBACK_API_URL", "").strip()
    or "http://216.48.178.133:4050/api/v1/Project/UploadProjectVideoAI"
)
# Internal monitor callback URL. Keep legacy names supported because the .env
# file in this repo uses MONITOR_UPLOAD_API_URL / MONITOR_VIDEO_UPLOAD_API_URL.
MONITORING_API_URL = (
    os.getenv("MONITOR_UPLOAD_API_URL", "").strip()
    or os.getenv("MONITORING_API_URL", "").strip()
)
API_KEY = os.getenv("API_KEY", "")
USE_API_KEY = os.getenv("USE_API_KEY", "false").lower() == "true"



# --- CORS MIDDLEWARE ---
origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://localhost:3002",
    "http://127.0.0.1:8005",
    "http://127.0.0.1:8006",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- STATUS MONITORING API ---
# Read-only endpoints for external monitoring system
from api_status import create_status_router
from scene_optimizer import SceneOptimizer, CULLING_LOGS_DIR
from scene_optimizer_video import VideoSceneOptimizer

status_router = create_status_router(active_jobs, render_queue, queue_lock, CURRENT_STATUS, ai_render_queue=ai_service.render_queue)
app.include_router(status_router)

# --- DATA MODELS (SCHEMA) ---
class Vector3(BaseModel):
    x: float
    y: float
    z: float

class ColorRGB(BaseModel):
    r: float
    g: float
    b: float

class DirectionalLight(BaseModel):
    intensity: float
    position: Vector3
    color: ColorRGB
    target: Optional[Vector3] = None
    cast_shadow: Optional[bool] = True

class AmbientLight(BaseModel):
    intensity: float

class GenerationPayload(BaseModel):
    floor_plan_data: str
    fov: float = 45.0
    directional_light: DirectionalLight
    ambient_light: Optional[AmbientLight] = None
    aspect_ratio: str
    render_quality: str
    # Some callers omit this field for image renders; keep it optional so the
    # request can still start instead of failing validation up front.
    timestamp: int = 0
    hdri_filename: Optional[str] = "kloppenheim_06_puresky_4k.exr"
    # Camera data fields (optional, will be extracted from JSON)
    threejs_camera: Optional[Dict[str, Any]] = None
    enable_status_updates: bool = True  # If True, send status updates to UploadGallerAI endpoint at each step
    
    class Config:
        extra = "allow"  # Allow extra fields that aren't in the model

# --- VIDEO-SPECIFIC DATA MODELS ---
class VideoAnimation(BaseModel):
    duration_seconds: float = 5.0
    fps: int = 30
    camera_position_start: Optional[Vector3] = None
    camera_position_end: Optional[Vector3] = None
    camera_target_start: Optional[Vector3] = None
    camera_target_end: Optional[Vector3] = None
    # Frame-by-frame keyframes array (if provided by frontend)
    # Each keyframe: {"frame": int, "position": {"x": float, "y": float, "z": float}, "target": {"x": float, "y": float, "z": float}}
    keyframes: Optional[List[Dict[str, Any]]] = None

class VideoDirectionalLight(BaseModel):
    intensity: float = 1.0
    position: Optional[Vector3] = None
    color: Optional[ColorRGB] = None
    target: Optional[Vector3] = None
    cast_shadow: Optional[bool] = False
    shadow_map_size: Optional[int] = 512
    shadow_bias: Optional[float] = 0

class VideoGenerationPayload(BaseModel):
    """Payload model for video rendering with camera animation data."""
    video_animation: Optional[VideoAnimation] = None  # Optional — if missing, Godot auto-generates a horizontal pan
    threejs_camera: Optional[Dict[str, Any]] = None
    coordinate_system: Optional[str] = "right_handed_y_up"
    target_coordinate_system: Optional[str] = "right_handed_z_up"
    ambient_light: Optional[AmbientLight] = None
    directional_light: Optional[VideoDirectionalLight] = None
    aspect_ratio: str = "16:9"
    render_quality: str = "hd"
    timestamp: int = 0
    floor_plan_data: Optional[Any] = None  # Can be string or dict — both formats accepted
    enable_status_updates: bool = True  # If True, send status updates to UploadGallerAI endpoint at each step
    
    class Config:
        extra = "allow"  # Allow extra fields that aren't in the model

# --- PAYLOAD VALIDATION FUNCTIONS ---

def _validate_image_payload(payload: GenerationPayload) -> tuple[bool, str]:
    """
    Validates image generation payload to ensure all required fields are present and valid.
    
    Returns:
        (is_valid, error_message) - True if valid, False with error message if invalid
    """
    try:
        # Check required fields
        if not payload.floor_plan_data:
            return False, "Missing required field: floor_plan_data"

        # Validate floor_plan_data is valid JSON
        try:
            if isinstance(payload.floor_plan_data, str):
                json.loads(payload.floor_plan_data)
        except json.JSONDecodeError:
            return False, "Invalid floor_plan_data: not valid JSON"
        
        # Validate directional_light
        if not payload.directional_light:
            return False, "Missing required field: directional_light"
        
        # Validate render quality
        valid_qualities = ['HD', 'FULL HD', 'FULLHD', 'QUAD HD', 'QUADHD', '2K', '4K', '6K', '8K', '12K', 'FAST_PREVIEW', 'LOW', 'MEDIUM']
        if payload.render_quality.upper() not in valid_qualities:
            return False, f"Invalid render_quality: {payload.render_quality}. Must be one of: {', '.join(valid_qualities)}"
        
        # Validate aspect ratio
        if not payload.aspect_ratio:
            return False, "Missing required field: aspect_ratio"

        return True, ""
        
    except Exception as e:
        return False, f"Validation error: {str(e)}"

def _validate_video_payload(payload: VideoGenerationPayload) -> tuple[bool, str]:
    """
    Validates video generation payload to ensure all required fields are present and valid.
    
    Returns:
        (is_valid, error_message) - True if valid, False with error message if invalid
    """
    try:
        # Check video_animation — if not present, Godot will auto-generate a horizontal pan
        if not payload.video_animation:
            print("ℹ️  No video_animation provided — Godot will auto-generate horizontal pan from camera position")
            return True, ""
        
        # Validate duration and fps
        if payload.video_animation.duration_seconds <= 0:
            return False, f"Invalid duration_seconds: {payload.video_animation.duration_seconds}. Must be greater than 0"
        
        if payload.video_animation.fps <= 0:
            return False, f"Invalid fps: {payload.video_animation.fps}. Must be greater than 0"
        
        # Validate duration is reasonable (max 60 seconds)
        if payload.video_animation.duration_seconds > 60:
            return False, f"Invalid duration_seconds: {payload.video_animation.duration_seconds}. Maximum allowed is 60 seconds"
        
        # Validate fps is reasonable (max 60 fps)
        if payload.video_animation.fps > 60:
            return False, f"Invalid fps: {payload.video_animation.fps}. Maximum allowed is 60 fps"
        
        # Check for camera animation data (keyframes or start/end positions)
        has_keyframes = payload.video_animation.keyframes and len(payload.video_animation.keyframes) > 0
        has_start_end = (
            payload.video_animation.camera_position_start is not None and
            payload.video_animation.camera_position_end is not None
        )
        
        if not has_keyframes and not has_start_end:
            # No keyframes or start/end — Godot will auto-generate from camera position
            print("ℹ️  No keyframes or start/end positions — Godot will auto-generate from camera")
        
        # Validate render quality
        valid_qualities = ['HD', 'FULL HD', 'FULLHD', 'QUAD HD', 'QUADHD', '2K', '4K', '6K', '8K', '12K', 'FAST_PREVIEW', 'LOW', 'MEDIUM']
        if payload.render_quality.upper() not in valid_qualities:
            return False, f"Invalid render_quality: {payload.render_quality}. Must be one of: {', '.join(valid_qualities)}"
        
        # Validate aspect ratio
        if not payload.aspect_ratio:
            return False, "Missing required field: aspect_ratio"
        
        return True, ""
        
    except Exception as e:
        return False, f"Validation error: {str(e)}"

# --- ASSET HANDLING & URL LOCALIZATION ---

def _load_asset_url_map() -> dict:
    """Loads the URL-to-local-file mapping from JSON file."""
    if os.path.exists(ASSET_URL_MAP_FILE):
        try:
            with open(ASSET_URL_MAP_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"{COLOR_YELLOW}Warning: Could not load asset URL map: {e}. Starting with empty map.{COLOR_RESET}")
            return {}
    return {}

def _save_asset_url_map(url_map: dict):
    """Saves the URL-to-local-file mapping to JSON file."""
    try:
        with open(ASSET_URL_MAP_FILE, 'w', encoding='utf-8') as f:
            json.dump(url_map, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"{COLOR_YELLOW}Warning: Could not save asset URL map: {e}{COLOR_RESET}")

def _get_cached_asset_path(url: str) -> Optional[str]:
    """
    Checks if URL exists in the mapping and returns local path if file exists.
    THREAD-SAFE: Uses a lock to ensure atomic read/write.
    """
    with map_lock:  # <--- LOCK START
        url_map = _load_asset_url_map()
        if url in url_map:
            local_path = url_map[url]
            # Check if file actually exists
            if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                return local_path
            else:
                # File missing, remove from map
                print(f"{COLOR_YELLOW}  ↳ Cached file missing, removing from map: {os.path.basename(local_path)}{COLOR_RESET}")
                del url_map[url]
                _save_asset_url_map(url_map)
        return None
    # <--- LOCK RELEASES AUTOMATICALLY HERE

def _add_to_asset_url_map(url: str, local_path: str):
    """
    Adds a URL-to-local-path mapping to the JSON file.
    THREAD-SAFE: Uses a lock to ensure atomic read-update-write.
    """
    with map_lock:  # <--- LOCK START
        url_map = _load_asset_url_map()
        # Only update if it's new or changed to avoid unnecessary writes
        if url not in url_map or url_map[url] != local_path:
            url_map[url] = local_path
            _save_asset_url_map(url_map)
    # <--- LOCK RELEASES AUTOMATICALLY HERE

import struct

def _check_glb_for_webp(filepath: str) -> bool:
    """
    Checks if a GLB file contains the EXT_texture_webp extension.
    Uses direct binary inspection of the JSON chunk.
    """
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, 'rb') as f:
            # Header: magic (4), version (4), length (4)
            magic = f.read(4)
            if magic != b'glTF': return False
            f.read(8) # Skip version and total length
            
            # Chunk 0 (JSON): length (4), type (4)
            chunk_length = struct.unpack('<I', f.read(4))[0]
            chunk_type = f.read(4)
            if chunk_type != b'JSON': return False
            
            json_bytes = f.read(chunk_length)
            json_text = json_bytes.decode('utf-8', errors='ignore')
            
            # Look for the extension string
            return "EXT_texture_webp" in json_text
    except Exception as e:
        print(f"{COLOR_YELLOW}  ⚠️  Warning: Could not inspect GLB {os.path.basename(filepath)}: {e}{COLOR_RESET}")
        return False

def _repair_glb_file(filepath: str):
    """
    Godot-only pipeline: WebP repair is not performed here.
    This function logs the condition and leaves the file unchanged.
    """
    try:
        print(f"{COLOR_YELLOW}  ⚠️  WebP detected in GLB, but automatic repair is disabled in the Godot pipeline:{COLOR_RESET} {os.path.basename(filepath)}")
        return False
    except Exception as e:
        print(f"{COLOR_RED}  ❌ Repair error: {e}{COLOR_RESET}")
        return False

def _check_glb_for_draco(filepath: str) -> bool:
    """
    Checks if a GLB file contains Draco mesh compression.
    """
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(4)
            if magic != b'glTF':
                return False
            f.read(8)
            chunk_length = struct.unpack('<I', f.read(4))[0]
            chunk_type = f.read(4)
            if chunk_type != b'JSON':
                return False
            json_bytes = f.read(chunk_length)
            json_text = json_bytes.decode('utf-8', errors='ignore')
            return "KHR_draco_mesh_compression" in json_text
    except Exception as e:
        print(f"{COLOR_YELLOW}  ⚠️  Warning: Could not inspect GLB {os.path.basename(filepath)}: {e}{COLOR_RESET}")
        return False

def _build_godot_compatible_glb_path(source_url: str, local_path: str) -> str:
    """
    Builds a stable output path for a Godot-compatible GLB copy.
    """
    compat_dir = os.path.abspath(os.path.join(ASSET_DOWNLOAD_DIR, "_godot_compatible"))
    os.makedirs(compat_dir, exist_ok=True)

    source_key = source_url or local_path or ""
    digest = hashlib.sha1(source_key.encode("utf-8", errors="ignore")).hexdigest()[:10]
    base_name = os.path.splitext(os.path.basename(local_path))[0] or "asset"
    return os.path.join(compat_dir, f"{digest}_{base_name}_nodraco.glb")

def _convert_draco_glb_to_plain_glb(input_path: str, output_path: str) -> tuple[bool, str]:
    """
    Uses a small Python helper to decode Draco-compressed meshes into a plain GLB.
    """
    script_path = os.path.join(SCRIPT_DIR, "tools", "decompress_draco_glb.py")
    if not os.path.exists(script_path):
        return False, f"Missing Draco conversion helper: {script_path}"

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        timeout_seconds = int(os.getenv("DRACO_GLB_CONVERSION_TIMEOUT_SECONDS", "300"))
        result = subprocess.run(
            [sys.executable, script_path, input_path, output_path],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            if not message:
                message = f"Node helper exited with code {result.returncode}"
            return False, message
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False, "Draco conversion finished but output file was not created"
        return True, ""
    except FileNotFoundError:
        return False, "Python interpreter was not found"
    except subprocess.TimeoutExpired:
        return False, f"Draco conversion timed out after {timeout_seconds}s"
    except Exception as e:
        return False, str(e)

def _convert_glb_webp_textures_to_png(input_path: str, output_path: str) -> tuple[bool, str]:
    """
    Rewrites embedded WebP textures in a GLB as PNGs.
    """
    script_path = os.path.join(SCRIPT_DIR, "tools", "convert_glb_webp_to_png.py")
    if not os.path.exists(script_path):
        return False, f"Missing WebP conversion helper: {script_path}"

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        result = subprocess.run(
            [sys.executable, script_path, input_path, output_path],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=int(os.getenv("GLB_WEBP_CONVERSION_TIMEOUT_SECONDS", "300")),
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            if not message:
                message = f"Python helper exited with code {result.returncode}"
            return False, message
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return False, "WebP conversion finished but output file was not created"
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "WebP conversion timed out"
    except Exception as e:
        return False, str(e)

def _ensure_godot_compatible_glb(source_url: str, local_path: str) -> str:
    """
    Converts Draco-compressed GLBs and embedded WebP textures into a Godot-friendly GLB.
    Returns the original path if conversion is not needed or fails.
    """
    if not local_path or not isinstance(local_path, str):
        return local_path
    if not local_path.lower().endswith(".glb"):
        return local_path
    draco_present = _check_glb_for_draco(local_path)
    webp_present = _check_glb_for_webp(local_path)
    if not draco_present and not webp_present:
        return local_path

    current_path = local_path
    show_details = os.getenv("ASSETS_DETAIL_PRINT", "false").lower() == "true"

    if draco_present:
        compatible_path = _build_godot_compatible_glb_path(source_url, local_path)
        needs_draco_convert = (
            not os.path.exists(compatible_path)
            or os.path.getsize(compatible_path) == 0
            or _check_glb_for_draco(compatible_path)
            or _check_glb_for_webp(compatible_path)
        )
        if needs_draco_convert:
            if show_details:
                print(f"{COLOR_YELLOW}  ↻ Draco GLB detected, converting for Godot:{COLOR_RESET} {os.path.basename(local_path)}")
            ok, error = _convert_draco_glb_to_plain_glb(current_path, compatible_path)
            if not ok:
                print(f"{COLOR_YELLOW}  ⚠️  Draco conversion failed, keeping original GLB:{COLOR_RESET} {os.path.basename(local_path)} ({error})")
                return local_path
        current_path = compatible_path

    if _check_glb_for_webp(current_path):
        png_tmp_path = current_path + ".pngtmp"
        if show_details:
            print(f"{COLOR_YELLOW}  ↻ WebP textures detected, converting to PNG:{COLOR_RESET} {os.path.basename(current_path)}")
        ok, error = _convert_glb_webp_textures_to_png(current_path, png_tmp_path)
        if ok:
            os.replace(png_tmp_path, current_path)
        else:
            print(f"{COLOR_YELLOW}  ⚠️  WebP texture conversion failed, keeping current GLB:{COLOR_RESET} {os.path.basename(current_path)} ({error})")
            if os.path.exists(png_tmp_path):
                try:
                    os.remove(png_tmp_path)
                except OSError:
                    pass
            if draco_present and current_path != local_path:
                # Keep the Draco-decoded file even if texture conversion failed.
                if source_url:
                    _add_to_asset_url_map(source_url, current_path)
                return current_path
            return local_path

    if source_url:
        _add_to_asset_url_map(source_url, current_path)
    return current_path

def _convert_glb_path_to_url(glb_path: str) -> str:
    """
    Converts a relative GLB path to a full Azure Blob Storage URL.
    
    Input: glb-assets/Sofa/Double_Seater_Sofa/Nordic_Sofa_HP.glb
    Output: https://zrealtystoragedev.blob.core.windows.net/glb-assets/Sofa/Double_Seater_Sofa/Nordic_Sofa_HP.glb
    
    Does NOT modify the filename (trusts input).
    """
    if not glb_path or not isinstance(glb_path, str):
        return ""
    
    # Add base URL prefix
    base_url = "https://zrealtystoragedev.blob.core.windows.net/"
    
    # Remove leading slash if present
    if glb_path.startswith("/"):
        glb_path = glb_path[1:]
    
    # Construct full URL
    full_url = f"{base_url}{glb_path}"
    
    return full_url

def _timed_download_worker(url: str, context: str = ""):
    """Worker function to download a single file, time it, and provide context with retries."""
    start_time = time.monotonic()
    
    base_url_prefix = "https://zrealtystoragedev.blob.core.windows.net/"
    cleaned_url = url.strip().strip('"')
    if not cleaned_url or not isinstance(cleaned_url, str) or not cleaned_url.startswith(base_url_prefix):
        return url, "", 0.0, None

    # Check if URL is already cached
    cached_path = _get_cached_asset_path(cleaned_url)
    if cached_path:
        # --- Also check cached GLBs for unsupported WebP textures ---
        if cached_path.lower().endswith('.glb'):
            if _check_glb_for_webp(cached_path):
                _repair_glb_file(cached_path)
            cached_path = _ensure_godot_compatible_glb(cleaned_url, cached_path)
        
        duration = time.monotonic() - start_time
        print(f"{COLOR_GREEN}✓ {context} Using cached:{COLOR_RESET} {os.path.basename(cleaned_url)} {COLOR_YELLOW}({duration:.3f}s){COLOR_RESET}")
        return url, cached_path, duration, None

    # URL not cached, proceed with download
    last_error = None
    for attempt in range(3):
        try:
            download_url = f"{cleaned_url}?{SAS_TOKEN}" if '?' not in cleaned_url else f"{cleaned_url}&{SAS_TOKEN.lstrip('?')}"
            
            # Use configured per-asset timeout (default 150s)
            req_timeout = int(os.getenv("ASSET_INDIVIDUAL_TIMEOUT_SECONDS", "150"))
            response = requests.get(download_url, timeout=req_timeout)
            response.raise_for_status()
            
            ext = os.path.splitext(urlparse(cleaned_url).path)[1]
            safe_name = os.path.basename(urlparse(cleaned_url).path).replace("_LR_", "_HR_")
            unique_prefix = str(uuid.uuid4())[:8]
            filename = f"{unique_prefix}_{safe_name}"

            local_path = os.path.abspath(os.path.join(ASSET_DOWNLOAD_DIR, filename))
            
            with open(local_path, "wb") as f:
                f.write(response.content)

            # --- Optional GLB compatibility check ---
            if cleaned_url.lower().endswith('.glb'):
                if _check_glb_for_webp(local_path):
                    _repair_glb_file(local_path)
                local_path = _ensure_godot_compatible_glb(cleaned_url, local_path)


            # Save URL-to-local-path mapping
            _add_to_asset_url_map(cleaned_url, local_path)
            
            duration = time.monotonic() - start_time
            
            # CHECK ENV VAR FOR DETAILED PRINTING
            show_details = os.getenv("ASSETS_DETAIL_PRINT", "false").lower() == "true"
            if show_details:
                print(f"{COLOR_GREEN}✓ {context} Downloaded:{COLOR_RESET} {os.path.basename(cleaned_url)} {COLOR_YELLOW}(Attempt {attempt + 1}/3, {duration:.2f}s){COLOR_RESET}")
            
            return url, local_path, duration, None

        except requests.RequestException as e:
            last_error = e
            if attempt < 2:
                # Only print retry warnings if detailed printing is on, OR if it's a significant error?
                # Keeping retry warnings is probably good, but maybe make them less intrusive?
                # For now, respecting the "heaviness" request, let's look at show_details
                show_details = os.getenv("ASSETS_DETAIL_PRINT", "false").lower() == "true"
                if show_details:
                    print(f"{COLOR_YELLOW}  ↳ Attempt {attempt + 1} failed for {os.path.basename(cleaned_url)}. Retrying in 2s... ({e}){COLOR_RESET}")
                time.sleep(2)
    
    duration = time.monotonic() - start_time
    return url, "", duration, last_error

def _download_asset_with_fallback(original_url: str, use_high_poly: bool = True):
    """
    Downloads assets directly.
    The 'use_high_poly' flag is kept for compatibility but the URL resolution 
    is now handled by the API master data logic beforehand.
    """
    # CHECK ENV VAR FOR DETAILED PRINTING
    show_details = os.getenv("ASSETS_DETAIL_PRINT", "false").lower() == "true"

    if not original_url:
        return "", ""

    # Skip SVG and thumbnail files - we don't need these for rendering
    if any(skip in original_url.lower() for skip in ['.svg', 'thumbnail', 'svg_']):
        if show_details:
            print(f"{COLOR_YELLOW}⊘ Skipping:{COLOR_RESET} {os.path.basename(original_url)} (SVG/Thumbnail not needed)")
        return original_url, ""
    
    # Generic Download Logic
    # We trust that the URL provided (e.g. from API resolution) is the correct one.
    file_type = "Asset"
    if original_url.lower().endswith('.glb'):
        file_type = "GLB"
    elif "/textures/" in original_url.lower():
        file_type = "Texture"
    elif original_url.lower().endswith('.obj'):
        file_type = "OBJ"
    
    if show_details:
        print(f"{COLOR_BLUE}→ Processing {file_type}:{COLOR_RESET} {os.path.basename(original_url)}")
        
    url, local_path, duration, error = _timed_download_worker(original_url, file_type)
    
    if error and show_details:
        print(f"{COLOR_RED}[X] Download Failed:{COLOR_RESET} {os.path.basename(url)} {COLOR_YELLOW}({duration:.2f}s) - {error}{COLOR_RESET}")
        
    return original_url, local_path

def _parse_texture_urls_from_mtl(mtl_filepath: str) -> Set[str]:
    """Scans a local MTL file and extracts all external texture URLs."""
    urls = set()
    texture_keys = ('map_Kd', 'map_Ks', 'map_Ka', 'map_Bump', 'bump', 'map_d', 'map_Pr', 'map_Pm', 'map_Ke', 'map_bump', 'map_disp', 'map_Disp')
    try:
        with open(mtl_filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                parts = line.split()
                if not parts or parts[0] not in texture_keys:
                    continue
                
                for part in parts[1:]:
                    cleaned_part = part.strip('\"\'')
                    if cleaned_part.startswith(('http://', 'https://')):
                        urls.add(cleaned_part)
                        break
    except Exception as e:
        print(f"{COLOR_RED}ERROR parsing MTL for texture URLs {os.path.basename(mtl_filepath)}: {e}{COLOR_RESET}")
    return urls

def _update_mtl_texture_paths(mtl_filepath: str, download_map: dict):
    """Updates texture URLs inside a local MTL file to local basenames."""
    print(f"{COLOR_YELLOW}  -> Finalizing texture paths in: {os.path.basename(mtl_filepath)}{COLOR_RESET}")
    try:
        with open(mtl_filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        new_lines, was_updated = [], False
        for line in lines:
            for original_url, local_path in list(download_map.items()):
                if local_path and original_url in line:
                    basename = os.path.basename(local_path)
                    line = line.replace(original_url, basename)
                    was_updated = True
                    break
            new_lines.append(line)
        
        if was_updated:
            with open(mtl_filepath, 'w', encoding='utf-8') as f:
                f.writelines(new_lines)
    except Exception as e:
        print(f"{COLOR_RED}ERROR updating MTL file {mtl_filepath}: {e}{COLOR_RESET}")

# --- Paste this corrected function into your main.py ---

def _download_and_localize_assets(plan: dict, use_high_res: bool = True, logger: Optional[RenderLogger] = None):
    """
    Corrected workflow with HP support:
    1. Recursively collect ALL URLs from the plan data first.
    2. Download primary OBJ/MTL for items/holes (converting to HP if available) and all textures.
    3. Parse downloaded MTLs to find and download any secondary texture assets.
    4. Update MTLs to use local texture paths.
    5. Update the main floorplan dict to use local paths.
    """
    if logger:
        logger.start_asset_download()
    
    # --- FETCH MASTER DATA (Once per run) ---
    _fetch_master_data_from_api()
    
    all_urls_to_find = set()
    
    # Store ID -> URL mappings so we can look them up later during replacement
    asset_id_to_url = {}
    texture_id_to_urls = {}
    
    # NEW: Store input_url -> resolved_hp_url mapping
    input_url_to_hp = {}
    
    # If plan is a string, parse it first so we can scan the object structure
    is_json_string = False
    if isinstance(plan, str):
        try:
            plan_data = json.loads(plan)
            is_json_string = True
        except Exception:
            plan_data = plan # Fallback
    else:
        plan_data = plan

    layer_to_scan = plan_data

    # Helper to recursively find all URLs in a data structure
    def collect_urls_recursive(data_obj, url_set):
        if isinstance(data_obj, str):
            # Case 1: The string is a URL itself
            if data_obj.startswith('https://'):
                # --- NEW: Try URL resolution via Master API ---
                resolved = _resolve_hp_url_from_url(data_obj, show_details=True)
                if isinstance(resolved, list):
                    for r in resolved: url_set.add(r)
                    input_url_to_hp[data_obj] = resolved
                else:
                    target = resolved if resolved else data_obj
                    url_set.add(target)
                    if resolved: input_url_to_hp[data_obj] = resolved
            # Case 2: The string is a JSON list of URLs
            elif data_obj.strip().startswith('[') and data_obj.strip().endswith(']'):
                try:
                    parsed_list = json.loads(data_obj)
                    if isinstance(parsed_list, list):
                        for item in parsed_list:
                            if isinstance(item, str) and item.startswith('https://'):
                                # --- NEW: Try URL resolution via Master API ---
                                resolved = _resolve_hp_url_from_url(item, show_details=True)
                                if isinstance(resolved, list):
                                    for r in resolved: url_set.add(r)
                                    input_url_to_hp[item] = resolved
                                else:
                                    target = resolved if resolved else item
                                    url_set.add(target)
                                    if resolved: input_url_to_hp[item] = resolved
                except (json.JSONDecodeError, TypeError):
                    pass # Ignore strings that look like lists but aren't valid JSON
            # Case 3: The string is a relative GLB path (glb-assets/...)
            elif data_obj.startswith('glb-assets/'):
                # --- NEW: Try URL resolution via Master API first ---
                resolved = _resolve_hp_url_from_url(data_obj, show_details=True)
                if resolved:
                    url_set.add(resolved)
                else:
                    # Convert relative GLB path to full URL
                     full_url = _convert_glb_path_to_url(data_obj)
                     if full_url:
                         url_set.add(full_url)
                         # Add reverse mapping for input_url_to_hp
                         input_url_to_hp[data_obj] = full_url

            # Case 4: The string is a relative Texture path (textures/...)
            elif data_obj.startswith('textures/'):
                 # Convert relative texture path to full URL
                 full_url = f"https://zrealtystoragedev.blob.core.windows.net/{data_obj}"
                 url_set.add(full_url)
                 input_url_to_hp[data_obj] = full_url # CRITICAL: Map relative path to full URL for replacement
                 if logger:
                     print(f"  Found relative texture path: {data_obj} -> {full_url}")

        elif isinstance(data_obj, list):
            for item in data_obj:
                collect_urls_recursive(item, url_set)
        elif isinstance(data_obj, dict):
            # Priority 1: Check if we have manual URLs and try to resolve HP from them
            found_manual_url = False
            asset_urls_dict = data_obj.get('asset_urls', {}) if isinstance(data_obj.get('asset_urls'), dict) else {}
            
            # Check GLB_File_URL first as primary manual URL
            glb_path = asset_urls_dict.get('GLB_File_URL') or data_obj.get('GLB_File_URL') or data_obj.get('glb_Url') or data_obj.get('glb_url')
            
            # 1. Try Resolving via URL (As requested by User)
            if glb_path and isinstance(glb_path, str):
                resolved = _resolve_hp_url_from_url(glb_path, show_details=True)
                if resolved:
                    url_set.add(resolved)
                    input_url_to_hp[glb_path] = resolved
                    found_manual_url = True
            
            # Priority 2: Fallback - collect all direct URLs found in asset_urls
            if not found_manual_url and asset_urls_dict:
                for key, value in asset_urls_dict.items():
                    if isinstance(value, str) and value.startswith('https://'):
                        resolved = _resolve_hp_url_from_url(value, show_details=True)
                        if isinstance(resolved, list):
                            for r in resolved: url_set.add(r)
                            input_url_to_hp[value] = resolved
                        elif resolved:
                            url_set.add(resolved)
                            input_url_to_hp[value] = resolved
                        else:
                            url_set.add(value)
            
            # Recursively process all values
            for key, value in data_obj.items():
                if found_manual_url:
                    # IGNORE specific keys if we have resolved this asset (Prevent manual low/med poly downloads/recursion)
                    if key in ['glb_Url', 'GLB_File_URL', 'glb_url', 'lowPoly_Glb', 'highPoly_Glb', 'mediumPoly_Glb', 'asset_urls']:
                        continue
                    if isinstance(value, str) and (value.startswith('glb-assets/') or value.lower().endswith('.glb')):
                        continue
                
                collect_urls_recursive(value, url_set)
                
            # Texture Library ID (Processed independently of Asset ID)
            if 'textureLibrary_Id' in data_obj:
                t_id = data_obj.get('textureLibrary_Id')
                if t_id:
                    resolved_urls = _resolve_texture_urls(t_id, show_details=True)
                    if resolved_urls:
                        texture_id_to_urls[t_id] = resolved_urls
                        for u in resolved_urls:
                            url_set.add(u)

    # Phase 1: Collect ALL URLs from the entire plan structure
    collect_urls_recursive(layer_to_scan, all_urls_to_find)

    # Phase 2: Download all collected assets
    print(f"\n{COLOR_BLUE}--- Asset Download Step 1: Found {len(all_urls_to_find)} unique assets. Starting download... ---{COLOR_RESET}")

    download_map = {}
    
    # Use manual executor management instead of 'with' block to allow non-blocking shutdown
    executor = ThreadPoolExecutor(max_workers=16)
    try:
        futures = {executor.submit(_download_asset_with_fallback, url, use_high_res): url for url in all_urls_to_find}
        
        # --- IMPLEMENT ASSET DOWNLOAD TIMEOUT (Configurable via .env, default 2.5 mins) ---
        start_time = time.monotonic()
        # Default to 150 seconds if not set
        TIMEOUT_SECONDS = int(os.getenv("ASSET_DOWNLOAD_TIMEOUT_SECONDS", "150"))
        PRINT_INTERVAL = 10
        next_print_time = start_time + PRINT_INTERVAL
        
        completed_count = 0
        total_count = len(futures)
        
        # Collect futures as they complete, but with a total loop timeout
        # We use a while loop to check for timeout and print status
        pending = set(futures.keys())
        
        while pending:
            # Calculate time remaining
            now = time.monotonic()
            elapsed = now - start_time
            
            if elapsed > TIMEOUT_SECONDS:
                print(f"\n{COLOR_YELLOW}⚠️  ASSET DOWNLOAD TIMEOUT REACHED ({TIMEOUT_SECONDS}s){COLOR_RESET}")
                print(f"  Proceeding with {completed_count}/{total_count} assets downloaded.")
                # Force non-blocking shutdown to proceed immediately
                # cancel_futures=True stops pending tasks. wait=False ignores running tasks.
                executor.shutdown(wait=False, cancel_futures=True)
                break
                
            # Wait for a short interval to allow printing status
            # We use wait() on the whole set, but with a short timeout
            # Note: wait() returns (done, not_done)
            done_now, pending_now = wait(pending, timeout=min(1.0, TIMEOUT_SECONDS - elapsed))
            
            # Process completed ones
            for future in done_now:
                pending.remove(future)
                completed_count += 1
                try:
                    original_url, final_path = future.result()
                    download_map[original_url] = final_path
                    
                    # Log asset
                    if logger and original_url:
                        asset_name = os.path.basename(original_url)
                        cached = final_path and os.path.exists(final_path) if final_path else False
                        logger.add_asset(original_url, asset_name, final_path, cached)
                except Exception as e:
                    print(f"{COLOR_RED}Error in initial download worker for {futures[future]}: {e}{COLOR_RESET}")

            # Print Status update
            if time.monotonic() >= next_print_time:
                show_details = os.getenv("ASSETS_DETAIL_PRINT", "false").lower() == "true"
                if not show_details:
                    print(f"⏳ Assets downloading ({int(elapsed)}s)...")
                next_print_time = time.monotonic() + PRINT_INTERVAL
                
    finally:
        # Ensure executor is eventually cleaned up, but don't block if we already shut it down
        try:
            # If we broke due to timeout, executor might be shut down already or running zombie threads.
            # Calling shutdown again with wait=False is safe.
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    # Phase 3: Parse MTLs and download secondary item/hole textures
    item_textures_to_download = set()
    mtl_files_to_update = []
    
    # Find which of our downloaded files are MTLs
    for url, local_path in download_map.items():
        if local_path and local_path.lower().endswith('.mtl'):
            mtl_files_to_update.append(local_path)
            print(f"\n{COLOR_BLUE}--- Parsing MTL '{os.path.basename(local_path)}' for associated textures... ---{COLOR_RESET}")
            texture_urls_in_mtl = _parse_texture_urls_from_mtl(local_path)
            if texture_urls_in_mtl:
                # Find textures that we haven't already downloaded
                new_textures = texture_urls_in_mtl - download_map.keys()
                if new_textures:
                    print(f"  Found {len(new_textures)} new texture(s) to download.")
                    item_textures_to_download.update(new_textures)
                else:
                    print("  All associated textures already accounted for.")
            else:
                print("  No external texture URLs found in this MTL.")
    
    if item_textures_to_download:
        print(f"\n{COLOR_BLUE}--- Asset Download Step 2: Downloading {len(item_textures_to_download)} additional textures discovered in MTLs ---{COLOR_RESET}")
        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {executor.submit(_download_asset_with_fallback, url, use_high_res): url for url in item_textures_to_download}
            for future in as_completed(futures):
                try:
                    original_url, final_path = future.result()
                    download_map[original_url] = final_path
                    
                    # Log asset
                    if logger and original_url:
                        asset_name = os.path.basename(original_url)
                        cached = final_path and os.path.exists(final_path) if final_path else False
                        logger.add_asset(original_url, asset_name, final_path, cached)
                except Exception as e:
                    print(f"{COLOR_RED}Error in texture download worker for {futures[future]}: {e}{COLOR_RESET}")

    # Step 4: Update downloaded MTL files with local texture paths
    if mtl_files_to_update:
        print(f"\n{COLOR_BLUE}--- Asset Processing: Updating MTL files with local texture paths ---{COLOR_RESET}")
        for mtl_path in mtl_files_to_update:
            _update_mtl_texture_paths(mtl_path, download_map)

    # Step 5: Replace all URLs in the main floorplan dict with their local paths
    def replace_urls_in_plan(data_obj):
        if isinstance(data_obj, str):
            # Case 1: The string is a direct URL match
            test_url = input_url_to_hp.get(data_obj, data_obj)
            if isinstance(test_url, list):
                # If resolved to multiple HR textures, return local paths as a JSON list or similar
                # For MTL/Texture mapping, we usually expect a single local path. 
                # If it's a list, the renderer might need it differently. 
                # For now, let's return the first available local path if it's a single string context.
                local_paths = [download_map[u] for u in test_url if u in download_map]
                if local_paths:
                    return local_paths[0] if len(local_paths) == 1 else json.dumps(local_paths)
                return data_obj
            elif test_url in download_map:
                return download_map[test_url]
                
            # Case 2: The string is a relative GLB path - convert to full URL and look up
            if data_obj.startswith('glb-assets/'):
                test_url = input_url_to_hp.get(data_obj)
                if isinstance(test_url, list):
                    local_paths = [download_map[u] for u in test_url if u in download_map]
                    if local_paths: return json.dumps(local_paths) if len(local_paths) > 1 else local_paths[0]
                elif not test_url:
                    full_url = _convert_glb_path_to_url(data_obj)
                    test_url = input_url_to_hp.get(full_url, full_url)
                
                if not isinstance(test_url, list) and test_url in download_map:
                    return download_map[test_url]
                elif isinstance(test_url, list):
                    local_paths = [download_map[u] for u in test_url if u in download_map]
                    if local_paths: return json.dumps(local_paths) if len(local_paths) > 1 else local_paths[0]
            
            # Case 3: The string is a relative Texture path - convert to full URL and look up
            if data_obj.startswith('textures/'):
                 full_url = f"https://zrealtystoragedev.blob.core.windows.net/{data_obj}"
                 if full_url in download_map:
                     return download_map[full_url]
            
            # Case 4: The string is a JSON list of URLs
            if data_obj.strip().startswith('[') and data_obj.strip().endswith(']'):
                try:
                    parsed_list = json.loads(data_obj)
                    if isinstance(parsed_list, list):
                        # Replace each URL in the list
                        replaced_list = []
                        for url in parsed_list:
                            test_v = input_url_to_hp.get(url, url)
                            if isinstance(test_v, list):
                                for u in test_v:
                                    if u in download_map: replaced_list.append(download_map[u])
                            elif test_v in download_map:
                                replaced_list.append(download_map[test_v])
                            elif url.startswith('glb-assets/'):
                                # Handle relative GLB paths in lists
                                hp_res = input_url_to_hp.get(url)
                                if not hp_res:
                                    full_u = _convert_glb_path_to_url(url)
                                    hp_res = input_url_to_hp.get(full_u, full_u)
                                
                                if isinstance(hp_res, list):
                                    for u in hp_res:
                                        if u in download_map: replaced_list.append(download_map[u])
                                elif hp_res in download_map:
                                    replaced_list.append(download_map[hp_res])
                                else:
                                    replaced_list.append(url)
                            else:
                                replaced_list.append(url)
                        return json.dumps(replaced_list)
                except (json.JSONDecodeError, TypeError):
                    pass # Fallback to original string if parsing fails
            return data_obj # Return original string if no match
        elif isinstance(data_obj, list):
            return [replace_urls_in_plan(item) for item in data_obj]
        elif isinstance(data_obj, dict):
            # Process dictionary values
            new_dict = {}
            for k, v in data_obj.items():
                new_dict[k] = replace_urls_in_plan(v)
            
            # --- NEW: Inject Local Paths based on IDs ---
            
            # Explicitly handle isColorEdited to ensure it's a boolean (Fix for "asset colours not changing")
            if 'isColorEdited' in new_dict:
                val = new_dict['isColorEdited']
                if isinstance(val, str):
                    new_dict['isColorEdited'] = val.lower() == 'true'
            
            # Also check if it's inside 'asset_urls' commonly used in frontend
            if 'asset_urls' in new_dict and isinstance(new_dict['asset_urls'], dict):
                au = new_dict['asset_urls']
                if 'isColorEdited' in au:
                    val = au['isColorEdited']
                    if isinstance(val, str):
                        au['isColorEdited'] = val.lower() == 'true'
            
            # 1. Check direct keys
            a_id = new_dict.get('asset3D_Id') or new_dict.get('assetId_3D')
            
            # 2. Check nested inside 'asset_urls'
            if not a_id and 'asset_urls' in new_dict and isinstance(new_dict['asset_urls'], dict):
                a_id = new_dict['asset_urls'].get('asset3D_Id') or new_dict['asset_urls'].get('assetId_3D')

            if a_id and a_id in asset_id_to_url:
                url = asset_id_to_url[a_id]
                if url in download_map:
                     local_path = download_map[url]
                     
                     # Update Top-level keys
                     if 'glb_Url' in new_dict: new_dict['glb_Url'] = local_path
                     if 'GLB_File_URL' in new_dict: new_dict['GLB_File_URL'] = local_path
                     if 'glb_url' in new_dict: new_dict['glb_url'] = local_path
                     
                     # Update Nested 'asset_urls' keys
                     if 'asset_urls' in new_dict and isinstance(new_dict['asset_urls'], dict):
                         au = new_dict['asset_urls']
                         if 'glb_Url' in au: au['glb_Url'] = local_path
                         if 'GLB_File_URL' in au: au['GLB_File_URL'] = local_path
                         if 'glb_url' in au: au['glb_url'] = local_path

            # If this dict has a textureLibrary_Id, we can update/inject 'texture_urls'
            if 'textureLibrary_Id' in new_dict:
                t_id = new_dict['textureLibrary_Id']
                if t_id in texture_id_to_urls:
                    urls = texture_id_to_urls[t_id]
                    # Filter to those that were successfully downloaded
                    local_texture_paths = []
                    for u in urls:
                        if u in download_map:
                            local_texture_paths.append(download_map[u])
                    
                    if local_texture_paths:
                        # Inject as 'texture_urls' (or 'textures'?) - using 'texture_urls' as a safe bet for renderer
                        new_dict['texture_urls'] = local_texture_paths
            
            return new_dict
        return data_obj

    print(f"\n{COLOR_BLUE}--- Finalizing floor plan data with all local asset paths ---{COLOR_RESET}")
    
    if logger:
        logger.end_asset_download()
    
    localized_plan = replace_urls_in_plan(plan_data)
    
    # Post-processing: fix any broken local paths
    localized_plan = _fix_broken_local_paths(localized_plan)
    
    # If original input was a string, return string (for Godot input compatibility)
    if is_json_string:
        return json.dumps(localized_plan)
        
    return localized_plan


def _fix_broken_local_paths(data, _depth=0):
    """
    Recursively walks plan data and fixes local file paths that don't exist.
    For paths pointing to other project folders (e.g., 'r&d'), tries to:
    1. Find the same filename in local asset_downloads
    2. Re-download from API if assetId_3D is available
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_downloads = os.path.join(script_dir, "asset_downloads")
    
    if _depth > 20:  # Prevent infinite recursion
        return data
    
    if isinstance(data, str):
        # Check if it looks like a local file path that doesn't exist
        if (os.sep in data or '/' in data) and not data.startswith('http'):
            # It's a local path — check common file extensions
            ext = os.path.splitext(data)[1].lower()
            if ext in ('.glb', '.obj', '.mtl', '.jpg', '.jpeg', '.png', '.exr', '.hdr'):
                if not os.path.exists(data):
                    filename = os.path.basename(data)
                    local_candidate = os.path.join(local_downloads, filename)
                    if os.path.exists(local_candidate):
                        print(f"  🔄 Fixed broken path: {filename} (was: ...{data[-40:]})")
                        return local_candidate
                    else:
                        print(f"  ⚠️ Missing asset (not in cache): {filename}")
        return data
    
    elif isinstance(data, dict):
        fixed = {}
        for key, value in data.items():
            fixed[key] = _fix_broken_local_paths(value, _depth + 1)
        return fixed
    
    elif isinstance(data, list):
        return [_fix_broken_local_paths(item, _depth + 1) for item in data]
    
    return data

def _run_godot_render(job_id: str, payload: GenerationPayload, logger: Optional[RenderLogger] = None) -> str:
    """
    Runs the Godot rendering process.
    1. Localize assets.
    2. Save input JSON.
    3. Call Godot headless.
    4. Return path to output image.
    """
    if logger:
        # Reusing logs for consistency
        logger.start_glb_creation() 
    
    print(f"{COLOR_BLUE}--- Starting Godot Render ---{COLOR_RESET}")
    
    # 1. Localize Assets
    floor_plan_data = payload.floor_plan_data
    scene_data = json.loads(floor_plan_data) if isinstance(floor_plan_data, str) else floor_plan_data
    localized_geometry = _download_and_localize_assets(scene_data, use_high_res=True, logger=logger)
    
    # 2. Build Godot Input
    # Convert payload to dict to get camera, lights, etc.
    godot_input = payload.dict(exclude={'floor_plan_data', 'video_animation'}, exclude_unset=True)
    # Inject localized geometry
    godot_input['floor_plan_data'] = localized_geometry
    
    # 2.5 Scene Culling — remove rooms/items not visible from camera
    output_basename = f"{job_id}_render"
    culling_log_path = os.path.join(CULLING_LOGS_DIR, f"{output_basename}_culling.txt")
    try:
        optimizer = SceneOptimizer(log_path=culling_log_path)
        godot_input = optimizer.cull_scene(godot_input)
        print(f"✅ Scene culling complete. Log: {culling_log_path}")
    except Exception as e:
        print(f"⚠️ Scene culling failed (continuing without culling): {e}")
    
    # 3. Save Input JSON
    input_json_path = os.path.abspath(os.path.join(RUNTIME_DIR, f"{job_id}_godot_input.json"))
    with open(input_json_path, "w", encoding="utf-8") as f:
        json.dump(godot_input, f, indent=2)
        
    output_image_path = os.path.abspath(os.path.join(RENDER_OUTPUT_DIR, f"{job_id}_render.png"))
    
    # Call Godot
    godot_exe = GODOT_EXE
    project_path = GODOT_PROJECT_PATH
    
    # Arguments: --path <project> "res://main.tscn" -- <input> <output>
    # NOTE: Do NOT use --headless for renders; headless disables rendering in Godot 4.x.
    cmd = [godot_exe]
    cmd.extend([
        "--path", project_path,
        "res://main.tscn",
        "--",
        input_json_path,
        output_image_path
    ])
    
    print(f"Executing Godot: {' '.join(cmd)}")
    
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=1500)
        print(res.stdout)
        if res.stderr:
            print("Godot Stderr:", res.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Godot failed: {e}")
        print("STDOUT:", e.stdout)
        print("STDERR:", e.stderr)
        raise Exception(f"Godot execution failed: {e}")
    except subprocess.TimeoutExpired:
        raise Exception("Godot execution timed out")
        
    if not os.path.exists(output_image_path):
        raise Exception("Godot did not produce an output image.")
        
    print(f"{COLOR_GREEN}✅ Godot Render Success: {output_image_path}{COLOR_RESET}")
    
    if logger:
        logger.end_glb_creation() # Mark step done
        
    return output_image_path
def _find_ffmpeg_path():
    """
    Finds ffmpeg executable path on Windows system.
    Checks local installation first, then common installation locations and PATH.
    """
    # Check local installation first (in project directory)
    local_ffmpeg = os.path.join(os.path.dirname(__file__), "ffmpeg", "bin", "ffmpeg.exe")
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    
    # Common ffmpeg installation paths on Windows
    common_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        r"C:\tools\ffmpeg\bin\ffmpeg.exe",
        r"C:\Users\{}\AppData\Local\ffmpeg\bin\ffmpeg.exe".format(os.getenv('USERNAME', '')),
    ]
    
    # Check common paths
    for path in common_paths:
        if os.path.exists(path):
            print(f"✅ Found ffmpeg at: {path}")
            return path
    
    # Check if ffmpeg is in PATH
    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, timeout=5)
        if result.returncode == 0:
            print("✅ Found ffmpeg in system PATH")
            return 'ffmpeg'  # Return command name if found in PATH
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    # Try to find in Program Files
    program_files_paths = [
        r"C:\Program Files",
        r"C:\Program Files (x86)",
    ]
    
    for base_path in program_files_paths:
        if os.path.exists(base_path):
            try:
                for item in os.listdir(base_path):
                    ffmpeg_path = os.path.join(base_path, item, "bin", "ffmpeg.exe")
                    if os.path.exists(ffmpeg_path):
                        print(f"✅ Found ffmpeg at: {ffmpeg_path}")
                        return ffmpeg_path
            except (PermissionError, OSError):
                continue
    
    return None

def _delete_png_frames(output_dir: str, base_name: str):
    """
    Deletes PNG frame files after successful MP4 encoding.
    
    Args:
        output_dir: Directory containing PNG frames
        base_name: Base name of the PNG files (e.g., "render" for "render_0001.png")
    """
    try:
        import glob
        pattern = os.path.join(output_dir, f"{base_name}_*.png")
        png_files = sorted(glob.glob(pattern))
        
        if not png_files:
            return
        
        print(f"\n🧹 Cleaning up {len(png_files)} temporary PNG frames...")
        deleted_count = 0
        
        for png_file in png_files:
            try:
                os.remove(png_file)
                deleted_count += 1
            except Exception as e:
                print(f"⚠️  Warning: Could not delete {png_file}: {e}")
        
        if deleted_count > 0:
            print(f"✅ Deleted {deleted_count} PNG frame files")
        else:
            print(f"⚠️  No PNG files were deleted")
    except Exception as e:
        print(f"⚠️  Warning: Error during PNG cleanup: {e}")

def _encode_png_sequence_to_mp4(output_dir: str, base_name: str, output_mp4_path: str, fps: int = 30, delete_frames: bool = True) -> bool:
    """
    Encodes PNG image sequence to MP4 video using ffmpeg.
    
    Args:
        output_dir: Directory containing PNG frames
        base_name: Base name of the PNG files (e.g., "render" for "render_0001.png")
        output_mp4_path: Full path where MP4 should be saved
        fps: Frame rate for the video (default: 30)
        delete_frames: Whether to delete PNG frames after successful encoding (default: True)
    
    Returns:
        True if encoding succeeded, False otherwise
    """
    try:
        print(f"\n{'='*60}\n🎬 ENCODING PNG SEQUENCE TO MP4\n{'='*60}")
        print(f"Input directory: {output_dir}")
        print(f"Base name: {base_name}")
        print(f"Output MP4: {output_mp4_path}")
        print(f"Frame rate: {fps} fps")
        
        # Find all PNG files matching the pattern
        import glob
        pattern = os.path.join(output_dir, f"{base_name}_*.png")
        png_files = sorted(glob.glob(pattern))
        
        if not png_files:
            print(f"❌ ERROR: No PNG files found matching pattern: {pattern}")
            return False
        
        print(f"✅ Found {len(png_files)} PNG frames")
        
        # Find ffmpeg executable
        ffmpeg_path = _find_ffmpeg_path()
        if not ffmpeg_path:
            print("\n" + "="*80)
            print("❌ ERROR: ffmpeg not found. Please install ffmpeg to encode videos.")
            print("="*80)
            print("\n📥 QUICK INSTALLATION (Recommended):")
            print("   Run this command to automatically download and install ffmpeg:")
            print("   python install_ffmpeg.py")
            print("\n📥 MANUAL INSTALLATION OPTIONS:")
            print("\n1. Download from official website:")
            print("   https://www.gyan.dev/ffmpeg/builds/")
            print("   - Download 'ffmpeg-release-essentials.zip'")
            print("   - Extract to: " + os.path.join(os.path.dirname(__file__), "ffmpeg"))
            print("\n2. Install via Chocolatey (if installed):")
            print("   choco install ffmpeg")
            print("\n3. Install via winget (Windows 10/11):")
            print("   winget install ffmpeg")
            print("\n⚠️  After installation, restart this application.")
            print("="*80 + "\n")
            return False
        
        # Determine frame numbering pattern
        # Check if frames start from 0000 or 0001
        first_frame_0000 = os.path.exists(os.path.join(output_dir, f"{base_name}_0000.png"))
        first_frame_0001 = os.path.exists(os.path.join(output_dir, f"{base_name}_0001.png"))
        
        if first_frame_0000:
            # Frames start from 0000
            input_pattern = os.path.join(output_dir, f"{base_name}_%04d.png")
            start_number = 0
        elif first_frame_0001:
            # Frames start from 0001
            input_pattern = os.path.join(output_dir, f"{base_name}_%04d.png")
            start_number = 1
        else:
            # Try to find the first frame
            first_frame = None
            for i in range(10000):
                frame_path = os.path.join(output_dir, f"{base_name}_{i:04d}.png")
                if os.path.exists(frame_path):
                    first_frame = i
                    break
            
            if first_frame is None:
                print(f"❌ ERROR: Could not find first frame in sequence")
                return False
            
            input_pattern = os.path.join(output_dir, f"{base_name}_%04d.png")
            start_number = first_frame
            print(f"   Detected frame numbering starting from: {start_number:04d}")
            
        # VALIDATE FRAME SEQUENCE (Prevent missing frames/gaps)
        print(f"🔍 Validating frame sequence ({len(png_files)} frames detected)...")
        expected_count = len(png_files)
        # Check for gaps
        frames_found = []
        for f in png_files:
            try:
                # Extract number from filename (assuming ..._XXXX.png)
                num_part = f[-8:-4] 
                if num_part.isdigit():
                    frames_found.append(int(num_part))
            except:
                pass
        
        frames_found.sort()
        if len(frames_found) > 1:
            # Check for gaps
            gaps = []
            for i in range(len(frames_found) - 1):
                if frames_found[i+1] != frames_found[i] + 1:
                    gaps.append((frames_found[i], frames_found[i+1]))
            
            if gaps:
                print(f"⚠️  WARNING: Found {len(gaps)} gaps in frame sequence!")
                print(f"   First gap: between {gaps[0][0]} and {gaps[0][1]}")
                print(f"   FFmpeg might skip or shorten the video.")
                # Optional: We could fill gaps here, but for now just warn
            else:
                print(f"✅ Frame sequence is contiguous (Indices {frames_found[0]} to {frames_found[-1]})")
        
        # Build ffmpeg command - Two step process:
        # Step 1: Create video from PNG frames (no rotation) → temporary file
        # Step 2: Rotate the video by 180 degrees → final output
        temp_video_path = os.path.join(output_dir, f"{base_name}_temp.mp4")
        
        # Step 1: Create video from PNG frames (no rotation)
        ffmpeg_cmd_step1 = [
            ffmpeg_path,
            '-y',  # Overwrite output file
            '-start_number', str(start_number),
            '-framerate', str(fps), # Input framerate
            '-i', input_pattern,
            '-r', str(fps), # Output framerate (CRITICAL: Locks output timing)
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-crf', '18',  # High quality
            '-preset', 'medium',
            temp_video_path
        ]
        
        print(f"📹 Step 1: Creating video from PNG frames...")
        print(f"   Command: {' '.join(ffmpeg_cmd_step1)}")
        
        # Run step 1: Create video
        result_step1 = subprocess.run(
            ffmpeg_cmd_step1,
            capture_output=True,
            text=True,
            timeout=1500  # 10 minute timeout for encoding
        )
        
        if result_step1.returncode != 0:
            print(f"❌ ERROR: Step 1 (video creation) failed")
            print(f"   Return code: {result_step1.returncode}")
            if result_step1.stderr:
                print(f"   Error output: {result_step1.stderr[:500]}")
            return False
        
        if not os.path.exists(temp_video_path) or os.path.getsize(temp_video_path) == 0:
            print(f"❌ ERROR: Temporary video file was not created or is empty")
            return False
        
        # Step 2: Add audio to video (rotation commented out)
        # Rotation was: transpose=2,transpose=2 = +180° (clockwise) | transpose=1,transpose=1 = -180° (counter-clockwise)
        
        # Find audio file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        audio_path = os.path.join(script_dir, "audio", "audio.mp3")
        has_audio = os.path.exists(audio_path)
        
        if has_audio:
            print(f"🎵 Found audio file: {audio_path}")
            # Step 2: Add audio (rotation commented out)
            ffmpeg_cmd_step2 = [
                ffmpeg_path,
                '-y',  # Overwrite output file
                '-i', temp_video_path,  # Video input
                '-i', audio_path,  # Audio input
                # '-vf', 'transpose=2,transpose=2',  # Rotate 180 degrees clockwise - COMMENTED OUT
                '-c:v', 'libx264',
                '-c:a', 'aac',  # Audio codec
                '-b:a', '192k',  # Audio bitrate
                '-shortest',  # Finish encoding when the shortest input stream ends
                '-pix_fmt', 'yuv420p',
                '-crf', '18',  # High quality
                '-preset', 'medium',
                output_mp4_path
            ]
            print(f"🎵 Step 2: Adding audio to video...")
        else:
            print(f"⚠️  Audio file not found: {audio_path} (continuing without audio)")
            # Step 2: Copy video only (no rotation, no audio)
            ffmpeg_cmd_step2 = [
                ffmpeg_path,
                '-y',  # Overwrite output file
                '-i', temp_video_path,
                # '-vf', 'transpose=2,transpose=2',  # Rotate 180 degrees clockwise - COMMENTED OUT
                '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p',
                '-crf', '18',  # High quality
                '-preset', 'medium',
                output_mp4_path
            ]
            print(f"📹 Step 2: Copying video to final output...")
        
        print(f"   Command: {' '.join(ffmpeg_cmd_step2)}")
        
        # Run step 2: Process video (add audio and/or rotate)
        result_step2 = subprocess.run(
            ffmpeg_cmd_step2,
            capture_output=True,
            text=True,
            timeout=1500  # 10 minute timeout for encoding
        )
        
        # Clean up temporary file
        if os.path.exists(temp_video_path):
            try:
                os.remove(temp_video_path)
                print(f"🧹 Cleaned up temporary video file")
            except Exception as e:
                print(f"⚠️  Warning: Could not delete temporary file: {e}")
        
        if result_step2.returncode != 0:
            print(f"❌ ERROR: Step 2 (video processing) failed")
            print(f"   Return code: {result_step2.returncode}")
            if result_step2.stderr:
                print(f"   Error output: {result_step2.stderr[:500]}")
            return False
        
        if os.path.exists(output_mp4_path) and os.path.getsize(output_mp4_path) > 0:
            file_size = os.path.getsize(output_mp4_path)
            audio_status = "with audio" if has_audio else "without audio"
            print(f"✅ SUCCESS: MP4 video encoded and {audio_status} successfully!")
            print(f"   Output: {output_mp4_path}")
            print(f"   Size: {file_size:,} bytes ({file_size / (1024*1024):.2f} MB)")
            if has_audio:
                print(f"   🎵 Audio: {audio_path}")
            
            # Delete PNG frames after successful encoding (if requested)
            if delete_frames:
                _delete_png_frames(output_dir, base_name)
            
            return True
        else:
            print(f"❌ ERROR: Final MP4 file was not created or is empty")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"❌ ERROR: ffmpeg encoding timed out after 10 minutes")
        return False
    except Exception as e:
        print(f"❌ ERROR: Failed to encode PNG sequence to MP4: {e}")
        import traceback
        traceback.print_exc()
        return False



def _get_thumbnail_path(file_path: str) -> str:
    """Return the expected thumbnail path next to a rendered image or video."""
    base_dir = os.path.dirname(os.path.abspath(file_path))
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    return os.path.join(base_dir, f"{base_name}_thumb.webp")


def _upload_to_blob_storage(file_path: str, blob_container: str = "render-images") -> str:
    """
    Uploads the rendered file (image or video) to Azure Blob Storage and returns the blob URL.
    
    Args:
        file_path: Local path to the file (image or video)
        blob_container: Name of the blob container (default: "render-images")
    
    Returns:
        Full URL of the uploaded blob, or None if upload failed
    """
    try:
        print(f"\n{'='*60}\n☁️  UPLOADING TO BLOB STORAGE\n{'='*60}")
        print(f"File Path: {file_path}")
        print(f"Container: {blob_container}")
        
        # Get the filename
        filename = os.path.basename(file_path)
        
        # Determine content type based on file extension
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext == '.mp4':
            content_type = 'video/mp4'
            file_type = "Video"
        elif file_ext in ['.png', '.jpg', '.jpeg']:
            content_type = 'image/png' if file_ext == '.png' else 'image/jpeg'
            file_type = "Image"
        elif file_ext == '.webp':
            content_type = 'image/webp'
            file_type = "Thumbnail"
        else:
            content_type = 'application/octet-stream'
            file_type = "File"
        
        # Construct blob URL
        blob_base_url = "https://zrealtystoragedev.blob.core.windows.net"
        blob_path = f"{blob_container}/{filename}"
        blob_url = f"{blob_base_url}/{blob_path}"
        
        # Construct upload URL with SAS token
        upload_url = f"{blob_url}?{SAS_TOKEN}"
        
        print(f"Blob URL: {blob_url}")
        print(f"Uploading {file_type}: {filename}")
        
        # Read the file
        with open(file_path, 'rb') as file:
            file_content = file.read()
            file_size = len(file_content)
            print(f"File size: {file_size:,} bytes ({file_size / (1024*1024):.2f} MB)")
        
        # Upload to blob storage using PUT request
        headers = {
            'x-ms-blob-type': 'BlockBlob',
            'Content-Type': content_type
        }
        
        print(f"Uploading to: {blob_url}")
        response = requests.put(upload_url, data=file_content, headers=headers, timeout=1500)  # Longer timeout for videos
        
        if response.status_code in [200, 201]:
            print(f"{COLOR_GREEN}✅ SUCCESS: {file_type} uploaded to blob storage!{COLOR_RESET}")
            print(f"   Blob URL: {blob_url}")
            return blob_url
        else:
            print(f"{COLOR_RED}❌ FAILED: Blob upload returned status {response.status_code}{COLOR_RESET}")
            print(f"   Response: {response.text}")
            return None
            
    except Exception as e:
        print(f"{COLOR_RED}❌ Error uploading to blob storage: {e}{COLOR_RESET}") 
        import traceback
        traceback.print_exc()
        return None

def _send_status_update(
    project_id: int, 
    gallery_id: int, 
    user_id: int, 
    job_id: str,
    status: str,
    step: str,
    progress: int,
    message: str,
    render_type: str = "IMAGE",
    file_path: Optional[str] = None,
    blob_url: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    logger: Optional[RenderLogger] = None,
    api_endpoint: Optional[str] = None,
    wait_for_delivery: bool = False
) -> bool:
    """
    Sends status update to UploadGallerAI endpoint in a NON-BLOCKING background thread.
    
    Status Codes (NEW):
    - 1 = InProgress (for all steps before the last step)
    - 2 = Completed (for the last/final step)
    - 3 = Rejected (for exceptional handling/validation failures)
    - 4 = Error (for errors/exceptions)
    
    If wait_for_delivery is True, the status payload is sent synchronously so
    the engine cannot reorder the AI job's in-progress and completed updates.

    Returns:
        True immediately (queued successfully) unless wait_for_delivery is used.
    """
    
    # 1. Update active_jobs dictionary details IMMEDIATELY (synchronous)
    # This ensures the local monitoring dashboard is instant
    with queue_lock:
        if job_id in active_jobs:
            active_jobs[job_id]['status'] = status
            active_jobs[job_id]['progress'] = progress
            active_jobs[job_id]['current_step'] = step
            active_jobs[job_id]['message'] = message
            
    # 2. UPDATE GLOBAL STATUS CACHE
    _update_global_status(
        job_id=job_id,
        status=status,
        step=step,
        progress=progress,
        message=message,
        project_id=project_id,
        gallery_id=gallery_id,
        job_type=render_type.lower()
    )
    


    # 3. Define the network work to be done in background
    def _network_worker():
        try:
            # Map string status to numeric status code
            status_code_map = {
                "processing": 1, "in_progress": 1, "queued": 1,
                "completed": 2, "success": 2,
                "rejected": 3,
                "failed": 4, "error": 4
            }
            status_code = status_code_map.get(status.lower(), 1)
            
            status_names = {1: "InProgress", 2: "Completed", 3: "Rejected", 4: "Error"}
            status_name = status_names.get(status_code, f"Unknown({status_code})")
            
            # Prepare payload
            status_payload_form = {
                "UserId": str(user_id),
                "ProjectId": str(project_id),
                "Id": str(gallery_id),
                "RenderType": render_type.upper(),
                "StatusMessage": message,
                "Status": status_code,
                "JobId": job_id,
                "StatusText": status,
                "Step": step,
                "Progress": str(progress),
                "Timestamp": datetime.now().isoformat()
            }
            
            payload_details = dict(details or {})
            if blob_url:
                url_field_name = "VideoUrl" if render_type == "VIDEO" else "ImageUrl"
                status_payload_form[url_field_name] = blob_url
                payload_details.setdefault("image_url", blob_url)
                payload_details.setdefault("output_url", blob_url)
                payload_details.setdefault("blob_url", blob_url)
            if thumbnail_url:
                status_payload_form["ThumbnailUrl"] = thumbnail_url
                payload_details.setdefault("thumbnail_url", thumbnail_url)

            if payload_details:
                status_payload_form["Details"] = json.dumps(payload_details)
                
            # Determine endpoint
            upload_url = api_endpoint if api_endpoint else (VIDEO_UPLOAD_API_URL if render_type == "VIDEO" else UPLOAD_API_URL)
            
            print(f"\n{COLOR_BLUE}📡 [Background] Sending Status: {status_name} ({progress}%) - {message}{COLOR_RESET}")
            # DEBUG: Print payload to verify ImageUrl/VideoUrl/ThumbnailUrl
            print(f"   Payload: {json.dumps(status_payload_form, default=str)}")
            
            request_start_time = time.monotonic()

            # Add API Key headers if enabled
            headers = {}
            if USE_API_KEY and API_KEY:
                headers["ZRealtyServiceApiKey"] = API_KEY
                # Also try adding it as a query param just in case, or stick to header?
                # Sticking to header simply.

            # Always notify the local monitor first so the queue/UI can complete
            # even if the public callback endpoint is slow or unavailable.
            # If the monitor accepts the update, it becomes the single sender to
            # the public API. That avoids duplicate or out-of-order status writes
            # to UploadProjectGalleryAI / UploadProjectVideoAI.
            files = {'UploadImage': (None, '')}
            monitor_sent = False
            if MONITORING_API_URL:
                try:
                    print(f"   📡 Broadcasting to Monitor: {MONITORING_API_URL}")
                    monitor_response = None
                    for attempt in range(1, 4):
                        try:
                            monitor_response = requests.post(
                                MONITORING_API_URL,
                                data=status_payload_form,
                                files=files,
                                headers=headers,
                                verify=False,
                                timeout=10,
                            )
                            print(f"   📡 Monitor broadcast response: {monitor_response.status_code} (attempt {attempt}/3)")
                            if monitor_response.status_code in [200, 201, 202]:
                                monitor_sent = True
                                break
                        except Exception as monitor_exc:
                            print(f"   ⚠️ Monitor broadcast attempt {attempt}/3 failed: {monitor_exc}")
                            time.sleep(1.5 * attempt)
                except Exception as mon_e:
                    print(f"   ⚠️ Monitoring broadcast failed: {mon_e}")

            if monitor_sent:
                request_duration = time.monotonic() - request_start_time
                if logger:
                    logger.add_status_update(step, f"{status_name}({status_code})", request_duration, 200)
                print(f"{COLOR_GREEN}   ✓ Status accepted by monitor; skipping direct callback to avoid duplicate writes ({request_duration:.2f}s){COLOR_RESET}")
                return

            # Send request to Primary API
            response = None
            primary_error = None
            try:
                response = requests.post(upload_url, data=status_payload_form, files=files, headers=headers, verify=False, timeout=60)
            except Exception as upload_exc:
                primary_error = upload_exc

            request_duration = time.monotonic() - request_start_time

            if logger and response is not None:
                logger.add_status_update(step, f"{status_name}({status_code})", request_duration, response.status_code)

            if response is not None and response.status_code in [200, 201, 202]:
                print(f"{COLOR_GREEN}   ✓ Status sent successfully ({request_duration:.2f}s){COLOR_RESET}")
            elif response is not None:
                # Try to parse JSON error message
                error_detail = response.text[:200]
                try:
                    error_json = response.json()
                    if "message" in error_json:
                        error_detail = error_json["message"]
                    elif "Message" in error_json:
                        error_detail = error_json["Message"]
                except:
                    pass
                print(f"{COLOR_YELLOW}   ⚠️ Status API Error ({response.status_code}): {error_detail} ({request_duration:.2f}s){COLOR_RESET}")
            elif primary_error is not None:
                print(f"{COLOR_YELLOW}   ⚠️ Status API Request Failed: {primary_error} ({request_duration:.2f}s){COLOR_RESET}")
                
        except Exception as e:
            print(f"{COLOR_YELLOW}   ⚠️ Background status update failed: {e}{COLOR_RESET}")

    if wait_for_delivery:
        _network_worker()
        return True

    # 4. Start background thread
    try:
        t = Thread(target=_network_worker, daemon=True)
        t.start()
        return True
    except Exception as e:
        print(f"Error starting status thread: {e}")
        return False

def _upload_render_to_api(project_id: int, gallery_id: int, file_path: str, user_id: int = 0, logger: Optional[RenderLogger] = None, job_id: str = ""):
    """
    Step 6: Finalizes the render job by uploading the binary and sending the success message.
    """
    try:
        render_type = "VIDEO" if file_path.lower().endswith('.mp4') else "IMAGE"
        print(f"\n{'='*60}\n📤 STEP 6: FINAL UPLOAD TO API ({render_type})\n{'='*60}")
        
        # Step 1: Upload to blob storage for permanent record
        file_ext = os.path.splitext(file_path)[1].lower()
        blob_container = "render-videos" if file_ext == '.mp4' else "render-images"
        blob_url = _upload_to_blob_storage(file_path, blob_container=blob_container)
        thumbnail_url = None
        thumbnail_path = _get_thumbnail_path(file_path)
        if os.path.exists(thumbnail_path):
            print(f"{COLOR_BLUE}🖼️ Uploading thumbnail: {thumbnail_path}{COLOR_RESET}")
            thumbnail_url = _upload_to_blob_storage(thumbnail_path, blob_container=blob_container)
            if not thumbnail_url:
                print(f"{COLOR_YELLOW}⚠️ Thumbnail upload failed, continuing with the main render only.{COLOR_RESET}")
        else:
            print(f"{COLOR_YELLOW}⚠️ Thumbnail not found at: {thumbnail_path}{COLOR_RESET}")
        
        # Step 2: Send blob URL + status to main API (NO binary file)
        if not blob_url:
            print(f"{COLOR_RED}❌ Upload to blob storage failed. Cannot complete job.{COLOR_RESET}")
            _send_status_update(
                project_id=project_id,
                gallery_id=gallery_id,
                user_id=user_id,
                job_id=job_id,
                status="error",
                step="upload_binary",
                progress=0,
                message="Something went wrong. Please try again.",
                render_type=render_type,
                logger=logger,
                wait_for_delivery=True
            )
            return False

        # We use _send_status_update which sends the main blob URL and thumbnail URL
        
        # --- NEW: Read Asset Report to include log details in final JSON ---
        final_details = {}
        try:
            asset_report_path = os.path.join(GLB_OUTPUT_DIR, f"{job_id}_asset_report.json")
            if os.path.exists(asset_report_path):
                with open(asset_report_path, 'r', encoding='utf-8') as f:
                    asset_report = json.load(f)
                final_details['asset_processing_report'] = asset_report
                print(f"📄 Included asset report with {len(asset_report)} items.")
        except Exception as e:
            print(f"⚠️ Could not include asset report: {e}")

        if thumbnail_url:
            final_details["thumbnail_url"] = thumbnail_url

        success = _send_status_update(
            project_id=project_id,
            gallery_id=gallery_id,
            user_id=user_id,
            job_id=job_id,
            status="completed",
            step="completed",
            progress=100,
            message="Your render is ready!" if render_type == "IMAGE" else "Your video is ready!",
            render_type=render_type,
            blob_url=blob_url,  # Send blob URL in ImageUrl field
            thumbnail_url=thumbnail_url,
            logger=logger,
            details=final_details,
            wait_for_delivery=True
        )
        
        

                
        if success:
            print(f"{COLOR_GREEN}✅ Final upload and status update successful!{COLOR_RESET}")
            if blob_url:
                print(f"   Permanent Blob Storage URL: {blob_url}")
            return True
        else:
            print(f"{COLOR_YELLOW}⚠️  Status update failed but blob upload succeeded{COLOR_RESET}")
            if blob_url:
                print(f"   Permanent Blob Storage URL: {blob_url}")
            return False
            
    except Exception as e:
        print(f"{COLOR_RED}❌ ERROR in final upload: {str(e)}{COLOR_RESET}")
        import traceback
        traceback.print_exc()
        return False

# --- BACKGROUND RENDER TASK ---

def _background_render_task(project_id: int, gallery_id: int, payload: GenerationPayload, job_id: str, user_id: int = 0, suppress_error_status: bool = False):
    """The main function that runs in the background to perform the full render and upload pipeline."""
    logger = RenderLogger(job_id)
    logger.start_process()
    logger.set_render_type("IMAGE")
    logger.set_user_details(project_id, gallery_id, user_id)
    logger.set_render_quality(payload.render_quality) 
    
    output_image_path = None
    enable_status_updates = getattr(payload, 'enable_status_updates', False)
    
    try:
        start_time = time.monotonic()
        print(f"\n{'='*80}\n🚀 BACKGROUND RENDER TASK STARTED (GODOT)\n{'='*80}\nJob ID: {job_id}\nProject ID: {project_id}\nGallery ID: {gallery_id}\nUser ID: {user_id}\nStatus Updates: {'ENABLED' if enable_status_updates else 'DISABLED'}\nTimestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*80}\n")
        
        # Step 1/5: Preparing your scene
        if enable_status_updates:
            _send_status_update(project_id, gallery_id, user_id, job_id, "processing", "preparing", 10, "Preparing your scene...", render_type="IMAGE", logger=logger)
        
        print(f"🔍 STEP 1/5: Validating input payload...\n{'─'*60}")
        is_valid, validation_error = _validate_image_payload(payload)
        if not is_valid:
            error_msg = f"Invalid payload: {validation_error}"
            print(f"{COLOR_RED}❌ VALIDATION FAILED: {error_msg}{COLOR_RESET}")
            if enable_status_updates:
                _send_status_update(
                    project_id, gallery_id, user_id, job_id, 
                    "rejected", "validation", 0, 
                    "Something went wrong. Please try again.", 
                    render_type="IMAGE", logger=logger
                )
            logger.add_interrupt("validation_failed", error_msg)
            logger.end_process(success=False, error=error_msg)
            return
        print(f"✅ Payload validation passed\n")
        
        # Step 2/5: Setting up your space
        if enable_status_updates:
            _send_status_update(project_id, gallery_id, user_id, job_id, "processing", "loading", 30, "Setting up your space...", render_type="IMAGE", logger=logger)
        
        # Step 3/5: Rendering your design
        if enable_status_updates:
            _send_status_update(project_id, gallery_id, user_id, job_id, "processing", "rendering", 50, "Rendering your design...", render_type="IMAGE", logger=logger)
            
        print(f"🎬 STEP 3/5: Running Render...\n{'─'*60}")
        
        output_image_path = _run_godot_render(job_id, payload, logger=logger)
        
        if not output_image_path or not os.path.exists(output_image_path):
            raise Exception("Godot render failed to produce output")
            
        print(f"✅ Render completed successfully: {output_image_path}\n")

        # Step 4/5: Adding final touches
        if enable_status_updates:
             _send_status_update(project_id, gallery_id, user_id, job_id, "processing", "finalizing", 80, "Adding final touches...", render_type="IMAGE", logger=logger, wait_for_delivery=True)
        
        print(f"📤 STEP 4/5: Uploading to external API...\n{'─'*60}")
             
        upload_success = _upload_render_to_api(project_id, gallery_id, output_image_path, user_id, logger=logger, job_id=job_id)
        
        end_time = time.monotonic()
        if upload_success:
            print(f"\n{'='*80}\n🎉 ALL STEPS COMPLETED SUCCESSFULLY! (Total Time: {(end_time - start_time):.2f}s)\n{'='*80}")
            logger.end_process(success=True)
        else:
            print(f"\n{'='*80}\n⚠️ RENDER COMPLETED BUT UPLOAD FAILED (Total Time: {(end_time - start_time):.2f}s)\n{'='*80}")
            logger.end_process(success=True, error="Upload failed")
            
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(f"\n{'='*80}\n{COLOR_RED}❌ ERROR IN BACKGROUND TASK{COLOR_RESET}\n{'='*80}\nJob ID: {job_id}\nError: {error_msg}\n{'='*80}\n")
        import traceback
        traceback.print_exc()
        
        if suppress_error_status:
            # Re-raise so the retry loop can catch it and retry
            logger.add_interrupt("error", error_msg)
            logger.end_process(success=False, error=error_msg)
            raise
        
        if enable_status_updates:
            _send_status_update(project_id, gallery_id, user_id, job_id, "error", "error", 0, "Scene is too large to render. Please try a different setup.", render_type="IMAGE", logger=logger)
        logger.add_interrupt("error", error_msg)
        logger.end_process(success=False, error=error_msg)
        raise  # Always propagate so retry loop detects the failure
    finally:
        pass

def _run_godot_video_render(job_id: str, payload: VideoGenerationPayload, logger: Optional[RenderLogger] = None) -> str:
    """
    Runs the Godot video rendering process using MovieWriter (--write-movie) for fast rendering.
    Falls back to PNG frame-by-frame if MovieWriter is not available.
    """
    # 1. Localize assets (same pattern as image render)
    if logger: logger.start_glb_creation()
    
    print(f"🎬 Starting Godot Video Render for Job {job_id}")
    
    localized_geometry = None
    if payload.floor_plan_data:
        # Parse floor_plan_data string → dict if needed
        floor_plan_data = payload.floor_plan_data
        scene_data = json.loads(floor_plan_data) if isinstance(floor_plan_data, str) else floor_plan_data
        # Download and localize all asset paths to absolute local paths
        localized_geometry = _download_and_localize_assets(scene_data, use_high_res=True, logger=logger)
        print(f"--- Finalizing floor plan data with all local asset paths ---")
    
    if logger: logger.end_glb_creation()

    # 2. Build Godot input JSON with localized paths
    # Exclude floor_plan_data since we replace it with localized version
    godot_input = payload.dict(exclude={'floor_plan_data'}, exclude_unset=False)
    if localized_geometry:
        godot_input['floor_plan_data'] = localized_geometry
    elif payload.floor_plan_data:
        # If localization failed, try to use raw data
        if isinstance(payload.floor_plan_data, str):
            try:
                godot_input['floor_plan_data'] = json.loads(payload.floor_plan_data)
            except:
                godot_input['floor_plan_data'] = payload.floor_plan_data
        else:
            godot_input['floor_plan_data'] = payload.floor_plan_data

    # Remove None values to prevent GDScript null issues (has() returns true but value is null)
    godot_input = {k: v for k, v in godot_input.items() if v is not None}
    
    # 2.5 Video Scene Culling — remove rooms/items not visible along camera path
    output_basename = f"{job_id}_render"
    culling_log_path = os.path.join(CULLING_LOGS_DIR, f"{output_basename}_culling.txt")
    if ENABLE_VIDEO_CULLING:
        try:
            video_optimizer = VideoSceneOptimizer(log_path=culling_log_path)
            godot_input = video_optimizer.cull_scene(godot_input)
            print(f"✅ Video scene culling complete. Log: {culling_log_path}")
        except Exception as e:
            print(f"⚠️ Video scene culling failed (continuing without culling): {e}")
    else:
        print("ℹ️  Video scene culling disabled by main.py flag; rendering full scene.")

    # Save the properly assembled input JSON
    input_json_path = os.path.join(RUNTIME_DIR, f"{job_id}_video_input.json")
    try:
        with open(input_json_path, 'w', encoding='utf-8') as f:
            json.dump(godot_input, f, ensure_ascii=False, default=str)
        print(f"📄 Saved video input JSON to: {input_json_path}")
    except Exception as e:
        print(f"❌ Failed to save video input JSON: {e}")
        raise e

    # 3. Prepare Output Directory
    output_dir = os.path.abspath(os.path.join(RENDER_OUTPUT_DIR, job_id))
    os.makedirs(output_dir, exist_ok=True)
    
    # Output paths
    # MovieWriter saves AVI relative to the Godot project directory
    # Use global paths configured at start
    godot_exe = GODOT_EXE
    project_path = GODOT_PROJECT_PATH
    
    avi_filename = f"{job_id}_render.avi"
    avi_godot_output = os.path.join(project_path, avi_filename)  # Where Godot will save it
    output_video_path = os.path.abspath(os.path.join(RENDER_OUTPUT_DIR, f"{job_id}_render.mp4"))
    temp_img_base = os.path.join(output_dir, "frame.png")  # PNG fallback
    
    fps = payload.video_animation.fps if payload.video_animation else 30
    
    # 4. Run Godot with MovieWriter
    # NOTE: Do NOT use --headless for renders; headless disables rendering in Godot 4.x.
    cmd = [godot_exe]
    cmd.extend([
        "--path", project_path,
        "--write-movie", avi_filename,  # Simple filename - Godot saves in project dir
        "--fixed-fps", str(fps),
        "res://video_main.tscn",
        "--",
        input_json_path,
        output_video_path,
        temp_img_base  # passed so GDScript knows where to save PNGs if MovieWriter fails
    ])
    
    print(f"🚀 Running Godot MovieWriter Command: {' '.join(cmd)}")
    
    timeout = int(os.getenv("VIDEO_RENDER_TIMEOUT_SECONDS", "1800"))
    
    try:
        # NOTE: check=False because Godot MovieWriter always exits with code 1 even on success
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
        if res.stdout:
            print(f"📋 Godot Output:\n{res.stdout[-2000:]}")
        if res.stderr:
            print(f"⚠️  Godot Stderr:\n{res.stderr[-1000:]}")
        
        # Check if MovieWriter actually produced the AVI file
        if not os.path.exists(avi_godot_output) or os.path.getsize(avi_godot_output) == 0:
            # Only fail if no AVI was produced AND no meaningful output
            if "VIDEO RENDER COMPLETE" not in (res.stdout or ""):
                print(f"❌ Godot failed to produce video (exit code {res.returncode})")
                raise Exception(f"Godot render failed with exit code {res.returncode}")
    except subprocess.TimeoutExpired:
        print(f"❌ Godot execution timed out after {timeout}s")
        raise Exception("Godot render timed out")

    # 5. Check what was produced and convert to MP4
    if os.path.exists(avi_godot_output) and os.path.getsize(avi_godot_output) > 0:
        # MovieWriter produced an AVI — convert to MP4
        avi_size_mb = os.path.getsize(avi_godot_output) / (1024 * 1024)
        print(f"✅ MovieWriter AVI created: {avi_godot_output} ({avi_size_mb:.1f} MB)")
        print(f"🎞️ Converting AVI to MP4...")
        
        ffmpeg_path = _find_ffmpeg_path()
        if not ffmpeg_path:
            print("❌ FFmpeg not found! Cannot convert AVI to MP4.")
            raise Exception("FFmpeg not found")
        
        # Find audio file
        script_dir = os.path.dirname(os.path.abspath(__file__))
        audio_path = os.path.join(script_dir, "audio", "audio.mp3")
        has_audio = os.path.exists(audio_path)
        
        # Build FFmpeg command: AVI → MP4
        # MovieWriter AVI includes a silent audio track — we must explicitly ignore it
        ffmpeg_cmd = [
            ffmpeg_path,
            '-y',
            '-i', avi_godot_output,
        ]
        
        if has_audio:
            ffmpeg_cmd.extend(['-i', audio_path])
            ffmpeg_cmd.extend([
                '-map', '0:v',     # Video from AVI (input 0)
                '-map', '1:a',     # Audio from external MP3 (input 1)
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-shortest',
            ])
            print(f"🎵 Adding audio: {audio_path}")
        else:
            ffmpeg_cmd.extend([
                '-map', '0:v',     # Video only, no audio
                '-c:v', 'libx264',
                '-an',             # Explicitly no audio
            ])
        
        ffmpeg_cmd.extend([
            '-pix_fmt', 'yuv420p',
            '-crf', '18',
            '-preset', 'medium',
            output_video_path
        ])
        
        print(f"📹 FFmpeg command: {' '.join(ffmpeg_cmd)}")
        
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode != 0:
            print(f"❌ FFmpeg conversion failed: {result.stderr[:500] if result.stderr else 'No error output'}")
            raise Exception("FFmpeg AVI to MP4 conversion failed")
        
        # Clean up AVI
        try:
            os.remove(avi_godot_output)
            print(f"🧹 Cleaned up AVI file")
        except:
            pass
        
        if os.path.exists(output_video_path) and os.path.getsize(output_video_path) > 0:
            mp4_size_mb = os.path.getsize(output_video_path) / (1024 * 1024)
            print(f"✅ Video output saved: {output_video_path} ({mp4_size_mb:.1f} MB)")
            # Clean up the temp frames folder (no longer needed after MP4 is saved)
            try:
                import shutil
                shutil.rmtree(output_dir, ignore_errors=True)
                print(f"🧹 Cleaned up temp folder: {output_dir}")
            except Exception:
                pass
            return output_video_path
        else:
            raise Exception("MP4 output file was not created")
    else:
        # Fallback: check for PNG sequence (MovieWriter might not have been available)
        print("⚠️  MovieWriter AVI not found — checking for PNG fallback frames...")
        
        print(f"🎞️ Encoding PNG sequence to MP4 (FPS: {fps})...")
        success = _encode_png_sequence_to_mp4(output_dir, "frame", output_video_path, fps=fps, delete_frames=True)
        
        if success:
            print(f"✅ Video output saved: {output_video_path}")
            # Clean up the temp frames folder (PNG frames already deleted by encoder,
            # but the folder itself still remains — remove it)
            try:
                import shutil
                shutil.rmtree(output_dir, ignore_errors=True)
                print(f"🧹 Cleaned up temp folder: {output_dir}")
            except Exception:
                pass
            return output_video_path
        else:
            raise Exception("Video encoding failed — no AVI or PNG frames produced")

def _background_video_render_task(project_id: int, gallery_id: int, payload: VideoGenerationPayload, job_id: str, user_id: int = 0, suppress_error_status: bool = False):
    """The main function that runs in the background to perform the full video render and upload pipeline."""
    logger = RenderLogger(job_id)
    logger.start_process()
    logger.set_render_type("VIDEO")
    logger.set_user_details(project_id, gallery_id, user_id)
    logger.set_render_quality(payload.render_quality)
    
    output_video_path = None
    enable_status_updates = getattr(payload, 'enable_status_updates', False)
    
    try:
        start_time = time.monotonic()
        video_animation = payload.video_animation
        duration_seconds = video_animation.duration_seconds if video_animation else 5.0
        fps = video_animation.fps if video_animation else 30
        print(f"\n{'='*80}\n🎬 BACKGROUND VIDEO RENDER TASK STARTED (GODOT)\n{'='*80}\nJob ID: {job_id}\nProject ID: {project_id}\nGallery ID: {gallery_id}\nUser ID: {user_id}\nDuration: {duration_seconds}s @ {fps}fps\nStatus Updates: {'ENABLED' if enable_status_updates else 'DISABLED'}\nTimestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*80}\n")
        
        # Step 1/5: Preparing your scene
        if enable_status_updates:
            _send_status_update(project_id, gallery_id, user_id, job_id, "processing", "preparing", 10, "Preparing your scene...", render_type="VIDEO", logger=logger)
        
        print(f"🔍 STEP 1/5: Validating input payload...\n{'─'*60}")
        is_valid, validation_error = _validate_video_payload(payload)
        if not is_valid:
            error_msg = f"Invalid payload: {validation_error}"
            print(f"{COLOR_RED}❌ VALIDATION FAILED: {error_msg}{COLOR_RESET}")
            if enable_status_updates:
                _send_status_update(
                    project_id, gallery_id, user_id, job_id, 
                    "rejected", "validation", 0, 
                    "Something went wrong. Please try again.", 
                    render_type="VIDEO", logger=logger
                )
            logger.add_interrupt("validation_failed", error_msg)
            logger.end_process(success=False, error=error_msg)
            return
        print(f"✅ Payload validation passed\n")
        
        # Step 2/5: Setting up your space
        if enable_status_updates:
            _send_status_update(project_id, gallery_id, user_id, job_id, "processing", "loading", 30, "Setting up your space...", render_type="VIDEO", logger=logger)
        
        # Step 3/5: Creating your video
        if enable_status_updates:
            _send_status_update(project_id, gallery_id, user_id, job_id, "processing", "rendering", 50, "Creating your video...", render_type="VIDEO", logger=logger)
            
        print(f"🎬 STEP 3/5: Running Video Render...\n{'─'*60}")
        
        # Optimization (Optional, kept from original logic if needed, but handled by Godot's throughput mostly)
        # Note: Godot can handle unoptimized scenes better in some cases, but culling is always good.
        # We reused the optimizer in previous code. Let's skip it for simplicity unless requested, as it was complex.
        # If needed, we can re-add `VideoSceneOptimizer` usage here.
        
        output_video_path = _run_godot_video_render(job_id, payload, logger=logger)
        
        if not output_video_path or not os.path.exists(output_video_path):
            raise Exception("Godot video render failed to produce output")
            
        print(f"✅ Video Render completed successfully: {output_video_path}\n")

        # Step 4/5: Processing your video
        if enable_status_updates:
             _send_status_update(project_id, gallery_id, user_id, job_id, "processing", "finalizing", 80, "Processing your video...", render_type="VIDEO", logger=logger, wait_for_delivery=True)
        
        print(f"📤 STEP 4/5: Uploading to external API...\n{'─'*60}")
             
        upload_success = _upload_render_to_api(project_id, gallery_id, output_video_path, user_id, logger=logger, job_id=job_id)
        
        end_time = time.monotonic()
        if upload_success:
            print(f"\n{'='*80}\n🎉 ALL VIDEO STEPS COMPLETED SUCCESSFULLY! (Total Time: {(end_time - start_time):.2f}s)\n{'='*80}")
            logger.end_process(success=True)
        else:
            print(f"\n{'='*80}\n⚠️ VIDEO RENDER COMPLETED BUT UPLOAD FAILED (Total Time: {(end_time - start_time):.2f}s)\n{'='*80}")
            logger.end_process(success=True, error="Upload failed")
            
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(f"\n{'='*80}\n{COLOR_RED}❌ ERROR IN VIDEO BACKGROUND TASK{COLOR_RESET}\n{'='*80}\nJob ID: {job_id}\nError: {error_msg}\n{'='*80}\n")
        import traceback
        traceback.print_exc()
        
        if suppress_error_status:
            # Re-raise so the retry loop can catch it and retry
            logger.add_interrupt("error", error_msg)
            logger.end_process(success=False, error=error_msg)
            raise
        
        if enable_status_updates:
            _send_status_update(project_id, gallery_id, user_id, job_id, "error", "error", 0, "Scene is too large to render. Please try a different setup.", render_type="VIDEO", logger=logger)
        logger.add_interrupt("error", error_msg)
        logger.end_process(success=False, error=error_msg)
        raise  # Always propagate so retry loop detects the failure
    finally:
        pass

# --- API ENDPOINTS ---

@app.get("/")
async def root():
    """Root endpoint to confirm the service is running."""
    return {"message": "Service Online: The Dream Engine is ready to bring your visions to life."}





@app.post("/api/cancel-job/{job_id}")
async def cancel_job(job_id: str):
    """
    Cancel a specific job by marking it as failed.
    
    **No Authentication Required:** This endpoint is for internal dashboard use only.
    
    Args:
        job_id: The UUID of the job to cancel
    
    Returns:
        Success message with job details
    """
    with queue_lock:
        if job_id not in active_jobs:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        
        job = active_jobs[job_id]
        
        # Only allow cancelling queued or processing jobs
        if job["status"] not in ["queued", "processing"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot cancel job with status '{job['status']}'. Only queued or processing jobs can be cancelled."
            )
        
        # Mark job as failed
        job["status"] = "failed"
        job["failed_at"] = datetime.now().isoformat()
        job["error"] = "Cancelled by user"
        job["message"] = "Job Cancelled: The creation process has been gently set aside."
        job["progress"] = 0
        
        print(f"\n{'='*80}\n❌ JOB CANCELLED BY USER\n{'='*80}\nJob ID: {job_id}\nType: {job.get('type', 'unknown')}\nProject: {job.get('project_id', 'unknown')}\n{'='*80}\n")
    

    
    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "message": f"Job {job_id} cancelled successfully",
            "job_id": job_id
        }
    )


@app.post("/generate-glb-with-camera/", response_model=None)
async def generate_glb_with_camera(
    payload: GenerationPayload,
    current_user: CurrentUser
):
    """
    Generates a fast, low-resolution GLB for preview purposes (without camera - camera handled by Godot).
    
    **Authentication Required:** This endpoint requires a valid API key in the X-API-Key header.
    """
    job_id = str(uuid.uuid4())
    logger = RenderLogger(job_id)
    logger.start_process()
    
    try:
        print(f"Generating low-res GLB for preview (Job ID: {job_id}, User: {current_user.email})")
        base_glb = _build_base_glb(job_id, payload.floor_plan_data, use_high_res=False, logger=logger)
        # Create camera coordinates file - pass full payload dict to extract all camera data
        # Use dict(exclude_unset=False) to include all fields including optional camera data
        payload_dict = payload.dict(exclude_unset=False, exclude_none=False)
        camera_json_path = _create_camera_coordinates_file(base_glb, payload_dict, payload.render_quality, payload.aspect_ratio, logger=logger)
        logger.end_process(success=True)
        return FileResponse(path=base_glb, media_type='model/gltf-binary', filename=f"model_{job_id}.glb")
    except subprocess.CalledProcessError as e:
        error_msg = f"Godot render prep error:\n{e.stdout}\n{e.stderr}"
        logger.end_process(success=False, error=error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
    except Exception as e:
        logger.end_process(success=False, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-4k-render/")
async def generate_4k_render_async(
    current_user: CurrentUser,
    project_id: int = Query(..., alias="ProjectId", description="The master Project ID for the render."),
    id: int = Query(..., alias="Id", description="The specific gallery item ID for this render."),
    user_id: int = Query(0, alias="UserId", description="The user ID for this render."),
    job_id_param: Optional[str] = Query(None, alias="JobId", description="Optional manual job ID."),
    payload: GenerationPayload = Body(...)
):
    """
    Accepts a render job, adds it to the queue, and immediately returns a confirmation.
    Jobs are processed ONE AT A TIME to prevent render conflicts.
    
    **Authentication Required:** This endpoint requires a valid API key in the X-API-Key header.
    """
    job_id = job_id_param if job_id_param else str(uuid.uuid4())
    print(f"\n{'='*80}\n[IMAGE] NEW RENDER REQUEST RECEIVED\n{'='*80}")
    print(f"Job ID: {job_id}\nAuthenticated User: {current_user.email}\nProject ID: {project_id}\nGallery ID: {id}\nUser ID: {user_id}\nTimestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nQuality: {payload.render_quality}\n{'='*80}")
    
    # Add job to queue
    job_data = {
        'job_id': job_id,
        'type': 'image',
        'project_id': project_id,
        'gallery_id': id,
        'user_id': user_id,
        'payload': payload
    }
    
    # Track job status
    with queue_lock:
        queue_position = render_queue.qsize() + 1  # Position in queue (1-indexed)
        active_jobs[job_id] = {
            'status': 'queued',
            'position': queue_position,
            'queued_at': datetime.now().isoformat(),
            'render_type': 'IMAGE',
            'project_id': project_id,
            'gallery_id': id
        }
    
    render_queue.put(job_data)
    
    # Broadcast queue update to monitoring dashboard

    
    print(f"\n{COLOR_GREEN}[OK] Job added to queue (Position: {queue_position}){COLOR_RESET}")
    print(f"[STATS] Current queue size: {render_queue.qsize()}")
    print(f"[NOTE] Jobs are processed ONE AT A TIME to prevent system overload\n")
    
    return JSONResponse(
        status_code=202,
        content = {
            "status": "queued",
            "message": "Job Queued: Your vision has been successfully entrusted to us. The magic begins now.",
            "job_id": job_id,
            "queue_position": queue_position,
            "queue_size": render_queue.qsize(),
            "project_id": project_id,
            "UserId": user_id,
            "RenderId": 0,
            "RenderType": "IMAGE",
    }
    )

@app.post("/generate-4k-video/")
async def generate_4k_video_async(
    current_user: CurrentUser,
    project_id: int = Query(..., alias="ProjectId", description="The master Project ID for the video render."),
    id: int = Query(..., alias="Id", description="The specific gallery item ID for this video render."),
    user_id: int = Query(0, alias="UserId", description="The user ID for this video render."),
    job_id_param: Optional[str] = Query(None, alias="JobId", description="Optional manual job ID."),
    payload: VideoGenerationPayload = Body(...)
):
    """
    Accepts a video render job with camera animation, adds it to the queue, and immediately returns a confirmation.
    Jobs are processed ONE AT A TIME to prevent render conflicts.
    
    **Authentication Required:** This endpoint requires a valid API key in the X-API-Key header.
    """
    job_id = job_id_param if job_id_param else str(uuid.uuid4())
    video_animation = payload.video_animation
    duration_seconds = video_animation.duration_seconds if video_animation else 5.0
    fps = video_animation.fps if video_animation else 30
    
    print(f"\n{'='*80}\n[VIDEO] NEW VIDEO RENDER REQUEST RECEIVED\n{'='*80}")
    print(f"Job ID: {job_id}\nAuthenticated User: {current_user.email}\nProject ID: {project_id}\nGallery ID: {id}\nUser ID: {user_id}\nDuration: {duration_seconds}s @ {fps}fps\nTimestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*80}")
    
    # Add job to queue
    job_data = {
        'job_id': job_id,
        'type': 'video',
        'project_id': project_id,
        'gallery_id': id,
        'user_id': user_id,
        'payload': payload
    }
    
    # Track job status
    with queue_lock:
        queue_position = render_queue.qsize() + 1  # Position in queue (1-indexed)
        active_jobs[job_id] = {
            'status': 'queued',
            'position': queue_position,
            'queued_at': datetime.now().isoformat(),
            'render_type': 'VIDEO',
            'project_id': project_id,
            'gallery_id': id,
            'duration': duration_seconds,
            'fps': fps
        }
    
    render_queue.put(job_data)
    
    # Broadcast queue update to monitoring dashboard

    
    print(f"\n{COLOR_GREEN}✅ Video job added to queue (Position: {queue_position}){COLOR_RESET}")
    print(f"📊 Current queue size: {render_queue.qsize()}")
    print(f"💡 Jobs are processed ONE AT A TIME to prevent system overload\n")
    
    return JSONResponse(
        status_code=202,
        content={
            "status": "queued",
            "message": "Job Queued: Your cinematic story has been accepted. Preparing the stage.",
            "job_id": job_id,
            "queue_position": queue_position,
            "queue_size": render_queue.qsize(),
            "project_id": project_id,
            "UserId": user_id,
            "RenderId": 0,
            "RenderType": "VIDEO",
            "duration_seconds": duration_seconds,
            "fps": fps
        }
    )
@app.get("/logs/{job_id}")
async def get_job_log(job_id: str):
    """
    Returns the JSON render log for a specific job ID.
    Used by the monitoring system to collect artifacts.
    """
    log_path = os.path.join("logs", f"{job_id}_render.json")
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail=f"Log file for job {job_id} not found")
    
    return FileResponse(log_path, media_type="application/json", filename=f"{job_id}_render.json")

# --- AI RENDER ENDPOINT ---
from fastapi import File, UploadFile

@app.post("/ai-render/convert-day")
async def sd_airender(
    current_user: CurrentUser,
    project_id: int = Query(..., alias="ProjectId", description="The master Project ID"),
    id: int = Query(..., alias="Id", description="The specific gallery item ID"),
    user_id: int = Query(..., alias="UserId", description="The user ID"),
    job_id_param: Optional[str] = Query(None, alias="JobId", description="Optional existing Job ID"),
    image: UploadFile = File(..., description="PNG image file to convert"),
):
    if not ai_service.is_libraries_installed:
        raise HTTPException(status_code=503, detail="AI rendering is disabled - missing dependencies (diffusers, torch, etc.)")
    
    if image.content_type not in {"image/png", "image/x-png"}:
        raise HTTPException(status_code=400, detail="Only PNG images are supported for ai render.")
    
    file_bytes = await image.read()
    if len(file_bytes) / (1024 * 1024) > ai_service.max_file_mb:
        raise HTTPException(status_code=400, detail=f"File exceeds {ai_service.max_file_mb}MB.")
    
    job_id = job_id_param if job_id_param else str(uuid.uuid4())
    input_path = ai_service.input_dir / f"{job_id}_{Path(image.filename).stem}.png"
    output_path = ai_service.output_dir / f"{job_id}_render.png"
    
    # Save uploaded file
    try:
        input_path.write_bytes(file_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save input image: {e}")
    
    with queue_lock:
        active_jobs[job_id] = {
            "status": "queued",
            "type": "ai render",
            "display_type": "ai render",
            "render_type": ai_service.status_render_type,
            "project_id": project_id,
            "gallery_id": id,
            "user_id": user_id,
            "queued_at": datetime.now().isoformat(),
            "progress": 0,
            "message": "AI render job queued.",
            "current_step": "queued",
            "input_filename": image.filename,
            "output_filename": output_path.name,
            "started_at": None,
            "completed_at": None,
            "failed_at": None,
        }
    
    job_data = {
        "job_id": job_id,
        "project_id": project_id,
        "gallery_id": id,
        "user_id": user_id,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "filename": image.filename,
    }
    
    ai_service.render_queue.put(job_data)
    
    print(f"{COLOR_GREEN}✅ AI Render job added to queue: {job_id}{COLOR_RESET}")
    
    return JSONResponse(
        status_code=202,
        content={
            "status": "queued",
            "job_id": job_id,
            "ProjectId": project_id,
            "Id": id,
            "UserId": user_id,
            "RenderType": ai_service.status_render_type
        }
    )


@app.get("/engine-status")
def ai_engine_status():
    jobs = _get_ai_jobs_snapshot()
    queued = sum(1 for job in jobs if job.get("status") == "queued")
    processing = sum(1 for job in jobs if job.get("status") == "processing")
    completed = sum(1 for job in jobs if job.get("status") == "completed")
    failed = sum(1 for job in jobs if job.get("status") in {"failed", "error"})

    return {
        "status": "ok",
        "online": True,
        "is_paused": False,
        "is_busy": CURRENT_STATUS.get("is_busy", False),
        "queue_size": queued,
        "active_jobs": queued + processing,
        "completed_jobs": completed,
        "failed_jobs": failed,
        "current_job": CURRENT_STATUS.get("active_job"),
    }


@app.get("/health")
def ai_health():
    return ai_engine_status()


@app.get("/queue-status-public")
def ai_queue_status_public():
    jobs = _get_ai_jobs_snapshot()
    queued = sum(1 for job in jobs if job.get("status") == "queued")
    processing = sum(1 for job in jobs if job.get("status") == "processing")
    completed = sum(1 for job in jobs if job.get("status") == "completed")
    failed = sum(1 for job in jobs if job.get("status") in {"failed", "error"})

    return JSONResponse(
        content={
            "queue_size": queued,
            "active_jobs_count": queued + processing,
            "processing_jobs_count": processing,
            "completed_jobs_count": completed,
            "failed_jobs_count": failed,
            "is_paused": False,
            "jobs": jobs,
            "engine_online": True,
            "engine_name": "External 4K + AI Engine",
            "engine_url": os.getenv("AI_RENDER_ENGINE_URL", "embedded://external-4k-ai-main"),
        }
    )


@app.get("/queue-status")
def ai_queue_status():
    return ai_queue_status_public()


@app.get("/job-status/{job_id}")
def ai_job_status(job_id: str):
    with queue_lock:
        job = active_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"AI job {job_id} not found")
    return {"job_id": job_id, **_build_ai_job_snapshot(job_id, dict(job))}


@app.get("/ai-render/health")
def ai_render_health_alias():
    return ai_engine_status()


@app.get("/ai-render/")
@app.get("/ai-render")
def ai_render_root_alias():
    return ai_engine_status()


@app.get("/ai-render/queue-status-public")
def ai_render_queue_status_public_alias():
    return ai_queue_status_public()


@app.get("/ai-render/queue-status")
def ai_render_queue_status_alias():
    return ai_queue_status_public()


@app.get("/ai-render/job-status/{job_id}")
def ai_render_job_status_alias(job_id: str):
    return ai_job_status(job_id)
