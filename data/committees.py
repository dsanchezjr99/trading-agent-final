"""
committees.py
Congressional committee membership — flags when a member trades in a sector
their committee directly oversees (highest-conviction signal class).

Source: unitedstates/congress-legislators (GitHub, no API key required)
  https://github.com/unitedstates/congress-legislators
  Fetched fresh on startup, cached to disk for the session.
"""

import json
import os
import requests
from pathlib import Path

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

MEMBERSHIP_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators"
    "/main/committee-membership-current.yaml"
)

CACHE_FILE = Path(__file__).parent.parent / "logs" / "committee_cache.json"

# ── Target committees and their GICS sector overlap ───────────────────────────
# Only the committees where oversight = real informational edge on specific sectors.
# Key: committee code from unitedstates/congress-legislators
# Value: (human name, [GICS sectors])

TARGET_COMMITTEES: dict[str, tuple[str, list[str]]] = {
    "HSAS": ("House Armed Services",            ["Defense", "Aerospace & Defense"]),
    "SSAS": ("Senate Armed Services",           ["Defense", "Aerospace & Defense"]),
    "HLIG": ("House Intelligence",              ["Defense", "Technology", "Aerospace & Defense"]),
    "SLIN": ("Senate Intelligence",             ["Defense", "Technology", "Aerospace & Defense"]),
    "HSBA": ("House Financial Services",        ["Financials", "Banks", "Real Estate"]),
    "SSBK": ("Senate Banking",                  ["Financials", "Banks", "Real Estate"]),
    "HSIF": ("House Energy and Commerce",       ["Energy", "Technology", "Health Care", "Communication Services"]),
    "SSEG": ("Senate Energy",                   ["Energy", "Utilities"]),
    "SSHR": ("Senate HELP",                     ["Health Care", "Pharmaceuticals & Biotechnology"]),
    "HSWM": ("House Ways and Means",            ["Financials", "Health Care"]),
    "SSFI": ("Senate Finance",                  ["Financials", "Health Care"]),
    "HSSY": ("House Science & Technology",      ["Technology", "Aerospace & Defense"]),
    "SSCM": ("Senate Commerce",                 ["Technology", "Communication Services", "Industrials"]),
    "HSAP": ("House Appropriations",            ["Defense", "Health Care", "Industrials"]),
    "SSAP": ("Senate Appropriations",           ["Defense", "Health Care", "Industrials"]),
    "HSJU": ("House Judiciary",                 ["Technology"]),
    "SSJU": ("Senate Judiciary",                ["Technology"]),
}

# ── In-memory cache ────────────────────────────────────────────────────────────
_member_committees: dict[str, list[str]] = {}   # normalized_name → [committee human names]
_loaded: bool = False


def _normalize(name: str) -> str:
    return name.lower().strip()


def _load(force: bool = False) -> None:
    """
    Build the member→committees map from the GitHub YAML.
    Falls back to disk cache if the fetch fails.
    Skips if already loaded (once per process lifetime unless force=True).
    """
    global _loaded, _member_committees

    if _loaded and not force:
        return

    if not _YAML_AVAILABLE:
        print("[committees] PyYAML not installed — committee tagging disabled. Run: pip install pyyaml")
        _loaded = True
        return

    # Try fetching fresh data
    try:
        resp = requests.get(MEMBERSHIP_URL, timeout=15)
        resp.raise_for_status()
        raw = yaml.safe_load(resp.text)
        _build_map(raw)
        _save_cache()
        print(f"[committees] Loaded live committee data — {len(_member_committees)} members mapped")
        _loaded = True
        return
    except Exception as e:
        print(f"[committees] Live fetch failed ({e}) — trying disk cache")

    # Fall back to disk cache
    if CACHE_FILE.exists():
        try:
            _member_committees = json.loads(CACHE_FILE.read_text())
            print(f"[committees] Loaded from disk cache — {len(_member_committees)} members")
            _loaded = True
            return
        except Exception as e:
            print(f"[committees] Disk cache load failed: {e}")

    print("[committees] No committee data available — using static fallback only")
    _load_static_fallback()
    _loaded = True


def _build_map(raw: dict) -> None:
    """Parse YAML committee membership into member→[committee names] map."""
    global _member_committees
    _member_committees = {}

    for code, members in raw.items():
        if code not in TARGET_COMMITTEES:
            continue
        committee_name = TARGET_COMMITTEES[code][0]
        if not isinstance(members, list):
            continue
        for m in members:
            name = _normalize(m.get("name", ""))
            if not name:
                continue
            if name not in _member_committees:
                _member_committees[name] = []
            if committee_name not in _member_committees[name]:
                _member_committees[name].append(committee_name)


def _save_cache() -> None:
    try:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(_member_committees, indent=2))
    except Exception as e:
        print(f"[committees] Cache save failed: {e}")


def _load_static_fallback() -> None:
    """Minimal static map for the 119th Congress — used only if GitHub and cache both fail."""
    global _member_committees
    static = {
        # House Armed Services
        "mike rogers": ["House Armed Services"],
        "adam smith": ["House Armed Services"],
        "rob wittman": ["House Armed Services"],
        "jack bergman": ["House Armed Services"],
        "don bacon": ["House Armed Services"],
        "austin scott": ["House Armed Services"],
        # Senate Armed Services
        "roger wicker": ["Senate Armed Services", "Senate Commerce"],
        "jack reed": ["Senate Armed Services"],
        "tom cotton": ["Senate Armed Services", "Senate Intelligence"],
        "angus king": ["Senate Armed Services", "Senate Intelligence"],
        "jeanne shaheen": ["Senate Armed Services"],
        "kirsten gillibrand": ["Senate Armed Services"],
        "mark kelly": ["Senate Armed Services"],
        # Intelligence
        "mike turner": ["House Intelligence"],
        "jim himes": ["House Intelligence"],
        "mark warner": ["Senate Intelligence", "Senate Banking"],
        "marco rubio": ["Senate Intelligence"],
        # Financial Services
        "french hill": ["House Financial Services"],
        "maxine waters": ["House Financial Services"],
        "tim scott": ["Senate Banking"],
        "sherrod brown": ["Senate Banking"],
        "elizabeth warren": ["Senate Banking"],
        # Energy
        "brett guthrie": ["House Energy and Commerce"],
        "frank pallone": ["House Energy and Commerce"],
        "john barrasso": ["Senate Energy"],
        "joe manchin": ["Senate Energy"],
        "lisa murkowski": ["Senate Energy", "Senate Appropriations"],
        # Health
        "bill cassidy": ["Senate HELP"],
        "bernie sanders": ["Senate HELP"],
        "patty murray": ["Senate HELP"],
        # Ways & Means / Finance
        "jason smith": ["House Ways and Means"],
        "richard neal": ["House Ways and Means"],
        # Appropriations
        "rosa delauro": ["House Appropriations"],
        "kay granger": ["House Appropriations"],
        "susan collins": ["Senate Appropriations", "Senate Intelligence"],
        # Tech
        "frank lucas": ["House Science & Technology"],
        "ted cruz": ["Senate Commerce"],
        "maria cantwell": ["Senate Commerce", "Senate Energy"],
        "amy klobuchar": ["Senate Commerce"],
        "jim jordan": ["House Judiciary"],
        "jerry nadler": ["House Judiciary"],
    }
    _member_committees.update(static)


# ── Public API ────────────────────────────────────────────────────────────────

def get_member_committees(member_name: str) -> list[str]:
    """
    Returns list of relevant committee names for a congressional member.
    Loads data on first call.
    """
    _load()
    normalized = _normalize(member_name)

    if normalized in _member_committees:
        return _member_committees[normalized]

    # Try last-name-only match (handles name format variations), but only if unambiguous
    last = normalized.split()[-1] if normalized else ""
    matches = [v for k, v in _member_committees.items() if k.split()[-1] == last]
    if len(matches) == 1:
        return matches[0]

    return []


def get_committee_tag(member_name: str) -> str:
    """
    Returns a bracketed committee tag for the trade summary prompt.
    E.g., "[CMTE: House Armed Services, Senate Intelligence]"
    Returns empty string if no committee data found.
    """
    committees = get_member_committees(member_name)
    if not committees:
        return ""
    return f"[CMTE: {', '.join(committees[:2])}]"
