"""
Workflow plumbing: a tiny declarative graph spec, a LangGraph builder, and a
local runner.

Production runs the spec through LangGraph (`build_langgraph`). The local
runner implements the same semantics (run a node, merge its returned partial
state, follow the conditional or plain edge) so the exact same graph
topology and routing are unit-tested without LangGraph installed — the graph
definition lives in ONE place (a WorkflowSpec) and both executors consume it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
RouterFn = Callable[[dict[str, Any]], str]


@dataclass
class WorkflowSpec:
    entry: str
    nodes: dict[str, NodeFn]
    edges: list[tuple[str, str]] = field(default_factory=list)
    # node -> (router, {router_result: next_node})
    conditionals: dict[str, tuple[RouterFn, dict[str, str]]] = field(default_factory=dict)
    finish: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """Fail fast on a mis-wired graph (unknown node, node with no way out)."""
        names = set(self.nodes)
        if self.entry not in names:
            raise ValueError(f"Entry node '{self.entry}' is not defined.")
        for src, dst in self.edges:
            if src not in names or dst not in names:
                raise ValueError(f"Edge {src}->{dst} references an unknown node.")
        for src, (_, mapping) in self.conditionals.items():
            if src not in names or any(dst not in names for dst in mapping.values()):
                raise ValueError(f"Conditional edge from '{src}' references an unknown node.")
        for name in self.finish:
            if name not in names:
                raise ValueError(f"Finish node '{name}' is not defined.")
        for name in names:
            has_out = (
                name in self.finish or name in self.conditionals or any(src == name for src, _ in self.edges)
            )
            if not has_out:
                raise ValueError(f"Node '{name}' has no outgoing edge.")


def build_langgraph(spec: WorkflowSpec, state_schema: type):
    """Compile the spec with LangGraph. Imported lazily so modules that only
    need the spec (tests, tooling) do not require the dependency."""
    from langgraph.graph import END, START, StateGraph

    spec.validate()
    graph = StateGraph(state_schema)
    for name, fn in spec.nodes.items():
        graph.add_node(name, fn)
    graph.add_edge(START, spec.entry)
    for src, dst in spec.edges:
        graph.add_edge(src, dst)
    for src, (router, mapping) in spec.conditionals.items():
        graph.add_conditional_edges(src, router, mapping)
    for name in spec.finish:
        graph.add_edge(name, END)
    return graph.compile()


async def run_workflow(spec: WorkflowSpec, state_schema: type, initial: dict[str, Any], *, recursion_limit: int = 60) -> dict[str, Any]:
    """Execute with LangGraph and return the final state."""
    compiled = build_langgraph(spec, state_schema)
    return await compiled.ainvoke(initial, config={"recursion_limit": recursion_limit})


async def run_locally(spec: WorkflowSpec, initial: dict[str, Any], *, max_steps: int = 60) -> dict[str, Any]:
    """Reference executor with the same semantics as LangGraph — used by unit
    tests (and nothing else)."""
    spec.validate()
    state = dict(initial)
    current: Optional[str] = spec.entry
    steps = 0
    while current is not None:
        steps += 1
        if steps > max_steps:
            raise RuntimeError("Workflow exceeded the step limit (possible loop).")
        update = await spec.nodes[current](state)
        if update:
            state.update(update)
        if current in spec.conditionals:
            router, mapping = spec.conditionals[current]
            current = mapping[router(state)]
        elif current in spec.finish:
            current = None
        else:
            current = next(dst for src, dst in spec.edges if src == current)
    return state
