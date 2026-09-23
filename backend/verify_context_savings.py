#
# Verifies the core Phase 2 claim from solution.md: per-turn context cost stays
# roughly flat as the catalog grows, because the LLM only ever sees a query
# result, never the catalog itself. This is the check solution.md promises
# ("run the same conversation against catalog.json and a synthetic 10x-larger
# version, compare token counts") — this script is that comparison.
#
# Compares two things, against both the real catalog.json and a synthetic N x
# larger one (built by duplicating every location/provider/appointment-type N
# times, each copy internally consistent):
#
#   (a) "naive" approach   — the full catalog dumped as text once, as a naive
#                             design would put in the system prompt
#   (b) this design         — the actual JSON returned by each real catalog.py
#                             function call used in the phase2_demo graph
#
# Usage: backend/.venv/bin/python backend/verify_context_savings.py [multiplier]
#

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import catalog  # noqa: E402

try:
    import tiktoken

    _enc = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_enc.encode(text))

    TOKENIZER = "tiktoken cl100k_base (exact)"
except ImportError:
    def count_tokens(text: str) -> int:
        return len(text) // 4  # rough fallback: ~4 chars/token in English/JSON

    TOKENIZER = "chars/4 (approximate — pip install tiktoken for exact counts)"


def synthesize(data: dict, multiplier: int) -> dict:
    """N independent, internally-consistent copies of the catalog."""
    if multiplier == 1:
        return data
    locations, providers, appts = [], [], []
    for i in range(multiplier):
        for l in data["locations"]:
            l2 = dict(l)
            l2["id"] = f"{l['id']}_c{i}"
            locations.append(l2)
        for a in data["appointment_types"]:
            a2 = dict(a)
            a2["id"] = f"{a['id']}_c{i}"
            appts.append(a2)
        for p in data["providers"]:
            p2 = dict(p)
            p2["id"] = f"{p['id']}_c{i}"
            p2["location_ids"] = [f"{lid}_c{i}" for lid in p["location_ids"]]
            p2["appointment_type_ids"] = [f"{aid}_c{i}" for aid in p["appointment_type_ids"]]
            providers.append(p2)
    return {
        "policies": data.get("policies", []),
        "locations": locations,
        "providers": providers,
        "appointment_types": appts,
    }


def load_conn(data: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        tmp_path = f.name
    catalog._load(conn, Path(tmp_path))
    Path(tmp_path).unlink()
    return conn


def report(label: str, data: dict, conn: sqlite3.Connection) -> None:
    n_loc = len(data["locations"])
    n_prov = len(data["providers"])
    n_appt = len(data["appointment_types"])

    naive_text = json.dumps(data)
    naive_tokens = count_tokens(naive_text)

    calls = [
        ("resolve_provider_name(\"Smith\")", lambda: catalog.resolve_provider_name("Smith", conn=conn)),
        ("find_providers(specialty=\"Dermatology\")", lambda: catalog.find_providers(specialty="Dermatology", conn=conn)),
        ("find_appointment_types(specialty=\"Dermatology\")", lambda: catalog.find_appointment_types(specialty="Dermatology", conn=conn)),
        ("resolve_location(\"downtown\")", lambda: catalog.resolve_location("downtown", conn=conn)),
    ]

    print(f"\n=== {label} — {n_loc} locations, {n_prov} providers, {n_appt} appointment types ===")
    print(f"Naive full-catalog dump, once: {naive_tokens:,} tokens")
    print(f"{'call':45s} {'rows back':>10s} {'tokens':>10s}")
    total_call_tokens = 0
    for name, fn in calls:
        try:
            result = fn()
            text = json.dumps(result)
            rows = str(len(result))
        except catalog.TooManyResultsError as e:
            text = str(e)
            rows = f"refused ({e.count})"
        tokens = count_tokens(text)
        total_call_tokens += tokens
        print(f"{name:45s} {rows:>10s} {tokens:>10d}")
    print(f"{'(sum of all 4 calls, one full conversation)':45s} {'':>10s} {total_call_tokens:>10d}")


def main():
    multiplier = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    real_data = json.loads(catalog.CATALOG_PATH.read_text())
    real_conn = load_conn(real_data)
    report("Real catalog.json (1x)", real_data, real_conn)

    synthetic_data = synthesize(real_data, multiplier)
    synthetic_conn = load_conn(synthetic_data)
    report(f"Synthetic catalog ({multiplier}x)", synthetic_data, synthetic_conn)

    print(f"\nTokenizer: {TOKENIZER}")


if __name__ == "__main__":
    main()
