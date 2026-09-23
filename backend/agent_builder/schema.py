#
# Agent schema — the declarative contract the Phase 2 Copilot reads and writes.
#
# Design rule: stay as close to Pipecat Flows' own vocabulary as possible. A node
# carries Pipecat's native fields (`role_message`, `task_messages`, `pre/post_actions`)
# verbatim. The ONLY thing we add is `edges`: transitions expressed as DATA (a string
# `target`) rather than as Python closures — because a Copilot can emit a string, not
# a callable. `AgentBuilder` turns these strings back into the closures Pipecat wants.
#
# `type`, `data_refs` and `catalog_call` are Phase 1 additions with Phase 2 in mind:
# `type` is a pure UI/authoring hint (AgentBuilder ignores it — the graph still compiles
# from `edges`/`end` alone), `data_refs` is an unused-for-now slot for structured catalog
# references, and `catalog_call` lets a `tool_call` node call a real backend.catalog
# function, merging its result into the conversation state.
#

from dataclasses import dataclass, field
from typing import Optional

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs "Rachel"
DEFAULT_MODEL = "gpt-4o"

# UI-facing node kinds. Purely descriptive: AgentBuilder compiles every node the
# same way regardless of `type`, so adding a kind here never requires a builder change.
NODE_TYPES = ("message", "collect", "decision", "tool_call", "end")


def _infer_node_type(d: dict) -> str:
    """Best-effort `type` for nodes saved before this field existed (e.g. example_flow.json)."""
    if d.get("end"):
        return "end"
    edges = d.get("edges", [])
    if len(edges) > 1:
        return "decision"
    if len(edges) == 1 and (edges[0].get("properties") or edges[0].get("required")):
        return "collect"
    return "message"


@dataclass
class Edge:
    """A transition out of a node, exposed to the LLM as a callable tool."""

    function: str            # tool name the LLM calls to take this edge
    description: str         # when the model should call it
    target: str              # node to transition to (by name)
    # Fields to collect on this edge, as JSON-schema properties.
    properties: dict = field(default_factory=dict)
    required: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Edge":
        return cls(
            function=d["function"],
            description=d["description"],
            target=d["target"],
            properties=d.get("properties", {}),
            required=d.get("required", []),
        )


@dataclass
class Node:
    """A single conversational state. Fields mirror Pipecat Flows' NodeConfig."""

    name: str
    task_messages: list = field(default_factory=list)   # this node's objectives
    role_message: Optional[str] = None                  # overrides the global persona
    edges: list = field(default_factory=list)           # list[Edge]; transitions out
    pre_actions: list = field(default_factory=list)
    post_actions: list = field(default_factory=list)
    end: bool = False                                   # terminal -> ends the call
    type: str = "message"                                # UI hint only; see NODE_TYPES
    data_refs: dict = field(default_factory=dict)        # reserved for Phase 2 catalog refs
    # Real Phase 2 lookup: {"function": "<catalog.py function name>", "args": {<function
    # param name>: <state key to read the value from>}}. When set on a tool_call node,
    # AgentBuilder calls the real function and merges its result into the state.
    catalog_call: dict = field(default_factory=dict)
    position: dict = field(default_factory=dict)         # {"x", "y"} canvas layout, UI only

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            name=d["name"],
            task_messages=d.get("task_messages", []),
            role_message=d.get("role_message"),
            edges=[Edge.from_dict(e) for e in d.get("edges", [])],
            pre_actions=d.get("pre_actions", []),
            post_actions=d.get("post_actions", []),
            end=d.get("end", False),
            type=d.get("type") or _infer_node_type(d),
            data_refs=d.get("data_refs", {}),
            catalog_call=d.get("catalog_call", {}),
            position=d.get("position", {}),
        )


@dataclass
class AgentConfig:
    """A complete agent: identity + the conversation graph."""

    name: str
    initial_node: str
    nodes: list                          # list[Node]
    persona: str = ""                    # global role_message, applied to every node
    voice_id: str = DEFAULT_VOICE_ID
    model: str = DEFAULT_MODEL

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig":
        return cls(
            name=d["name"],
            initial_node=d["initial_node"],
            nodes=[Node.from_dict(n) for n in d["nodes"]],
            persona=d.get("persona", ""),
            voice_id=d.get("voice_id", DEFAULT_VOICE_ID),
            model=d.get("model", DEFAULT_MODEL),
        )
