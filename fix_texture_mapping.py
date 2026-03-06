"""
=============================================================================
  FIX TEXTURE MAPPING — Re-maps LR→HR texture URLs by texture type
  ─────────────────────────────────────────────────────────────────────────
  Problem:
    The API returns  low_Resolution_Url[]  and  high_Resolution_Url[]  in
    different orders.  The original downloader paired them by array index,
    so  LR_Roughness  got mapped to  HR_AmbientOcclusion  etc.

  Fix:
    This script fetches the texture catalogue from the API, extracts the
    "texture type" from each filename (BaseColor, NormalGL, Roughness …),
    and matches LR↔HR by that type.  Then it patches asset_url_map.json
    to have the correct  LR_URL → local_HR_file  mapping.

  Usage:
    python fix_texture_mapping.py
=============================================================================
"""

import os
import re
import json
import hashlib
import logging
import requests
from pathlib import Path
from urllib.parse import urlparse, unquote

# ---------------------------------------------------------------------------
# CONFIGURATION  (same as download_all_assets.py)
# ---------------------------------------------------------------------------
ASSETS_AND_TEXTURE_API_KEY         = "zrsk_dev_41fbb72c9a0e5f1c8d2a9b6d4e8f3c2"
ASSETS_AND_TEXTURE_API_HEADER_NAME = "ZRealtyServiceApiKey"
TEXTURE_ENDPOINT = "http://216.48.182.24:4050/api/v1/TextureMaster/GetAllTextureLibraries"

BLOB_BASE = "https://zrealtystoragedev.blob.core.windows.net"

BASE_DIR     = Path(__file__).parent
DOWNLOAD_DIR = BASE_DIR / "asset_downloads"
URL_MAP_FILE = DOWNLOAD_DIR / "asset_url_map.json"
BACKUP_FILE  = DOWNLOAD_DIR / "asset_url_map_backup.json"

API_HEADERS = {
    ASSETS_AND_TEXTURE_API_HEADER_NAME: ASSETS_AND_TEXTURE_API_KEY,
    "Accept": "application/json",
}

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE_DIR / "fix_texture_mapping.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def short_hash(text: str, length: int = 8) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:length]


def relative_to_full_url(rel_path: str) -> str:
    """Turn a relative API path into a full Azure Blob URL."""
    rel_path = rel_path.strip()
    if rel_path.startswith("http"):
        return rel_path
    rel_path = rel_path.lstrip("/")
    return f"{BLOB_BASE}/{rel_path}"


def url_to_local_filename(url: str) -> str:
    """<8-char-hash>_<original-basename>"""
    clean = url.split("?")[0]
    basename = os.path.basename(unquote(urlparse(clean).path))
    if not basename:
        basename = "unnamed_file"
    return f"{short_hash(clean)}_{basename}"


def extract_texture_type(url: str) -> str:
    """
    Extract the texture type keyword from a URL's filename.
    
    Examples:
        '.../Planks008_LR_NormalGL.png'    → 'normalgl'
        '.../Planks008_HR_BaseColor.png'   → 'basecolor'
        '.../Planks008_LR_Roughness.png'   → 'roughness'
        '.../Planks008_HR_AmbientOcclusion.png' → 'ambientocclusion'
        '.../Wood_LR_HR_BaseColor.png'     → 'basecolor'
        '.../fabric_LR_Albedo.png'         → 'albedo'
    
    Strategy:
      1. Get the filename stem (without extension)
      2. Remove common resolution prefixes (LR_, HR_, etc.)
      3. Take the last segment after the final underscore as the type
      4. Fallback: use the cleaned filename if no underscore pattern matches
    """
    clean = url.split("?")[0]
    basename = os.path.basename(unquote(urlparse(clean).path))
    stem = os.path.splitext(basename)[0]  # remove extension
    
    # Known texture type keywords (case-insensitive)
    known_types = [
        'basecolor', 'base_color', 'color', 'albedo', 'diffuse', 'diff',
        'normal', 'normaldx', 'normalgl', 'normal-dx', 'normal-gl',
        'roughness', 'rough',
        'displacement', 'disp', 'height',
        'ambientocclusion', 'ambient_occlusion', 'ao',
        'metallic', 'metalness', 'metal',
        'specular', 'specularlevel',
        'opacity', 'emission',
        'mortar', 'jointmask', 'smoothnes', 'metallicsmoothnes',
    ]
    
    # Try to find the texture type at the end of the filename
    # Pattern: anything_<resolution>_<type> where resolution is LR/HR/etc
    # Also handles: anything_<type> directly
    
    stem_lower = stem.lower()
    
    # Remove resolution markers to get to the type
    # Patterns: _LR_, _HR_, _LR_HR_, _4K_, _4K-PNG_, _1024, etc.
    # Clean these out so we can find the pure type at the end
    cleaned = re.sub(
        r'_(?:lr|hr|lr_hr|4k|4k-png|1k|2k|1024|2048|4096)(?=_|$)',
        '',
        stem_lower,
        flags=re.IGNORECASE,
    )
    
    # The texture type should be the last segment
    parts = cleaned.rsplit('_', 1)
    if len(parts) >= 2:
        candidate = parts[-1].strip()
        if candidate:
            return candidate
    
    # If we only got one part (no underscore after cleaning), try the whole cleaned string
    # Split by underscore from the original stem and check the last part
    orig_parts = stem_lower.split('_')
    for part in reversed(orig_parts):
        part = part.strip()
        if part in ('lr', 'hr', '4k', '1k', '2k', '1024', '2048', '4096', 'png', 'jpg', 'jpeg'):
            continue
        if part:
            return part
    
    return stem_lower


def match_lr_hr_by_type(lr_urls: list, hr_urls: list, texture_name: str) -> list:
    """
    Match LR and HR URLs by their texture type (BaseColor, NormalGL, etc.)
    
    Returns: list of (lr_url, hr_url) tuples, correctly paired by type.
    Any unmatched URLs are reported.
    """
    # Build lookup: type → URL for HR
    hr_type_map = {}
    for hr_url in hr_urls:
        ttype = extract_texture_type(hr_url)
        if ttype in hr_type_map:
            log.warning(
                "  ⚠ Texture '%s': duplicate HR type '%s' — %s vs %s",
                texture_name, ttype, hr_type_map[ttype], hr_url,
            )
        hr_type_map[ttype] = hr_url
    
    pairs = []
    unmatched_lr = []
    
    for lr_url in lr_urls:
        lr_type = extract_texture_type(lr_url)
        hr_url = hr_type_map.get(lr_type)
        
        if hr_url:
            pairs.append((lr_url, hr_url))
        else:
            # Try a fuzzy match — some textures have slightly different naming
            # e.g., 'color' vs 'basecolor', 'diff' vs 'basecolor'
            matched = False
            type_aliases = {
                'basecolor': ['color', 'albedo', 'diffuse', 'diff', 'base_color'],
                'color': ['basecolor', 'albedo', 'diffuse', 'diff'],
                'albedo': ['basecolor', 'color', 'diffuse', 'diff'],
                'normal': ['normalgl', 'normaldx'],
                'normalgl': ['normal'],
                'normaldx': ['normal'],
                'height': ['displacement', 'disp'],
                'displacement': ['height', 'disp'],
                'disp': ['displacement', 'height'],
                'roughness': ['rough'],
                'rough': ['roughness'],
                'ao': ['ambientocclusion', 'ambient_occlusion'],
                'ambientocclusion': ['ao'],
                'metallic': ['metalness', 'metal'],
                'metalness': ['metallic', 'metal'],
                'specular': ['specularlevel'],
                'specularlevel': ['specular'],
            }
            
            aliases = type_aliases.get(lr_type, [])
            for alias in aliases:
                if alias in hr_type_map:
                    pairs.append((lr_url, hr_type_map[alias]))
                    matched = True
                    log.info(
                        "  ℹ Texture '%s': fuzzy matched LR '%s' → HR '%s'",
                        texture_name, lr_type, alias,
                    )
                    break
            
            if not matched:
                unmatched_lr.append((lr_url, lr_type))
    
    if unmatched_lr:
        log.warning(
            "  ⚠ Texture '%s': %d LR URL(s) could not be matched to HR:",
            texture_name, len(unmatched_lr),
        )
        for url, ttype in unmatched_lr:
            log.warning("      type='%s'  →  %s", ttype, url)
    
    return pairs


# ---------------------------------------------------------------------------
# MAIN FIX LOGIC
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 70)
    log.info(" FIX TEXTURE MAPPING — Re-map LR→HR by texture type")
    log.info("=" * 70)

    # 1. Load existing url map
    if not URL_MAP_FILE.exists():
        log.error("❌ asset_url_map.json not found at %s", URL_MAP_FILE)
        return
    
    with open(URL_MAP_FILE, encoding="utf-8") as f:
        url_map = json.load(f)
    log.info("Loaded %d entries from asset_url_map.json", len(url_map))
    
    # 2. Backup
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(url_map, f, indent=2, ensure_ascii=False)
    log.info("Backup saved to %s", BACKUP_FILE.name)
    
    # 3. Fetch texture catalogue from API
    log.info("Fetching texture catalogue from API…")
    try:
        resp = requests.get(TEXTURE_ENDPOINT, headers=API_HEADERS, timeout=60)
        resp.raise_for_status()
        textures = resp.json()
        if not isinstance(textures, list):
            log.error("❌ API did not return a list")
            return
        log.info("  → %d texture records received", len(textures))
    except Exception as exc:
        log.error("❌ Failed to fetch texture catalogue: %s", exc)
        return
    
    # 4. For each completed texture, re-map LR→HR by type
    fixed_count = 0
    total_pairs = 0
    
    for rec in textures:
        name   = rec.get("textureLibraryName", "?")
        status = rec.get("textureProcessStatus", 0)
        
        if status != 2:
            continue
        
        lr_paths = rec.get("low_Resolution_Url") or []
        hr_paths = rec.get("high_Resolution_Url") or []
        
        if not lr_paths or not hr_paths:
            continue
        
        # Convert to full URLs
        lr_urls = [relative_to_full_url(p) for p in lr_paths]
        hr_urls = [relative_to_full_url(p) for p in hr_paths]
        
        # Match by type
        pairs = match_lr_hr_by_type(lr_urls, hr_urls, name)
        total_pairs += len(pairs)
        
        for lr_url, hr_url in pairs:
            # Compute what the local filename SHOULD be for this HR URL
            correct_local_filename = url_to_local_filename(hr_url)
            correct_local_path = str(DOWNLOAD_DIR / correct_local_filename)
            
            # Check if this LR URL exists in the map
            if lr_url in url_map:
                current_value = url_map[lr_url]
                if current_value != correct_local_path:
                    # This mapping was wrong — fix it
                    old_basename = os.path.basename(current_value)
                    new_basename = os.path.basename(correct_local_path)
                    log.info(
                        "  🔧 FIX [%s] LR=%s",
                        name,
                        os.path.basename(unquote(urlparse(lr_url).path)),
                    )
                    log.info(
                        "         OLD: %s  →  NEW: %s",
                        old_basename, new_basename,
                    )
                    url_map[lr_url] = correct_local_path
                    fixed_count += 1
            else:
                # LR URL not in map yet — add it
                url_map[lr_url] = correct_local_path
                fixed_count += 1
                log.info(
                    "  ➕ ADD [%s] %s → %s",
                    name,
                    os.path.basename(unquote(urlparse(lr_url).path)),
                    os.path.basename(correct_local_path),
                )
    
    # 5. Save fixed map
    with open(URL_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(url_map, f, indent=2, ensure_ascii=False)
    
    log.info("")
    log.info("=" * 70)
    log.info(" FIX COMPLETE")
    log.info("─" * 70)
    log.info("  Total texture pairs processed: %d", total_pairs)
    log.info("  Mappings fixed/added:          %d", fixed_count)
    log.info("  asset_url_map.json saved:      %d entries", len(url_map))
    log.info("  Backup:                        %s", BACKUP_FILE.name)
    log.info("=" * 70)


if __name__ == "__main__":
    main()
