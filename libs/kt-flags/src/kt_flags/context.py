"""Evaluation context plumbing.

Even though Phase 0 has no per-user / per-tenant targeting, the context is
wired end-to-end so a future ``DbProvider`` with targeting rules Just Works.
"""

from __future__ import annotations

from openfeature.evaluation_context import EvaluationContext

# Public alias — callers import ``EvalContext`` from ``kt_flags``.
EvalContext = EvaluationContext


def build_eval_context(
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    graph_id: str | None = None,
    environment: str | None = None,
    extra: dict[str, object] | None = None,
) -> EvalContext:
    """Construct an OpenFeature ``EvaluationContext`` from request / task inputs.

    ``user_id`` becomes the ``targeting_key`` (OpenFeature's conventional
    identifier for percentage rollouts). All other fields land in
    ``attributes``.
    """
    attributes: dict[str, object] = {}
    if tenant_id is not None:
        attributes["tenant_id"] = tenant_id
    if graph_id is not None:
        attributes["graph_id"] = graph_id
    if environment is not None:
        attributes["environment"] = environment
    if extra:
        attributes.update(extra)
    return EvaluationContext(targeting_key=user_id, attributes=attributes)
