#
# AgentBuilder — loads a declarative agent (JSON / dict) and compiles its node
# graph into Pipecat Flows objects.
#
#   JSON  ->  AgentConfig (validated)  ->  Pipecat Flows NodeConfig graph
#
# This is the seam between "agent as data" (what the Phase 2 Copilot produces)
# and "agent as a running conversation" (what bot.py executes). Keeping the
# compile + validation here means bot.py never touches the graph internals.
#

import json
from pathlib import Path
from typing import Union

from loguru import logger
from pipecat_flows import FlowManager, FlowsFunctionSchema, NodeConfig

import catalog
from .schema import AgentConfig, Edge, Node
from .validation import validate_config

# The real Phase 2 query layer, exposed to `catalog_call` nodes by name. Every
# function here is read-only and already verified against the real catalog.json
# (see backend/catalog.py) — the LLM never sees the catalog itself, only what
# one of these calls returns.
CATALOG_FUNCTIONS = {
    "resolve_provider_name": catalog.resolve_provider_name,
    "resolve_location": catalog.resolve_location,
    "find_providers": catalog.find_providers,
    "find_appointment_types": catalog.find_appointment_types,
    "find_provider_locations": catalog.find_provider_locations,
    "list_specialties": catalog.list_specialties,
    "verify_booking": catalog.verify_booking,
}


def _run_catalog_call(catalog_call: dict, state: dict) -> dict:
    """Call a real catalog.py function for a `catalog_call` node.

    `args` maps the function's own parameter names to the state key that holds
    the value, looked up from everything accumulated in conversation state so
    far (including whatever the edge that just led here collected — the
    caller merges that into `state` before calling this).
    """
    fn_name = catalog_call.get("function")
    fn = CATALOG_FUNCTIONS.get(fn_name)
    if fn is None:
        return {"error": f"unknown catalog function '{fn_name}'"}

    kwargs = {}
    for param, state_key in catalog_call.get("args", {}).items():
        if state_key in state:
            kwargs[param] = state[state_key]

    try:
        output = fn(**kwargs)
    except Exception as e:  # missing/invalid args, unknown ids, ... — never crash the call
        logger.warning(f"catalog_call {fn_name}({kwargs}) failed: {e}")
        return {"error": str(e)}

    if isinstance(output, list):
        return {"candidates": output, "candidate_count": len(output)}
    if isinstance(output, dict):
        return output
    return {"result": output}


class AgentBuilder:
    """Builds a runnable Pipecat Flows graph from a declarative AgentConfig."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self._nodes_by_name = {n.name: n for n in config.nodes}
        self._validate()

    # ---- loading -----------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "AgentBuilder":
        return cls(AgentConfig.from_dict(data))

    @classmethod
    def from_json(cls, path: Union[str, Path]) -> "AgentBuilder":
        data = json.loads(Path(path).read_text())
        return cls.from_dict(data)

    # ---- validation --------------------------------------------------------
    def _validate(self) -> None:
        errors = [i for i in validate_config(self.config) if i.severity == "error"]
        if errors:
            raise ValueError(
                "; ".join(f"{i.node}: {i.message}" if i.node else i.message for i in errors)
            )

    # ---- compilation -------------------------------------------------------
    def build_initial_node(self) -> NodeConfig:
        """Return the entry NodeConfig; downstream nodes are built lazily on transition."""
        return self._make_node(self._nodes_by_name[self.config.initial_node], {})

    def _make_node(self, node: Node, state: dict) -> NodeConfig:
        # A tool_call node's real lookup fires HERE, while the node is being
        # built to show to the model — not when one of its own edges later
        # fires. Firing it on the edge (the previous design) meant the model
        # was asked to "report the real results" before that data had been
        # fetched at all, and had nothing to go on but a plausible-sounding
        # guess — including inventing ids for its own next function call.
        # Fetching it now, before the model ever has to say anything about
        # this node, means the real data is already in front of it.
        task_messages = list(node.task_messages)
        if node.type == "tool_call" and node.catalog_call:
            result = _run_catalog_call(node.catalog_call, state)
            state.update(result)
            logger.info(f"[{node.name}] real catalog_call {node.catalog_call.get('function')} -> {result}")
            task_messages = task_messages + [
                {
                    "role": "developer",
                    "content": f"Real data just retrieved from the catalog: {json.dumps(result, default=str)}",
                }
            ]

        node_config: NodeConfig = {
            "name": node.name,
            "role_message": node.role_message or self.config.persona,
            "task_messages": task_messages,
            "functions": [self._make_edge_function(node, edge) for edge in node.edges],
        }
        if node.pre_actions:
            node_config["pre_actions"] = node.pre_actions
        # Explicit post_actions win; otherwise a terminal node ends the call.
        if node.post_actions:
            node_config["post_actions"] = node.post_actions
        elif node.end:
            node_config["post_actions"] = [{"type": "end_conversation"}]
        return node_config

    def _make_edge_function(self, node: Node, edge: Edge) -> FlowsFunctionSchema:
        async def handler(args: dict, flow_manager: FlowManager):
            # Persist what the caller gave us so the next node (and beyond) can use it.
            flow_manager.state.update(args)
            logger.info(f"[{edge.function}] -> {edge.target} | collected: {args}")
            next_node = self._make_node(self._nodes_by_name[edge.target], flow_manager.state)
            return {"status": "success", **args}, next_node

        return FlowsFunctionSchema(
            name=edge.function,
            description=edge.description,
            properties=edge.properties,
            required=edge.required,
            handler=handler,
        )
