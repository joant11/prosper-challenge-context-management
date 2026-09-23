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
    "list_specialties": catalog.list_specialties,
    "verify_booking": catalog.verify_booking,
}


def _run_catalog_call(catalog_call: dict, collected: dict, state: dict) -> dict:
    """Call a real catalog.py function for a `catalog_call` node.

    `args` maps the function's own parameter names to the state key that holds
    the value — checked in this turn's freshly collected fields first, then in
    everything accumulated from earlier turns.
    """
    fn_name = catalog_call.get("function")
    fn = CATALOG_FUNCTIONS.get(fn_name)
    if fn is None:
        return {"error": f"unknown catalog function '{fn_name}'"}

    kwargs = {}
    for param, state_key in catalog_call.get("args", {}).items():
        if state_key in collected:
            kwargs[param] = collected[state_key]
        elif state_key in state:
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
        return self._make_node(self._nodes_by_name[self.config.initial_node])

    def _make_node(self, node: Node) -> NodeConfig:
        node_config: NodeConfig = {
            "name": node.name,
            "role_message": node.role_message or self.config.persona,
            "task_messages": node.task_messages,
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
            # Persist what the caller gave us so later nodes can use it.
            result = dict(args)
            if node.type == "tool_call" and node.catalog_call:
                # Real Phase 2 lookup: call the actual catalog.py function.
                result.update(_run_catalog_call(node.catalog_call, result, flow_manager.state))
            flow_manager.state.update(result)
            logger.info(f"[{edge.function}] -> {edge.target} | collected: {result}")
            next_node = self._make_node(self._nodes_by_name[edge.target])
            return {"status": "success", **result}, next_node

        return FlowsFunctionSchema(
            name=edge.function,
            description=edge.description,
            properties=edge.properties,
            required=edge.required,
            handler=handler,
        )
