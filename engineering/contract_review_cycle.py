"""Drive the durable contract-review journal through an explicitly supplied transport.

The controller owns one-shot admission, session/response custody and local replay. A caller must
provide the reviewer transport; this module never creates a provider client or grants dispatch
authority by itself. A live adapter must independently satisfy the deployment's quota and usage
controls before it is supplied.
"""

from __future__ import annotations

from typing import Any

from .org_state import StateError, Store


def process_pending_reviews(
    db: Store,
    transport: Any,
    *,
    limit: int = 2,
) -> dict:
    """Replay saved responses and send bounded current reviews through an injected transport.

    The transport implements ``start(request)`` returning ``thread_id``, ``model``, ``pid`` and
    ``birth``, then ``review(session, request)`` returning ``provider_turn_id`` and exact
    ``raw_result`` bytes. Requests left in ``dispatching`` or ``thread_known`` after an interruption
    remain unresolved and are never resent here.
    """
    if type(limit) is not int or not 1 <= limit <= 10:
        raise StateError("contract review cycle limit invalid")
    if not callable(getattr(transport, "start", None)) or not callable(
        getattr(transport, "review", None)
    ):
        raise StateError("contract review transport incomplete")

    worklist = db.contract_review_worklist(limit=100)
    items = worklist["items"]
    progress = []
    failures = []
    invoked = 0
    handled = 0
    for item in items:
        if handled >= limit:
            break
        request_id = item["request_id"]
        if item["status"] == "response_recorded":
            handled += 1
            try:
                replayed = db.replay_contract_review_response(request_id, item["packet_hash"])
            except StateError as exc:
                failures.append({"request_id": request_id, "error": str(exc)[:256]})
            else:
                progress.append(
                    {
                        "request_id": request_id,
                        "status": replayed["body"]["status"],
                        "provider_invoked": False,
                    }
                )
            continue
        if not item["dispatch_eligible"]:
            continue

        try:
            handled += 1
            request = db.get_contract_review_request(request_id)
            body = request["body"]
            db.begin_contract_review_dispatch(request_id, item["packet_hash"])
            invoked += 1
            session = transport.start(request)
            if (
                type(session) is not dict
                or set(session) != {"thread_id", "model", "pid", "birth"}
                or type(session.get("model")) is not str
                or not session["model"].strip()
            ):
                raise StateError("contract review session identity invalid")
            identity = {"pid": session["pid"], "birth": session["birth"]}
            db.record_contract_review_session(
                request_id,
                item["packet_hash"],
                session["thread_id"],
                session["model"],
                body["dispatch_id"],
                identity,
            )
            response = transport.review(session, request)
            if (
                type(response) is not dict
                or set(response) != {"provider_turn_id", "raw_result"}
                or type(response.get("raw_result")) is not bytes
            ):
                raise StateError("contract review transport response invalid")
            db.record_contract_review_response(
                request_id,
                item["packet_hash"],
                body["dispatch_id"],
                response["provider_turn_id"],
                response["raw_result"],
            )
            replayed = db.replay_contract_review_response(request_id, item["packet_hash"])
            progress.append(
                {
                    "request_id": request_id,
                    "status": replayed["body"]["status"],
                    "provider_invoked": True,
                }
            )
        except (StateError, ValueError, TypeError, OSError, RuntimeError) as exc:
            failures.append({"request_id": request_id, "error": str(exc)[:256]})

    return {
        "overflow": worklist.get("overflow", False),
        "worklist_count": worklist.get("count"),
        "processed": progress,
        "failures": failures,
        "transport_invocations": invoked,
        "dispatch_authority": "explicitly_injected_transport",
    }
