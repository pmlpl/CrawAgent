"""Font decryption — decode PUA-encrypted text via Source Han Sans glyph matching.

Strategy:
  Fanqie Novel encrypts text with custom fonts mapping real chars to PUA (U+E000–U+F8FF)
  codepoints. The font is a subset of Source Han Sans SC with curves flattened to lines.
  We match each PUA glyph against SHS glyphs by image similarity (16x16 coarse → 64x64 fine).

Caching:
  - SHS pixel lookup is built once and cached to data/font_cache/shs_lookup.npz
  - Per-font PUA mapping is cached to data/font_cache/pua_map_<hash>.json
"""
import hashlib
import json
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SHS_FONT_PATH = _PROJECT_ROOT / "assets" / "fonts" / "SourceHanSansSC-Normal.otf"
SHS_DOWNLOAD_URL = (
    "https://cdn.jsdelivr.net/gh/adobe-fonts/source-han-sans"
    "@release/OTF/SimplifiedChinese/SourceHanSansSC-Normal.otf"
)
CACHE_DIR = _PROJECT_ROOT / "data" / "font_cache"

RENDER_SIZE = 160
IMG_SIZE_COARSE = 16
IMG_SIZE_FINE = 64
TOP_K = 30
PUA_LOW, PUA_HIGH = 0xE000, 0xF8FF
CJK_LOW, CJK_HIGH = 0x4E00, 0x9FFF

# In-process cache for SHS lookup (avoids reloading from disk on every call)
_shs_lookup = None


# ---------------------------------------------------------------------------
# Source Han Sans font management
# ---------------------------------------------------------------------------
def _ensure_shs_font() -> None:
    """Download Source Han Sans SC if not present locally."""
    if SHS_FONT_PATH.exists():
        return
    SHS_FONT_PATH.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(SHS_DOWNLOAD_URL, timeout=120, stream=True)
    resp.raise_for_status()
    with open(SHS_FONT_PATH, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _render_pixels(pil_font: ImageFont.FreeTypeFont, char: str, out_size: int):
    """Render a single char centered in a square canvas; return flattened pixel vector."""
    img = Image.new("L", (RENDER_SIZE, RENDER_SIZE), 255)
    draw = ImageDraw.Draw(img)
    try:
        bbox = pil_font.getbbox(char)
    except Exception:
        return None
    if not bbox or bbox[2] == bbox[0] or bbox[3] == bbox[1]:
        return None
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (RENDER_SIZE - w) // 2 - bbox[0]
    y = (RENDER_SIZE - h) // 2 - bbox[1]
    draw.text((x, y), char, font=pil_font, fill=0)
    small = img.resize((out_size, out_size), Image.LANCZOS)
    arr = np.asarray(small, dtype=np.float32) / 255.0
    return arr.flatten()


# ---------------------------------------------------------------------------
# SHS lookup (built once, cached to disk)
# ---------------------------------------------------------------------------
def _build_shs_lookup():
    """Build and cache SHS pixel arrays for all CJK chars."""
    global _shs_lookup
    if _shs_lookup is not None:
        return _shs_lookup

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / "shs_lookup.npz"

    if cache_file.exists():
        data = np.load(cache_file, allow_pickle=True)
        _shs_lookup = (data["chars"].tolist(), data["px16"], data["px64"])
        return _shs_lookup

    _ensure_shs_font()
    shs_font = TTFont(str(SHS_FONT_PATH))
    shs_cmap = shs_font.getBestCmap() or {}
    pil_font = ImageFont.truetype(str(SHS_FONT_PATH), size=RENDER_SIZE)

    chars = []
    px16_list = []
    px64_list = []
    for cc in shs_cmap:
        if not (CJK_LOW <= cc <= CJK_HIGH):
            continue
        p16 = _render_pixels(pil_font, chr(cc), IMG_SIZE_COARSE)
        p64 = _render_pixels(pil_font, chr(cc), IMG_SIZE_FINE)
        if p16 is not None and p64 is not None:
            chars.append(chr(cc))
            px16_list.append(p16)
            px64_list.append(p64)

    px16 = np.array(px16_list, dtype=np.float32)
    px64 = np.array(px64_list, dtype=np.float32)
    np.savez(cache_file, chars=np.array(chars), px16=px16, px64=px64)
    _shs_lookup = (chars, px16, px64)
    shs_font.close()
    return _shs_lookup


# ---------------------------------------------------------------------------
# PUA mapping (per encrypted font, cached to disk)
# ---------------------------------------------------------------------------
def _build_pua_mapping(font_url: str) -> dict:
    """Build PUA codepoint → real char mapping for the given encrypted font URL."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.md5(font_url.encode()).hexdigest()[:16]
    cache_file = CACHE_DIR / f"pua_map_{url_hash}.json"

    if cache_file.exists():
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    # Download encrypted font
    resp = requests.get(font_url, timeout=30)
    resp.raise_for_status()
    enc_font = TTFont(BytesIO(resp.content))
    enc_cmap = enc_font.getBestCmap() or {}
    pua_codes = [c for c in enc_cmap if PUA_LOW <= c <= PUA_HIGH]
    if not pua_codes:
        enc_font.close()
        return {}

    # woff2 → ttf for PIL rendering
    enc_font.flavor = None
    ttf_buf = BytesIO()
    enc_font.save(ttf_buf)
    enc_ttf_bytes = ttf_buf.getvalue()

    chars, shs_px16, shs_px64 = _build_shs_lookup()
    pil_enc = ImageFont.truetype(BytesIO(enc_ttf_bytes), size=RENDER_SIZE)

    mapping = {}
    for pc in pua_codes:
        p16 = _render_pixels(pil_enc, chr(pc), IMG_SIZE_COARSE)
        if p16 is None:
            continue
        # Stage 1: coarse match at 16×16
        diffs16 = np.sum((shs_px16 - p16) ** 2, axis=1)
        top_k = np.argsort(diffs16)[:TOP_K]
        # Stage 2: fine match at 64×64
        p64 = _render_pixels(pil_enc, chr(pc), IMG_SIZE_FINE)
        if p64 is None:
            mapping[str(pc)] = chars[int(top_k[0])]
            continue
        diffs64 = np.sum((shs_px64[top_k] - p64) ** 2, axis=1)
        best_local = int(np.argmin(diffs64))
        best_idx = int(top_k[best_local])
        mapping[str(pc)] = chars[best_idx]

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)

    enc_font.close()
    return mapping


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def has_pua(text: str) -> bool:
    """Check if text contains PUA characters (U+E000–U+F8FF)."""
    return any(PUA_LOW <= ord(c) <= PUA_HIGH for c in text)


def decrypt_text(text: str, font_url: str) -> str:
    """Decrypt PUA-encrypted text using the font at *font_url*.

    Args:
        text: Text that may contain PUA characters.
        font_url: URL of the encrypted font file (woff2/woff/otf).

    Returns:
        Text with PUA chars replaced by their real counterparts.
        On any error the original text is returned unchanged.
    """
    if not has_pua(text):
        return text
    try:
        mapping = _build_pua_mapping(font_url)
    except Exception:
        return text
    if not mapping:
        return text
    return "".join(
        mapping.get(str(ord(c)), c) if PUA_LOW <= ord(c) <= PUA_HIGH else c
        for c in text
    )
