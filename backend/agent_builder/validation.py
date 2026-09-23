#
# Graph validation — checks that don't require compiling the graph.
#
# `errors` are fatal: AgentBuilder refuses to compile a graph that has them (see
# AgentBuilder._validate, which calls this and raises on any error-level issue).
# `warnings` are surfaced to the UI but never block save or a test call — a stuck
# or unreachable node is worth flagging, not worth refusing to demo.
#

from dataclasses import dataclass

from .schema import AgentConfig


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    message: str
    node: str = None


def validate_config(config: AgentConfig) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not config.nodes:
        issues.append(ValidationIssue("error", "Agent has no nodes."))
        return issues

    names = [n.name for n in config.nodes]
    name_set = set(names)
    seen = set()
    for n in names:
        if n in seen:
            issues.append(ValidationIssue("error", f"Duplicate node name '{n}'.", node=n))
        seen.add(n)

    if config.initial_node not in name_set:
        issues.append(
            ValidationIssue(
                "error", f"initial_node '{config.initial_node}' is not a defined node."
            )
        )

    by_name = {n.name: n for n in config.nodes}
    for node in config.nodes:
        if not node.task_messages:
            issues.append(ValidationIssue("warning", "Node has no task_messages.", node=node.name))
        if not node.edges and not node.end:
            issues.append(
                ValidationIssue(
                    "warning",
                    "Node has no outgoing edges and isn't marked as 'end' — the conversation could get stuck here.",
                    node=node.name,
                )
            )
        for edge in node.edges:
            if edge.target not in name_set:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"Edge '{edge.function}' targets unknown node '{edge.target}'.",
                        node=node.name,
                    )
                )

    if config.initial_node in name_set:
        reachable = {config.initial_node}
        stack = [config.initial_node]
        while stack:
            current = stack.pop()
            for edge in by_name[current].edges:
                if edge.target in name_set and edge.target not in reachable:
                    reachable.add(edge.target)
                    stack.append(edge.target)
        for node in config.nodes:
            if node.name not in reachable:
                issues.append(
                    ValidationIssue(
                        "warning", "Node is unreachable from the initial node.", node=node.name
                    )
                )

    return issues
