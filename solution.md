# Solution overview — Context Management for Scheduling

## The problem

A voice scheduling agent needs to navigate a clinic catalog (locations, providers,
appointment types) that's too large and too constraint-heavy to hand the LLM as text.
Doing so is expensive (token cost scales with catalog size on every turn) and, more
importantly, inaccurate: the LLM has to pick one right answer out of a long,
undifferentiated list, and a wrong-but-confident pick (wrong provider, wrong location,
a booking that violates a referral or new-patient rule) is a worse failure than not
knowing.

## The approach

**The LLM never sees the catalog.** It's loaded once into a real relational store
(SQLite, in `backend/catalog.py`) with explicit foreign keys between providers,
locations, and appointment types, mirroring the constraints that already exist in
`catalog.json` (a provider practices at specific locations, offers specific
appointment types; an appointment type may require a specific location capability, a
referral, or new-patient eligibility). The agent talks to this store only through a
small set of narrow, parametrized functions — `find_providers(...)`,
`find_appointment_types(...)`, `resolve_location(...)`, `resolve_provider_name(...)`,
`verify_booking(...)` — called from `tool_call` nodes in the Phase 1 conversation
graph. Every call returns a handful of exact rows; nothing ever puts the full catalog,
or even a long slice of it, into the model's context.

This treats the catalog as what it structurally is — small, relational data with hard
policy constraints — rather than as a body of text to search semantically. It sits
directly on top of the Phase 1 node-graph builder with no changes to that schema's
node/edge shape: `tool_call` nodes originally had a slot (`mock_response`) reserved for
a stand-in lookup; Phase 2 replaced it with `catalog_call`, which makes a node call the
real function instead (`mock_response` has since been removed entirely — every
`tool_call` node now either makes a real call or doesn't exist as a `tool_call`).

## Key architectural decisions

| Decision | Why |
| --- | --- |
| Relational store (SQLite) + parametrized query functions, not embeddings/RAG | The catalog is small (~140 rows total) and dominated by hard constraints (provider↔location↔appointment-type eligibility, referral rules, capability gating), not similarity judgments. Semantic search would blur distinctions that must stay exact (two different "Dr. Chen"s at different locations are not interchangeable), and RAG solves a scale problem this dataset doesn't have. |
| Enum-based classification for open language (complaint → specialty), constrained to `list_specialties()` | A small fixed category set — the specialties that actually exist in the catalog — is something an LLM classifies reliably via function calling; constraining the enum to real data prevents it from inventing a specialty that isn't bookable. No embeddings needed for this either. |
| Plain string-similarity fuzzy matching (stdlib `difflib`) for provider/location names, not semantic search | Matching spoken/misspelled names against a small known list is a string-similarity problem, not a meaning-similarity one — cheaper, more predictable, and needs no new dependency. |
| Ambiguous or low-confidence matches return candidates, never a silent guess | A wrong-but-confident booking is a worse outcome than one extra clarifying question — this is the core accuracy argument the whole design rests on. `resolve_location`/`resolve_provider_name` return a ranked candidate list; the calling node, not the matcher, decides what to do with ambiguity. |
| Structured filter queries (`find_providers`, `find_appointment_types`) refuse to return an oversized result rather than truncate it silently | Unlike the fuzzy matchers, these have no relevance ranking — truncating to N rows could silently drop the correct one. Past `MAX_RESULTS` (8), the function raises `TooManyResultsError` with a concrete narrowing suggestion (e.g. location) instead of guessing which rows matter. This wasn't hypothetical: `find_providers(specialty="Internal Medicine")` already returns 10 rows on the real, present-day catalog. |
| Known filters are combined into one query up front, not applied as two separate calls reconciled afterwards | When a caller gives both a name and a specialty, `resolve_provider_name` accepts `specialty` (and `location_id`) as optional pre-filters, applied *before* fuzzy scoring — one query instead of two. Two independently-capped queries reconciled by an LLM afterwards can silently drop a valid answer that only survives in one of them; combining filters up front makes that failure mode structurally impossible instead of prompting the model to trust one source over another. |
| Policy checks (`verify_booking`) as explicit code predicates over catalog columns, not a rules engine and not an LLM judgment call | The policy set (referral required, location capability, new-patient eligibility, provider-location/appointment-type validity) is small, fixed, and already expressed as literal fields in `catalog.json` — a WHERE-clause-shaped check is more auditable and can't be talked out of by conversational pressure the way a prompt-based rule could. |
| `new_patient` and `referral_on_file` are explicit, honestly-named caller-asserted inputs | There's no patient record in this dataset, so these two fields can't be independently verified. Rather than pretend otherwise, they're modeled as ordinary collected fields (same as `location_id` or `specialty`) and still enforced deterministically in `verify_booking` — the guarantee is "the rule is checked in code against what the caller stated," not "verified against a system of record." |
| Node-graph-driven narrowing: ask before searching, retry by narrowing further rather than paging | Because the Phase 1 conversation is already a graph, depth in the graph doubles as depth in the catalog hierarchy — name and specialty are collected together, up front, before any query fires. When a search is still too broad (only reachable on the no-name browsing path), the graph asks for a genuinely different filter (location) and retries once already-narrowed, rather than fetching the next batch of the same broad list. |

## Data model

Tables loaded from `catalog.json` into SQLite (`backend/catalog.py`), column names
taken verbatim from the source data:

- `locations` (`id, name, address, city, phone, hours`) + `location_capabilities`
  (join table over `capabilities`).
- `providers` (`id, name, title, specialty, accepting_new_patients`) +
  `provider_locations`, `provider_appointment_types`, `provider_languages` (join
  tables over the array fields in the source JSON).
- `appointment_types` (`id, name, specialty, duration_min, requires_referral,
  new_patients_allowed, required_capability`).

Normalizing the array fields (`location_ids`, `appointment_type_ids`, `languages`,
`capabilities`) into real join tables is what makes them queryable with SQL joins
instead of requiring the caller to scan JSON arrays by hand.

## Query & normalization layer (implemented, `backend/catalog.py`)

- `find_providers(specialty?, location_id?, provider_id?, new_patient?)` /
  `find_appointment_types(specialty?, new_patient?, provider_id?)` — optional-filter
  SQL queries. Raise `TooManyResultsError` past `MAX_RESULTS` (8 rows) instead of
  returning an oversized, unranked list.
- `list_specialties()` — the real enum used to constrain the complaint→specialty
  classifier.
- `resolve_location(text)` / `resolve_provider_name(text, specialty?, location_id?)` —
  fuzzy matching against real columns, returning up to `limit` (default 3) ranked
  candidates. This cap is a hard, code-level guarantee (token cost is flat regardless
  of catalog size) — stronger than the structured filters above, which are only
  bounded as long as the query stays narrow. The optional exact filters on
  `resolve_provider_name` let a spoken name and a known specialty (or location)
  combine into a single call instead of two.
- `verify_booking(provider_id, location_id, appointment_type_id, new_patient,
  referral_on_file)` — the final gate: provider-location validity, provider-appointment-type
  validity, location capability gating, referral requirement, and both new-patient
  rules, each as an independent check.

All of the above have been run against the real `catalog.json` and verified to
produce correct results, including the intended failure cases (no orthopedists at a
given location, a referral-required appointment type rejected without one, a booking
rejected for the wrong location, an imaging-only appointment type rejected at a
location without that capability).

## Node graph wiring (implemented, `backend/agent_builder/builder.py`)

A node has a `catalog_call: {"function": "<name>", "args": {<param>: <state key>}}`
field. When a `tool_call` node has it, `AgentBuilder`'s edge handler calls the real
`catalog.py` function — pulling arguments from this turn's collected properties,
falling back to everything accumulated earlier in the conversation — and merges the
real result into the conversation state. No schema rework: same `tool_call` node kind
and edge-collection mechanism Phase 1 already had.

The reference implementation is the **"Presentation Prosper"** agent template
(`backend/agent_store.py`, key `phase2_demo`), a 9-node graph:

`greeting` (collects a provider name *and* a specialty together, in one turn) →
branches on whether a name was given → `lookup_by_name` (name + specialty combined in
one call) *or* `lookup_specialty` (specialty alone, with a `narrow_specialty_location`
retry loop if that's too broad) → `lookup_appointment_type` → `ask_location` →
`resolve_location_node` → `verify` (loops back to `ask_location` on a real policy
rejection) → `confirm`.

Every lookup is a real call, not a script — verified against arbitrary real provider
names, not just one worked example.

## Worked example (real data, run end to end)

This is the exact scenario that surfaced the accuracy bug described below, walked
through with real values from `catalog.json`.

**1. `greeting`.** Caller says: *"I'd like to see Dr. Chen, it's for a cardiology
issue."* Both `provider_name="Chen"` and `specialty="Cardiology"` are collected
together, in the same turn. The graph takes the `search_by_name` edge to
`lookup_by_name`.

**2. `lookup_by_name`.** Real call: `resolve_provider_name("Chen", specialty="Cardiology")`.
"Chen" alone matches 7 real providers across 6 specialties; filtering by specialty
*before* scoring narrows that to exactly the 2 who are cardiologists:

```
Dr. David Chen  — Cardiology
Dr. Emily Chen  — Cardiology
```

92 tokens, one call. The caller picks Dr. Emily Chen.

**3. `lookup_appointment_type`.** Real call:
`find_appointment_types(specialty="Cardiology", provider_id="prov_046")` → her real
appointment types, including Echocardiogram (45 min, requires a referral, requires the
`imaging` capability at the location).

**4. `ask_location`.** Caller says Richmond Care Center, confirms they're an existing
patient, and have a referral on file.

**5. `resolve_location_node`.** Real call: `resolve_location("Richmond Care Center")`
→ `loc_007`.

**6. `verify`.** Real call:
`verify_booking("prov_046", "loc_007", "appt_021", new_patient=False, referral_on_file=True)`
→ `{"ok": false, "violations": ["location lacks required capability: imaging"]}` —
Richmond Care Center genuinely doesn't have imaging. This is a real policy rejection,
not a scripted one. The graph loops back to `ask_location`.

**7. Retry.** Caller says Downtown Health Center instead. `resolve_location` →
`loc_004`. `verify_booking("prov_046", "loc_004", "appt_021", ...)` →
`{"ok": true, "violations": []}` — same provider, same appointment type, a location
that actually has imaging.

**8. `confirm`.** Booking confirmed: Dr. Emily Chen, Echocardiogram, Downtown Health
Center.

## Verifying the cost/accuracy claim

The design's central claim — token cost stays bounded as the catalog grows — is
checked directly, not just asserted: `backend/verify_context_savings.py` runs the same
sequence of real catalog calls against both the real `catalog.json` and a synthetic
10x-larger version (built by duplicating every location/provider/appointment-type N
times, each copy internally consistent), and compares real `tiktoken` counts against
what a naive full-catalog-as-text approach would cost.

| | Real catalog (1x) | Synthetic 10x |
| --- | --- | --- |
| Naive full-catalog dump | 10,877 tokens | 120,535 tokens |
| This design, one full conversation, followed through to a completed booking | ~700 tokens | 1,002 tokens |
| As % of naive | ~6% | 0.83% |

The 10x conversation was run to actual completion, not stopped at the first refusal:
`find_providers` genuinely needed and used its one narrowing retry (40 matches for
"Dermatology" alone → 2, after adding a location filter); `find_appointment_types`,
filtered by a specific provider rather than by specialty alone, never approached the
limit at all — duplicating the whole catalog doesn't give any single provider more
services, it just creates more providers. Re-run with
`backend/.venv/bin/python backend/verify_context_savings.py [multiplier]`.

## Explicitly out of scope

- Real calendar/availability integration — availability is mocked; the focus is
  catalog navigation, not scheduling-system plumbing.
- Embeddings/vector search for the core catalog matching — deliberately excluded (see
  decisions table above); the one narrow exception, if a specialty taxonomy ever grew
  too large for reliable enum classification, would be a small embedding classifier
  over specialty labels specifically, not the catalog.
- A general-purpose rules engine for policies — the known policy set is small and
  fixed enough that explicit predicates in `verify_booking` are more auditable than a
  DSL.
- Insurance data and an authored location/name alias table — not present in
  `catalog.json`; adding either would mean inventing data rather than navigating the
  given catalog, so both were cut from the original plan.
- Recovering from a *second* consecutive "too many results" on the no-name browsing
  path (e.g. a specialty still oversized even after narrowing by location) — the
  current graph asks once and retries once; a much larger catalog would need a second
  narrowing dimension (e.g. new-patient status) chained the same way. Not needed on
  the real catalog or the 10x synthetic one tested here, but not structurally
  guaranteed for an arbitrarily large one either.
