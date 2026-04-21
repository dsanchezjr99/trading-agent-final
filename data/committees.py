"""
committees.py
Congressional committee membership — used to flag when a member trades
in a sector their committee directly oversees (highest-conviction signal).

Primary source: ProPublica Congress API (free key at propublica.org/datastore/api)
  Set PROPUBLICA_API_KEY in .env to enable live lookups.
Fallback: Static 119th Congress map (chairs + key members of market-moving committees).
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

PROPUBLICA_API_KEY = os.getenv("PROPUBLICA_API_KEY", "")
PROPUBLICA_BASE    = "https://api.propublica.org/congress/v1"
CONGRESS_NUM       = 119  # 119th Congress (2025–2027)

# ── Sector relevance map ──────────────────────────────────────────────────────
# Which committees carry oversight advantage over which GICS sectors.
# When a member trades in a sector their committee oversees, it's the highest
# signal strength — they have non-public knowledge of contracts, regulation, etc.

COMMITTEE_TO_SECTORS: dict[str, list[str]] = {
    "Armed Services":                          ["Defense", "Aerospace & Defense"],
    "Intelligence":                            ["Defense", "Technology", "Aerospace & Defense"],
    "Financial Services":                      ["Financials", "Banks"],
    "Banking, Housing, and Urban Affairs":     ["Financials", "Real Estate"],
    "Energy and Commerce":                     ["Energy", "Technology", "Health Care", "Communication Services"],
    "Energy and Natural Resources":            ["Energy", "Utilities"],
    "Environment and Public Works":            ["Energy", "Utilities", "Industrials"],
    "Health, Education, Labor, and Pensions":  ["Health Care", "Pharmaceuticals & Biotechnology"],
    "Ways and Means":                          ["Financials", "Health Care"],
    "Agriculture":                             ["Consumer Staples", "Materials"],
    "Science, Space, and Technology":          ["Technology", "Aerospace & Defense"],
    "Commerce, Science, and Transportation":   ["Technology", "Communication Services", "Industrials"],
    "Judiciary":                               ["Technology"],
    "Appropriations":                          ["Defense", "Health Care", "Industrials"],
}

# ── Static 119th Congress fallback ───────────────────────────────────────────
# Covers chairs, ranking members, and the most active traders from each
# key oversight committee. Updated for 2025–2027 session.

STATIC_MEMBER_COMMITTEES: dict[str, list[str]] = {
    # House Armed Services
    "mike rogers":              ["Armed Services"],
    "adam smith":               ["Armed Services"],
    "rob wittman":              ["Armed Services"],
    "jack bergman":             ["Armed Services"],
    "don bacon":                ["Armed Services"],
    "austin scott":             ["Armed Services"],
    "michael waltz":            ["Armed Services"],
    "elissa slotkin":           ["Armed Services"],
    "chrissy houlahan":         ["Armed Services"],
    "seth moulton":             ["Armed Services"],
    "mike gallagher":           ["Armed Services"],
    "pat ryan":                 ["Armed Services"],
    # Senate Armed Services
    "roger wicker":             ["Armed Services", "Commerce, Science, and Transportation"],
    "jack reed":                ["Armed Services"],
    "tom cotton":               ["Armed Services", "Intelligence"],
    "angus king":               ["Armed Services", "Intelligence"],
    "jeanne shaheen":           ["Armed Services"],
    "kirsten gillibrand":       ["Armed Services"],
    "mark kelly":               ["Armed Services"],
    "dan sullivan":             ["Armed Services"],
    "joni ernst":               ["Armed Services"],
    "thom tillis":              ["Armed Services"],
    # House Intelligence
    "mike turner":              ["Intelligence"],
    "jim himes":                ["Intelligence"],
    "adam schiff":              ["Intelligence"],
    "andre carson":             ["Intelligence"],
    "raja krishnamoorthi":      ["Intelligence"],
    # Senate Intelligence
    "mark warner":              ["Intelligence"],
    "marco rubio":              ["Intelligence"],
    "richard burr":             ["Intelligence"],
    "susan collins":            ["Intelligence", "Appropriations"],
    "john ratcliffe":           ["Intelligence"],
    "james lankford":           ["Intelligence"],
    # House Financial Services
    "french hill":              ["Financial Services"],
    "maxine waters":            ["Financial Services"],
    "bill huizenga":            ["Financial Services"],
    "ann wagner":               ["Financial Services"],
    "blaine luetkemeyer":       ["Financial Services"],
    "patrick mchenry":          ["Financial Services"],
    "josh gottheimer":          ["Financial Services"],
    "brad sherman":             ["Financial Services"],
    # Senate Banking
    "tim scott":                ["Banking, Housing, and Urban Affairs"],
    "sherrod brown":            ["Banking, Housing, and Urban Affairs"],
    "elizabeth warren":         ["Banking, Housing, and Urban Affairs"],
    "jon tester":               ["Banking, Housing, and Urban Affairs"],
    "mark warner":              ["Banking, Housing, and Urban Affairs", "Intelligence"],
    "kyrsten sinema":           ["Banking, Housing, and Urban Affairs"],
    # House Energy & Commerce
    "brett guthrie":            ["Energy and Commerce"],
    "frank pallone":            ["Energy and Commerce"],
    "cathy mcmorris rodgers":   ["Energy and Commerce"],
    "bob latta":                ["Energy and Commerce"],
    "anna eshoo":               ["Energy and Commerce"],
    "diana degette":            ["Energy and Commerce"],
    # Senate Energy & Natural Resources
    "john barrasso":            ["Energy and Natural Resources"],
    "joe manchin":              ["Energy and Natural Resources"],
    "lisa murkowski":           ["Energy and Natural Resources", "Appropriations"],
    "maria cantwell":           ["Energy and Natural Resources", "Commerce, Science, and Transportation"],
    "martin heinrich":          ["Energy and Natural Resources", "Intelligence"],
    # Senate HELP
    "bill cassidy":             ["Health, Education, Labor, and Pensions"],
    "bernie sanders":           ["Health, Education, Labor, and Pensions"],
    "patty murray":             ["Health, Education, Labor, and Pensions"],
    "chris murphy":             ["Health, Education, Labor, and Pensions"],
    "maggie hassan":            ["Health, Education, Labor, and Pensions"],
    # House Ways & Means
    "jason smith":              ["Ways and Means"],
    "richard neal":             ["Ways and Means"],
    "kevin brady":              ["Ways and Means"],
    "ron kind":                 ["Ways and Means"],
    "mike thompson":            ["Ways and Means"],
    # Appropriations (broad defense + health spending)
    "rosa delauro":             ["Appropriations"],
    "kay granger":              ["Appropriations"],
    "shelley moore capito":     ["Appropriations"],
    "harold rogers":            ["Appropriations"],
    "tom cole":                 ["Appropriations"],
    # Science / Tech
    "frank lucas":              ["Science, Space, and Technology"],
    "zoe lofgren":              ["Science, Space, and Technology"],
    "ted cruz":                 ["Commerce, Science, and Transportation"],
    "amy klobuchar":            ["Commerce, Science, and Transportation"],
    "john thune":               ["Commerce, Science, and Transportation"],
    # Judiciary (tech antitrust)
    "jim jordan":               ["Judiciary"],
    "jerry nadler":             ["Judiciary"],
    "david cicilline":          ["Judiciary"],
    "ken buck":                 ["Judiciary"],
    # Agriculture
    "gt thompson":              ["Agriculture"],
    "david scott":              ["Agriculture"],
    "mike conaway":             ["Agriculture"],
    "debbie stabenow":          ["Agriculture"],
    "john boozman":             ["Agriculture"],
}


# ── ProPublica live integration ───────────────────────────────────────────────

_propublica_id_map:    dict[str, str]        = {}  # normalized_name → member_id
_propublica_id_loaded: bool                  = False
_committee_cache:      dict[str, list[str]]  = {}  # normalized_name → [committees]


def _normalize(name: str) -> str:
    """Lowercase, strip titles and extra whitespace."""
    name = name.lower().strip()
    for prefix in ("rep.", "sen.", "representative", "senator", "mr.", "mrs.", "dr."):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
    return name


def _load_propublica_id_map() -> None:
    """Fetch all current member name→ID mappings from ProPublica (once per session)."""
    global _propublica_id_loaded
    if _propublica_id_loaded or not PROPUBLICA_API_KEY:
        return
    _propublica_id_loaded = True

    headers = {"X-API-Key": PROPUBLICA_API_KEY}
    for chamber in ("house", "senate"):
        try:
            resp = requests.get(
                f"{PROPUBLICA_BASE}/{CONGRESS_NUM}/{chamber}/members.json",
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            members = resp.json()["results"][0]["members"]
            for m in members:
                full = f"{m.get('first_name', '')} {m.get('last_name', '')}".strip()
                _propublica_id_map[_normalize(full)] = m["id"]
            print(f"[committees] Loaded {len(members)} {chamber} member IDs from ProPublica")
        except Exception as e:
            print(f"[committees] ProPublica {chamber} member load failed: {e}")


def _fetch_from_propublica(member_name: str) -> list[str]:
    """Fetch a specific member's current committee assignments via ProPublica."""
    if not PROPUBLICA_API_KEY:
        return []

    _load_propublica_id_map()
    normalized  = _normalize(member_name)
    member_id   = _propublica_id_map.get(normalized)

    if not member_id:
        # Try last-name-only match against the ID map
        last = normalized.split()[-1] if normalized else ""
        matches = [(k, v) for k, v in _propublica_id_map.items() if k.split()[-1] == last]
        if len(matches) == 1:
            member_id = matches[0][1]

    if not member_id:
        return []

    try:
        resp = requests.get(
            f"{PROPUBLICA_BASE}/members/{member_id}.json",
            headers={"X-API-Key": PROPUBLICA_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        roles = resp.json()["results"][0].get("roles", [])
        committees: list[str] = []
        for role in roles:
            if str(role.get("congress", "")) == str(CONGRESS_NUM):
                for c in role.get("committees", []):
                    name = c.get("name", "").replace(" Committee", "").strip()
                    if name:
                        committees.append(name)
                for c in role.get("subcommittees", []):
                    name = c.get("name", "").strip()
                    if name:
                        committees.append(name)
        return committees
    except Exception as e:
        print(f"[committees] ProPublica detail fetch failed for {member_name}: {e}")
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def get_member_committees(member_name: str) -> list[str]:
    """
    Returns committee names for a congressional member.
    Uses ProPublica API if key is set; falls back to static 119th Congress map.
    Results are cached for the session lifetime.
    """
    normalized = _normalize(member_name)

    if normalized in _committee_cache:
        return _committee_cache[normalized]

    # ProPublica live lookup
    if PROPUBLICA_API_KEY:
        committees = _fetch_from_propublica(member_name)
        if committees:
            _committee_cache[normalized] = committees
            return committees

    # Static fallback — exact match first
    if normalized in STATIC_MEMBER_COMMITTEES:
        result = STATIC_MEMBER_COMMITTEES[normalized]
        _committee_cache[normalized] = result
        return result

    # Partial last-name match (only if unambiguous)
    last = normalized.split()[-1] if normalized else ""
    matches = [v for k, v in STATIC_MEMBER_COMMITTEES.items() if k.split()[-1] == last]
    if len(matches) == 1:
        _committee_cache[normalized] = matches[0]
        return matches[0]

    _committee_cache[normalized] = []
    return []


def get_committee_tag(member_name: str) -> str:
    """
    Returns a bracketed committee tag for inclusion in trade summaries.
    E.g., "[CMTE: Armed Services, Intelligence]"
    Returns empty string if no committee data found.
    """
    committees = get_member_committees(member_name)
    if not committees:
        return ""
    # Shorten to most relevant (first 2)
    short = committees[:2]
    return f"[CMTE: {', '.join(short)}]"


def has_oversight_relevance(member_name: str, sector: str) -> bool:
    """
    Returns True if the member sits on a committee that directly oversees
    the given GICS sector. Used to flag highest-conviction trades.
    """
    committees = get_member_committees(member_name)
    sector_lower = sector.lower()
    for committee in committees:
        # Match committee name to COMMITTEE_TO_SECTORS keys (partial match)
        for key, sectors in COMMITTEE_TO_SECTORS.items():
            if key.lower() in committee.lower() or committee.lower() in key.lower():
                for s in sectors:
                    if s.lower() in sector_lower or sector_lower in s.lower():
                        return True
    return False
