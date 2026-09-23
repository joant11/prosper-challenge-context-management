#
# Catalog store — Phase 2 context management.
#
# Loads backend/data/catalog.json into an in-memory SQLite database and exposes
# narrow, parametrized query functions over it. The LLM never sees this data as
# text; a tool_call node calls one of these functions and only ever gets back
# the short, exact result list it returns.
#
# Every column here is a field that exists in catalog.json (see
# backend/data/README.md for the source schema) — nothing here is invented data.
#

import difflib
import json
import sqlite3
from pathlib import Path
from typing import Optional

CATALOG_PATH = Path(__file__).parent / "data" / "catalog.json"

_SCHEMA = """
CREATE TABLE locations (
    id TEXT PRIMARY KEY,
    name TEXT,
    address TEXT,
    city TEXT,
    phone TEXT,
    hours TEXT
);
CREATE TABLE location_capabilities (
    location_id TEXT REFERENCES locations(id),
    capability TEXT
);
CREATE TABLE providers (
    id TEXT PRIMARY KEY,
    name TEXT,
    title TEXT,
    specialty TEXT,
    accepting_new_patients INTEGER
);
CREATE TABLE provider_languages (
    provider_id TEXT REFERENCES providers(id),
    language TEXT
);
CREATE TABLE provider_locations (
    provider_id TEXT REFERENCES providers(id),
    location_id TEXT REFERENCES locations(id)
);
CREATE TABLE provider_appointment_types (
    provider_id TEXT REFERENCES providers(id),
    appointment_type_id TEXT REFERENCES appointment_types(id)
);
CREATE TABLE appointment_types (
    id TEXT PRIMARY KEY,
    name TEXT,
    specialty TEXT,
    duration_min INTEGER,
    requires_referral INTEGER,
    new_patients_allowed INTEGER,
    required_capability TEXT
);
"""


def _load(conn: sqlite3.Connection, path: Path) -> None:
    data = json.loads(Path(path).read_text())
    conn.executescript(_SCHEMA)

    for l in data["locations"]:
        conn.execute(
            "INSERT INTO locations VALUES (?, ?, ?, ?, ?, ?)",
            (l["id"], l["name"], l["address"], l["city"], l["phone"], l["hours"]),
        )
        conn.executemany(
            "INSERT INTO location_capabilities VALUES (?, ?)",
            [(l["id"], c) for c in l["capabilities"]],
        )

    for p in data["providers"]:
        conn.execute(
            "INSERT INTO providers VALUES (?, ?, ?, ?, ?)",
            (p["id"], p["name"], p["title"], p["specialty"], int(p["accepting_new_patients"])),
        )
        conn.executemany(
            "INSERT INTO provider_languages VALUES (?, ?)",
            [(p["id"], lang) for lang in p["languages"]],
        )
        conn.executemany(
            "INSERT INTO provider_locations VALUES (?, ?)",
            [(p["id"], lid) for lid in p["location_ids"]],
        )
        conn.executemany(
            "INSERT INTO provider_appointment_types VALUES (?, ?)",
            [(p["id"], aid) for aid in p["appointment_type_ids"]],
        )

    for a in data["appointment_types"]:
        conn.execute(
            "INSERT INTO appointment_types VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                a["id"],
                a["name"],
                a["specialty"],
                a["duration_min"],
                int(a["requires_referral"]),
                int(a["new_patients_allowed"]),
                a.get("required_capability"),
            ),
        )
    conn.commit()


_conn: Optional[sqlite3.Connection] = None


def get_connection(path: Path = CATALOG_PATH) -> sqlite3.Connection:
    """Module-level singleton: the catalog is loaded once per process."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(":memory:")
        _conn.row_factory = sqlite3.Row
        _load(_conn, path)
    return _conn


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ---- structured search --------------------------------------------------

# A result this long is already a bad voice UX before it's a cost problem —
# nobody wants 40 provider names read out loud. Past this, find_providers /
# find_appointment_types refuse to guess which ones matter and instead say so,
# the same way resolve_provider_name returns candidates instead of a guess
# when a name is ambiguous. This is what makes their cost provably bounded
# regardless of catalog size, the same way the `limit` on the fuzzy matchers
# does — see verify_context_savings.py.
MAX_RESULTS = 8


class TooManyResultsError(Exception):
    """Raised instead of returning a result set too long to be spoken or to
    stay cheap — the caller should narrow the query, not read a huge list."""

    def __init__(self, count: int, narrow_by: str):
        self.count = count
        self.narrow_by = narrow_by
        super().__init__(
            f"{count} results — too many to list. Narrow further, e.g. by {narrow_by}."
        )


def find_providers(
    specialty: Optional[str] = None,
    location_id: Optional[str] = None,
    provider_id: Optional[str] = None,
    new_patient: Optional[bool] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Providers matching all given filters. Every filter is optional; an empty
    call returns every provider, so callers should only fire this once enough
    fields are known to make the result set meaningfully short. Raises
    TooManyResultsError rather than silently returning a long, unranked list —
    there's no ordering here that could tell you which of e.g. 40 cardiologists
    to keep and which to drop."""
    conn = conn or get_connection()
    sql = "SELECT DISTINCT providers.* FROM providers"
    where, params = [], []
    if location_id:
        sql += " JOIN provider_locations pl ON pl.provider_id = providers.id"
        where.append("pl.location_id = ?")
        params.append(location_id)
    if specialty:
        where.append("providers.specialty = ?")
        params.append(specialty)
    if provider_id:
        where.append("providers.id = ?")
        params.append(provider_id)
    if new_patient:
        where.append("providers.accepting_new_patients = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = _rows(conn.execute(sql, params))
    if len(rows) > MAX_RESULTS:
        missing = [
            f for f, given in [("a location", location_id), ("new-patient status", new_patient)] if not given
        ]
        raise TooManyResultsError(len(rows), " or ".join(missing) or "the provider's name")
    return rows


def find_appointment_types(
    specialty: Optional[str] = None,
    new_patient: Optional[bool] = None,
    provider_id: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Appointment types matching all given filters. Raises TooManyResultsError
    rather than silently returning a long, unranked list — see find_providers."""
    conn = conn or get_connection()
    sql = "SELECT DISTINCT appointment_types.* FROM appointment_types"
    where, params = [], []
    if provider_id:
        sql += (
            " JOIN provider_appointment_types pat"
            " ON pat.appointment_type_id = appointment_types.id"
        )
        where.append("pat.provider_id = ?")
        params.append(provider_id)
    if specialty:
        where.append("appointment_types.specialty = ?")
        params.append(specialty)
    if new_patient:
        where.append("appointment_types.new_patients_allowed = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = _rows(conn.execute(sql, params))
    if len(rows) > MAX_RESULTS:
        missing = [
            f for f, given in [("the provider", provider_id), ("new-patient status", new_patient)] if not given
        ]
        raise TooManyResultsError(len(rows), " or ".join(missing) or "specialty")
    return rows


def list_specialties(conn: Optional[sqlite3.Connection] = None) -> list[str]:
    """The enum of specialties that actually exist in the catalog — for the
    complaint -> specialty classifier to constrain its output to, instead of
    letting the model invent a specialty that isn't bookable."""
    conn = conn or get_connection()
    rows = conn.execute("SELECT DISTINCT specialty FROM providers ORDER BY specialty")
    return [r["specialty"] for r in rows]


# ---- fuzzy resolution -----------------------------------------------------


def resolve_location(
    text: str, limit: int = 3, cutoff: float = 0.3, conn: Optional[sqlite3.Connection] = None
) -> list[dict]:
    """Fuzzy-match caller speech against locations.name/city/address — no
    alias table, since catalog.json defines none; this only ever matches
    against fields that are actually in the data."""
    conn = conn or get_connection()
    text_l = text.strip().lower()
    scored = []
    for row in _rows(conn.execute("SELECT * FROM locations")):
        fields = [row["name"], row["city"], row["address"]]
        score = max(difflib.SequenceMatcher(None, text_l, f.lower()).ratio() for f in fields)
        if any(text_l in f.lower() for f in fields):
            score = max(score, 0.85)
        scored.append((score, row))
    scored.sort(key=lambda pair: -pair[0])
    return [row for score, row in scored[:limit] if score >= cutoff]


def resolve_provider_name(
    text: str,
    specialty: Optional[str] = None,
    location_id: Optional[str] = None,
    limit: int = 3,
    cutoff: float = 0.3,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Fuzzy-match a spoken provider name against providers.name (handles STT
    misspellings and partial names). When `specialty`/`location_id` are given,
    filters to those exactly *before* scoring — combining a known name with a
    known specialty in one call is strictly better than two separate calls
    cross-referenced afterwards: it can never surface a match one of the two
    calls alone would have dropped from its `limit`. Still doesn't disambiguate
    by itself when a name is shared within the same filtered set — that's the
    caller's job."""
    conn = conn or get_connection()
    text_l = text.strip().lower()
    sql = "SELECT DISTINCT providers.* FROM providers"
    where, params = [], []
    if location_id:
        sql += " JOIN provider_locations pl ON pl.provider_id = providers.id"
        where.append("pl.location_id = ?")
        params.append(location_id)
    if specialty:
        where.append("providers.specialty = ?")
        params.append(specialty)
    if where:
        sql += " WHERE " + " AND ".join(where)
    scored = []
    for row in _rows(conn.execute(sql, params)):
        name_l = row["name"].lower()
        score = difflib.SequenceMatcher(None, text_l, name_l).ratio()
        if text_l in name_l:
            score = max(score, 0.85)
        scored.append((score, row))
    scored.sort(key=lambda pair: -pair[0])
    return [row for score, row in scored[:limit] if score >= cutoff]


# ---- policy verification ---------------------------------------------------


def verify_booking(
    provider_id: str,
    location_id: str,
    appointment_type_id: str,
    new_patient: bool = False,
    referral_on_file: bool = False,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """The final hard gate before confirming a booking. Every check here is a
    direct comparison against catalog.json fields, except `referral_on_file`
    and `new_patient`, which are caller-asserted (there is no patient record
    in this dataset) rather than independently verified."""
    conn = conn or get_connection()
    violations = []

    provider = conn.execute(
        "SELECT * FROM providers WHERE id = ?", (provider_id,)
    ).fetchone()
    appt = conn.execute(
        "SELECT * FROM appointment_types WHERE id = ?", (appointment_type_id,)
    ).fetchone()
    location = conn.execute(
        "SELECT * FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    if not provider or not appt or not location:
        return {"ok": False, "violations": ["unknown provider, location, or appointment type"]}

    provider_at_location = conn.execute(
        "SELECT 1 FROM provider_locations WHERE provider_id = ? AND location_id = ?",
        (provider_id, location_id),
    ).fetchone()
    if not provider_at_location:
        violations.append("provider does not practice at the chosen location")

    provider_offers_type = conn.execute(
        "SELECT 1 FROM provider_appointment_types WHERE provider_id = ? AND appointment_type_id = ?",
        (provider_id, appointment_type_id),
    ).fetchone()
    if not provider_offers_type:
        violations.append("provider does not offer this appointment type")

    if appt["required_capability"]:
        location_has_capability = conn.execute(
            "SELECT 1 FROM location_capabilities WHERE location_id = ? AND capability = ?",
            (location_id, appt["required_capability"]),
        ).fetchone()
        if not location_has_capability:
            violations.append(
                f"location lacks required capability: {appt['required_capability']}"
            )

    if appt["requires_referral"] and not referral_on_file:
        violations.append("appointment type requires a referral on file")

    if new_patient and not appt["new_patients_allowed"]:
        violations.append("appointment type is not available to new patients")

    if new_patient and not provider["accepting_new_patients"]:
        violations.append("provider is not accepting new patients")

    return {"ok": not violations, "violations": violations}
