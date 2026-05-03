import sys

with open(r'c:\Zlendo2026\ZlendoRenderEngine - stage4.6\main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

replacement = """    # 1. Check Assets Map (GLBs)
    # The map keys are consistently percent-encoded for Azure.
    # We decode first to avoid double encoding, then re-encode safely.
    from urllib.parse import quote, unquote
    try:
        norm_url_encoded = quote(unquote(norm_url), safe='/')
        norm_url_decoded = unquote(norm_url)
    except Exception:
        norm_url_encoded = norm_url
        norm_url_decoded = norm_url

    # Try exact match first
    hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(norm_url)
    
    # Try fully encoded match ('&' -> '%26', spaces -> '%20')
    if not hp_found:
        hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(norm_url_encoded)

    # Try fully decoded match
    if not hp_found:
        hp_found = _CACHED_ASSET_URL_TO_HP_MAP.get(norm_url_decoded)

"""

# Lines 227 to 253 (0-indexed) are where the original block was
new_lines = lines[:227] + [replacement] + lines[254:]

with open(r'c:\Zlendo2026\ZlendoRenderEngine - stage4.6\main.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Updated main.py successfully")
