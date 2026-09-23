#
# Agent persistence — the simplest thing that works for Phase 1: one JSON file per
# agent, named by id, under data/agents/. Good enough for a single internal user;
# swapping this for a real DB later is a one-file change (server.py only calls
# these four functions).
#

import json
import uuid
from pathlib import Path

from agent_builder import AgentConfig
from agent_builder.schema import DEFAULT_MODEL, DEFAULT_VOICE_ID

AGENTS_DIR = Path(__file__).parent / "data" / "agents"

TEMPLATES = {
    "blank": {
        "name": "Untitled Agent",
        "persona": (
            "You are a helpful voice assistant. Your responses are spoken aloud, so keep "
            "them short and avoid anything that can't be read out."
        ),
        "voice_id": DEFAULT_VOICE_ID,
        "model": DEFAULT_MODEL,
        "initial_node": "greeting",
        "nodes": [
            {
                "name": "greeting",
                "type": "message",
                "position": {"x": 80, "y": 120},
                "task_messages": [
                    {"role": "developer", "content": "Greet the caller and explain how you can help."}
                ],
                "edges": [
                    {
                        "function": "finish",
                        "description": "Call this once you're done helping the caller.",
                        "target": "end",
                        "properties": {},
                        "required": [],
                    }
                ],
            },
            {
                "name": "end",
                "type": "end",
                "end": True,
                "position": {"x": 420, "y": 120},
                "task_messages": [{"role": "developer", "content": "Thank the caller and say goodbye."}],
            },
        ],
    },
    "clinic_scheduler": {
        "name": "Prosper Scheduler",
        "voice_id": DEFAULT_VOICE_ID,
        "model": DEFAULT_MODEL,
        "persona": (
            "You are a warm, efficient scheduling assistant for a healthcare clinic. Your "
            "responses are spoken aloud, so avoid emojis, lists, or anything that can't be "
            "read out. Keep replies to one or two short sentences. Always use an available "
            "function to move the conversation forward."
        ),
        "initial_node": "greeting",
        "nodes": [
            {
                "name": "greeting",
                "type": "collect",
                "position": {"x": 60, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": "Greet the caller, say you're the clinic's scheduling assistant, and ask whether they'd like to book, reschedule, or cancel an appointment.",
                    }
                ],
                "edges": [
                    {
                        "function": "choose_intent",
                        "description": "Record what the caller wants to do once they say it.",
                        "target": "collect_details",
                        "properties": {
                            "intent": {
                                "type": "string",
                                "enum": ["book", "reschedule", "cancel"],
                                "description": "What the caller wants to do.",
                            }
                        },
                        "required": ["intent"],
                    }
                ],
            },
            {
                "name": "collect_details",
                "type": "collect",
                "position": {"x": 380, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": "Collect the caller's full name and the reason for the visit. Ask for whatever is still missing, one question at a time.",
                    }
                ],
                "edges": [
                    {
                        "function": "record_details",
                        "description": "Record the caller's name and reason once both are known.",
                        "target": "check_availability",
                        "properties": {
                            "full_name": {"type": "string", "description": "Caller's full name."},
                            "reason": {"type": "string", "description": "Reason for the visit."},
                        },
                        "required": ["full_name", "reason"],
                    }
                ],
            },
            {
                "name": "check_availability",
                "type": "message",
                "position": {"x": 700, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "Let the caller know you're checking what's open, then tell them there "
                            "are two slots available: Tuesday 10 AM and Thursday 2 PM."
                        ),
                    }
                ],
                "data_refs": {},
                "edges": [
                    {
                        "function": "lookup_availability",
                        "description": "Look up open appointment slots once the caller's details are recorded.",
                        "target": "offer_times",
                        "properties": {},
                        "required": [],
                    }
                ],
            },
            {
                "name": "offer_times",
                "type": "collect",
                "position": {"x": 1020, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": "Offer exactly the slots you looked up. Ask which one works for them.",
                    }
                ],
                "edges": [
                    {
                        "function": "select_time",
                        "description": "Record the slot the caller picks.",
                        "target": "confirm",
                        "properties": {
                            "slot": {"type": "string", "description": "The chosen appointment slot."}
                        },
                        "required": ["slot"],
                    }
                ],
            },
            {
                "name": "confirm",
                "type": "end",
                "end": True,
                "position": {"x": 1340, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": "Confirm the appointment back to the caller, including their name and the chosen time, thank them, and say goodbye.",
                    }
                ],
            },
        ],
    },
    "phase2_demo": {
        "name": "Presentation Prosper",
        "voice_id": DEFAULT_VOICE_ID,
        "model": DEFAULT_MODEL,
        "persona": (
            "You are the clinic's scheduling assistant. Your responses are spoken aloud — keep "
            "them to two or three short sentences, no lists. Every lookup you do is a real query "
            "against the real clinic catalog, never a guess — always say what you actually found, "
            "including when nothing matched. Always use an available function to move the "
            "conversation forward."
        ),
        "initial_node": "greeting",
        "nodes": [
            {
                "name": "greeting",
                "type": "collect",
                "position": {"x": 60, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "Greet the caller as the clinic's scheduling assistant and ask who they'd "
                            "like to see, by name if they know one, and what the visit is for. Both can "
                            "come in the same answer."
                        ),
                    }
                ],
                "edges": [
                    {
                        "function": "search_by_name",
                        "description": "Call once the caller both names a specific provider and says what the visit is for.",
                        "target": "lookup_by_name",
                        "properties": {
                            "provider_name": {
                                "type": "string",
                                "description": "The provider's name exactly as the caller said it, e.g. 'Chen' or 'Dr. Smith'.",
                            },
                            "specialty": {
                                "type": "string",
                                "enum": [
                                    "Allergy/Immunology", "Cardiology", "Dental", "Dermatology", "ENT",
                                    "Endocrinology", "Family Medicine", "Gastroenterology", "Internal Medicine",
                                    "Lab", "Neurology", "OB/GYN", "Orthopedics", "Pediatrics", "Psychiatry",
                                    "Pulmonology", "Radiology",
                                ],
                                "description": "Classify what the visit is for into exactly one of these real clinic specialties — never invent one that isn't listed.",
                            },
                        },
                        "required": ["provider_name", "specialty"],
                    },
                    {
                        "function": "search_by_specialty",
                        "description": "Call once the caller says what the visit is for but has no specific provider in mind.",
                        "target": "lookup_specialty",
                        "properties": {
                            "specialty": {
                                "type": "string",
                                "enum": [
                                    "Allergy/Immunology", "Cardiology", "Dental", "Dermatology", "ENT",
                                    "Endocrinology", "Family Medicine", "Gastroenterology", "Internal Medicine",
                                    "Lab", "Neurology", "OB/GYN", "Orthopedics", "Pediatrics", "Psychiatry",
                                    "Pulmonology", "Radiology",
                                ],
                                "description": "Classify what the visit is for into exactly one of these real clinic specialties — never invent one that isn't listed.",
                            }
                        },
                        "required": ["specialty"],
                    },
                ],
            },
            {
                "name": "lookup_by_name",
                "type": "tool_call",
                "position": {"x": 380, "y": 160},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "You just ran a real search already filtered by both the name and the "
                            "specialty in one query — everything you see genuinely matches both, so "
                            "there's nothing else to cross-check. State how many matched and name them. "
                            "If exactly one, confirm that's who they mean. If more than one shares the "
                            "name, ask which. If nobody matched at all, say so honestly and ask if they'd "
                            "like to see who's available in this specialty instead, without a name filter."
                        ),
                    }
                ],
                "data_refs": {},
                "catalog_call": {
                    "function": "resolve_provider_name",
                    "args": {"text": "provider_name", "specialty": "specialty"},
                },
                "edges": [
                    {
                        "function": "choose_provider",
                        "description": "Call once a specific provider is chosen.",
                        "target": "lookup_appointment_type",
                        "properties": {
                            "provider_id": {
                                "type": "string",
                                "description": "The exact id of the chosen provider, copied from the real results already read out.",
                            }
                        },
                        "required": ["provider_id"],
                    },
                    {
                        "function": "browse_specialty_instead",
                        "description": "Call if nobody matched the name and the caller wants to see who's available in this specialty instead.",
                        "target": "lookup_specialty",
                        "properties": {},
                        "required": [],
                    },
                ],
            },
            {
                "name": "lookup_specialty",
                "type": "tool_call",
                "position": {"x": 700, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "You're browsing every real provider in this specialty (and location too, if "
                            "one was given) — the caller has no specific name in mind, or the name they "
                            "gave didn't match anyone here. Read out who's available and ask them to "
                            "pick. If the result has an 'error' field saying there were too many to list, "
                            "say so plainly and ask which location the caller prefers, so the search can "
                            "narrow by it."
                        ),
                    }
                ],
                "data_refs": {},
                "catalog_call": {
                    "function": "find_providers",
                    "args": {"specialty": "specialty", "location_id": "location_id"},
                },
                "edges": [
                    {
                        "function": "choose_provider",
                        "description": "Call once a specific provider is chosen.",
                        "target": "lookup_appointment_type",
                        "properties": {
                            "provider_id": {
                                "type": "string",
                                "description": "The exact id of the chosen provider, copied from the real results already read out (e.g. prov_000).",
                            }
                        },
                        "required": ["provider_id"],
                    },
                    {
                        "function": "narrow_by_location",
                        "description": "Call if the search returned an error saying there were too many results, once the caller names a location to narrow by.",
                        "target": "narrow_specialty_location",
                        "properties": {
                            "location_text": {
                                "type": "string",
                                "description": "The location the caller named, in their own words.",
                            }
                        },
                        "required": ["location_text"],
                    },
                ],
            },
            {
                "name": "narrow_specialty_location",
                "type": "tool_call",
                "position": {"x": 700, "y": 420},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "Report the real location match by name and address. Then say you're "
                            "searching again narrowed to that location."
                        ),
                    }
                ],
                "data_refs": {},
                "catalog_call": {"function": "resolve_location", "args": {"text": "location_text"}},
                "edges": [
                    {
                        "function": "retry_specialty_search",
                        "description": "Call once a location match is confirmed, to search again narrowed by it.",
                        "target": "lookup_specialty",
                        "properties": {
                            "location_id": {
                                "type": "string",
                                "description": "The id of the matched location, copied from the real result just read out.",
                            }
                        },
                        "required": ["location_id"],
                    }
                ],
            },
            {
                "name": "lookup_appointment_type",
                "type": "tool_call",
                "position": {"x": 1020, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "Report the real appointment types available for this specialty — name and "
                            "duration in minutes. Ask the caller to pick one, or pick the most obviously "
                            "fitting one yourself if they just want 'an appointment'."
                        ),
                    }
                ],
                "data_refs": {},
                "catalog_call": {
                    "function": "find_appointment_types",
                    "args": {"specialty": "specialty", "provider_id": "provider_id"},
                },
                "edges": [
                    {
                        "function": "choose_appointment_type",
                        "description": "Call once an appointment type is chosen.",
                        "target": "ask_location",
                        "properties": {
                            "appointment_type_id": {
                                "type": "string",
                                "description": "The id of the chosen appointment type, copied from the real results just read out.",
                            }
                        },
                        "required": ["appointment_type_id"],
                    }
                ],
            },
            {
                "name": "ask_location",
                "type": "collect",
                "position": {"x": 1340, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "Ask which location the caller prefers, and naturally work in whether "
                            "they're a new or existing patient, and whether they have a referral on "
                            "file — don't make it sound like a form."
                        ),
                    }
                ],
                "edges": [
                    {
                        "function": "search_location",
                        "description": "Call once the caller names a location and patient status.",
                        "target": "resolve_location_node",
                        "properties": {
                            "location_text": {
                                "type": "string",
                                "description": "The location the caller named, in their own words.",
                            },
                            "new_patient": {
                                "type": "boolean",
                                "description": "True if the caller is a new patient.",
                            },
                            "referral_on_file": {
                                "type": "boolean",
                                "description": "True if the caller says they have a referral on file. Default false if not mentioned.",
                            },
                        },
                        "required": ["location_text", "new_patient"],
                    }
                ],
            },
            {
                "name": "resolve_location_node",
                "type": "tool_call",
                "position": {"x": 1660, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "Report the real location match by name and address. If nothing matched "
                            "well, say so and ask them to repeat. Once you have a match, say you're "
                            "checking whether this exact booking is allowed."
                        ),
                    }
                ],
                "data_refs": {},
                "catalog_call": {"function": "resolve_location", "args": {"text": "location_text"}},
                "edges": [
                    {
                        "function": "check_policy",
                        "description": "Call once a location match is confirmed.",
                        "target": "verify",
                        "properties": {
                            "location_id": {
                                "type": "string",
                                "description": "The id of the matched location, copied from the real result just read out.",
                            }
                        },
                        "required": ["location_id"],
                    }
                ],
            },
            {
                "name": "verify",
                "type": "tool_call",
                "position": {"x": 1980, "y": 260},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "Report the real result plainly — this is a live policy check running in "
                            "code, not your own judgment call. If ok is true, move toward confirming the "
                            "booking. If ok is false, read out the exact violation reasons you got back, "
                            "say plainly that this specific combination isn't allowed, and ask if they'd "
                            "like to try a different location, or address whatever the violation named "
                            "(e.g. a referral or new-patient rule)."
                        ),
                    }
                ],
                "data_refs": {},
                "catalog_call": {
                    "function": "verify_booking",
                    "args": {
                        "provider_id": "provider_id",
                        "location_id": "location_id",
                        "appointment_type_id": "appointment_type_id",
                        "new_patient": "new_patient",
                        "referral_on_file": "referral_on_file",
                    },
                },
                "edges": [
                    {
                        "function": "book_confirmed",
                        "description": "Call once the check passed (ok: true) and the caller is ready to confirm.",
                        "target": "confirm",
                        "properties": {},
                        "required": [],
                    },
                    {
                        "function": "retry_location",
                        "description": "Call if the check failed (ok: false) and the caller wants to try a different location.",
                        "target": "ask_location",
                        "properties": {},
                        "required": [],
                    },
                ],
            },
            {
                "name": "confirm",
                "type": "end",
                "end": True,
                "position": {"x": 2300, "y": 180},
                "task_messages": [
                    {
                        "role": "developer",
                        "content": (
                            "Confirm the booking: provider name, appointment type, and location. Thank "
                            "the caller and say goodbye."
                        ),
                    }
                ],
            },
        ],
    },
}


def _path(agent_id: str) -> Path:
    return AGENTS_DIR / f"{agent_id}.json"


def list_agents() -> list[dict]:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    agents = []
    for path in AGENTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        agents.append(
            {"id": path.stem, "name": data.get("name", path.stem), "updated_at": path.stat().st_mtime}
        )
    agents.sort(key=lambda a: a["updated_at"], reverse=True)
    return agents


def load_raw(agent_id: str) -> dict:
    path = _path(agent_id)
    if not path.exists():
        raise KeyError(agent_id)
    return json.loads(path.read_text())


def load_config(agent_id: str) -> AgentConfig:
    return AgentConfig.from_dict(load_raw(agent_id))


def create(name: str = None, template: str = "blank") -> tuple[str, dict]:
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template '{template}'. Choose one of {list(TEMPLATES)}.")
    data = json.loads(json.dumps(TEMPLATES[template]))  # deep copy
    if name:
        data["name"] = name
    agent_id = uuid.uuid4().hex[:12]
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    _path(agent_id).write_text(json.dumps(data, indent=2))
    return agent_id, data


def save(agent_id: str, data: dict) -> None:
    """Validate `data` as an AgentConfig and persist it verbatim.

    Raises ValueError if the shape is fundamentally broken (missing required
    fields, dangling edges, no initial node, ...). Non-fatal issues (unreachable
    nodes, missing task_messages) are returned separately by the validate
    endpoint and never block a save.
    """
    try:
        config = AgentConfig.from_dict(data)
    except KeyError as e:
        raise ValueError(f"Missing required field: {e}") from e

    from agent_builder.validation import validate_config

    errors = [i for i in validate_config(config) if i.severity == "error"]
    if errors:
        raise ValueError("; ".join(f"{i.node}: {i.message}" if i.node else i.message for i in errors))

    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    _path(agent_id).write_text(json.dumps(data, indent=2))


def delete(agent_id: str) -> None:
    path = _path(agent_id)
    if path.exists():
        path.unlink()
