"""Loopback-only synthetic OpenAI Responses endpoint for Paperclip/Codex trials.

This fixture never calls a model provider and never retains prompt text or auth
headers. It exists so Paperclip's native runner can exercise a disposable Codex
home against deterministic responses before any live-provider qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_REPLY = "PAPERCLIP_SYNTHETIC_FIXTURE_OK"
MAX_REQUEST_BYTES = 1_048_576
FIXTURE_MODEL = "paperclip-synthetic-fixture"
FIXTURE_CALL_PREFIX = "call_resp_fixture_"
BRIDGE_TOOL_ALIASES = {
    "app_brain_autonomous_bri_submit_candidate_2362480aea40": "submit_candidate",
    "app_brain_autonomous_bri_inspect_candidate_1b8157ecda7e": "inspect_candidate",
    "app_brain_autonomous_bri_approve_candidate_35fdd3d22fbd": "approve_candidate",
}
BRIDGE_TOOL_NAME_BY_ACTION = {action: name for name, action in BRIDGE_TOOL_ALIASES.items()}
BRIDGE_SUBMIT_TOOL = BRIDGE_TOOL_NAME_BY_ACTION["submit_candidate"]
BRIDGE_INSPECT_TOOL = BRIDGE_TOOL_NAME_BY_ACTION["inspect_candidate"]
BRIDGE_APPROVE_TOOL = BRIDGE_TOOL_NAME_BY_ACTION["approve_candidate"]
SCRIPTABLE_TOOLS = frozenset(
    {
        "call_api",
        "create_task",
        "exec_command",
        "finish_task",
        "get_task_context",
        "get_task_history",
        "list_agents",
        "paperclip_block",
        "paperclip_finish",
        "reassign_task",
        "search_api",
        "search_tasks",
        "set_dependencies",
        *BRIDGE_TOOL_ALIASES,
    }
)


class FixtureError(ValueError):
    """Invalid fixture configuration or request."""


class ScriptedToolCall:
    """One deterministic function call returned to the native Codex runner."""

    def __init__(self, name: str, arguments: dict[str, Any]):
        if name not in SCRIPTABLE_TOOLS:
            raise FixtureError("fixture_tool_name_not_allowed")
        if type(arguments) is not dict or len(json.dumps(arguments)) > 32_000:
            raise FixtureError("fixture_tool_arguments_invalid")
        self.name = name
        self.arguments = dict(arguments)


def _fixture_tool_outputs(value: Any) -> dict[int, tuple[str, str]]:
    """Return completed, top-level outputs for fixture calls in script order.

    Responses input is untrusted data. Count only an output paired with the
    corresponding top-level function_call item, and require fixture call IDs
    to be the contiguous IDs emitted by this server. A nested or fabricated
    output therefore cannot advance the script.
    """
    if type(value) is not list:
        return {}
    calls: dict[str, str] = {}
    outputs: dict[str, str] = {}
    ambiguous: set[str] = set()
    for item in value:
        if type(item) is not dict:
            continue
        kind = item.get("type")
        call_id = item.get("call_id")
        if type(call_id) is not str:
            continue
        match = re.fullmatch(r"call_resp_fixture_(\d{6})", call_id)
        if match is None:
            continue
        if kind == "function_call":
            name = item.get("name")
            if type(name) is not str:
                continue
            if call_id in calls and calls[call_id] != name:
                ambiguous.add(call_id)
            calls[call_id] = name
        elif kind == "function_call_output":
            output = item.get("output")
            if type(output) is not str:
                continue
            if call_id in outputs and outputs[call_id] != output:
                ambiguous.add(call_id)
            outputs[call_id] = output

    paired: dict[int, tuple[str, str]] = {}
    for call_id, output in outputs.items():
        match = re.fullmatch(r"call_resp_fixture_(\d{6})", call_id)
        assert match is not None
        number = int(match.group(1))
        if number > 0 and call_id in calls and call_id not in ambiguous:
            paired[number] = (calls[call_id], output)
    return paired


_INSPECT_TOOL = BRIDGE_INSPECT_TOOL
_INSPECT_DIFF_MARKER = "--- Exact candidate diff ---"
_TEMPLATE_FIELDS = frozenset(
    {
        "approval_status",
        "base_sha",
        "candidate_sha",
        "handoff_state_revision",
        "inspection_digest",
        "operation_key",
        "status_version",
    }
)
_OUTPUT_FIELDS_BY_TOOL = {
    "exec_command": frozenset({"base_sha", "candidate_sha", "operation_key"}),
    "get_task_context": frozenset({"candidate_sha", "operation_key"}),
    "get_task_history": frozenset({"candidate_sha", "operation_key"}),
    "reassign_task": frozenset({"handoff_state_revision"}),
    "search_tasks": frozenset({"status_version"}),
    BRIDGE_SUBMIT_TOOL: frozenset({"candidate_sha", "operation_key"}),
    BRIDGE_APPROVE_TOOL: frozenset({"candidate_sha"}),
    _INSPECT_TOOL: frozenset(
        {
            "candidate_sha",
            "operation_key",
            "inspection_digest",
        }
    ),
}


def _issue_status_version(value: Any, issue_id: str) -> set[str]:
    """Find the version only on the exact task targeted by the scripted handoff."""
    matches: set[str] = set()

    def visit(node: Any) -> None:
        if type(node) is dict:
            version = node.get("statusVersion")
            if node.get("id") == issue_id and type(version) is int and version >= 0:
                matches.add(str(version))
            for nested in node.values():
                visit(nested)
        elif type(node) is list:
            for nested in node:
                visit(nested)
        elif type(node) is str:
            try:
                visit(json.loads(node))
            except json.JSONDecodeError:
                pass

    visit(value)
    return matches


def _nested_output_text(value: Any) -> list[str]:
    if type(value) is str:
        return [value]
    if type(value) is list:
        return [text for item in value for text in _nested_output_text(item)]
    if type(value) is dict:
        return [text for item in value.values() for text in _nested_output_text(item)]
    return []


def _tool_output_texts(name: str, output: str) -> list[str]:
    texts = [output]
    if name in ("get_task_context", "get_task_history", _INSPECT_TOOL, BRIDGE_APPROVE_TOOL):
        try:
            texts.extend(_nested_output_text(json.loads(output)))
        except json.JSONDecodeError:
            pass
    return texts


def _handoff_state_revisions(value: Any) -> set[str]:
    revisions: set[str] = set()

    def visit(node: Any) -> None:
        if type(node) is dict:
            revision = node.get("stateRevision")
            if type(revision) is int and revision >= 0:
                revisions.add(str(revision))
            for nested in node.values():
                visit(nested)
        elif type(node) is list:
            for nested in node:
                visit(nested)
        elif type(node) is str:
            try:
                visit(json.loads(node))
            except json.JSONDecodeError:
                pass

    visit(value)
    return revisions


def _successful_approval_statuses(value: Any) -> set[str]:
    statuses: set[str] = set()

    def visit(node: Any) -> None:
        if type(node) is dict:
            status = node.get("status")
            if type(status) is str and status in {"published", "review_rejected"}:
                statuses.add(status)
            for nested in node.values():
                visit(nested)
        elif type(node) is list:
            for nested in node:
                visit(nested)
        elif type(node) is str:
            statuses.update(re.findall(r"(?m)^status:(published|review_rejected)$", node))
            try:
                visit(json.loads(node))
            except json.JSONDecodeError:
                pass

    visit(value)
    return statuses


def _inspect_metadata(text: str) -> dict[str, str]:
    if _INSPECT_DIFF_MARKER not in text:
        return {}
    metadata_text = text.split(_INSPECT_DIFF_MARKER, 1)[0].strip()
    try:
        metadata = json.loads(metadata_text)
    except json.JSONDecodeError:
        return {}
    values: dict[str, set[str]] = {}

    def collect(value: Any) -> None:
        if type(value) is dict:
            for key, nested in value.items():
                if (
                    key in {"operation_key", "candidate_sha", "inspection_digest"}
                    and type(nested) is str
                    and re.fullmatch(r"[0-9a-f]{40,64}", nested)
                    and (key != "operation_key" or len(nested) == 64)
                ):
                    values.setdefault(key, set()).add(nested)
                collect(nested)
        elif type(value) is list:
            for nested in value:
                collect(nested)

    collect(metadata)
    if any(len(matches) > 1 for matches in values.values()):
        raise FixtureError("fixture_inspect_metadata_ambiguous")
    return {key: next(iter(matches)) for key, matches in values.items()}


def _script_bindings_from_input(
    value: Any, tool_script: tuple[ScriptedToolCall, ...]
) -> dict[str, str]:
    outputs = _fixture_tool_outputs(value)
    contiguous_count = _function_call_output_count(value, tool_script)
    matches: dict[str, set[str]] = {}
    reassign_target = next(
        (
            call.arguments.get("taskId")
            for call in tool_script
            if call.name == "reassign_task" and type(call.arguments.get("taskId")) is str
        ),
        None,
    )
    for number, (name, output) in outputs.items():
        if number > contiguous_count:
            continue
        allowed_fields = _OUTPUT_FIELDS_BY_TOOL.get(name, frozenset())
        for text in _tool_output_texts(name, output):
            for field, field_value in re.findall(r"(?m)^([a-z_]+):([0-9a-f]{40,64})$", text):
                if field in allowed_fields and not (
                    field == "operation_key" and len(field_value) != 64
                ):
                    matches.setdefault(field, set()).add(field_value)
            if name in ("get_task_context", "get_task_history"):
                for operation_key in re.findall(
                    r"(?m)^brain-autonomous-bridge:([0-9a-f]{64}):submitted$",
                    text,
                ):
                    matches.setdefault("operation_key", set()).add(operation_key)
            elif name == _INSPECT_TOOL:
                for field, field_value in _inspect_metadata(text).items():
                    matches.setdefault(field, set()).add(field_value)
        if name == "search_tasks" and reassign_target is not None:
            try:
                versions = _issue_status_version(json.loads(output), reassign_target)
            except json.JSONDecodeError:
                versions = set()
            if len(versions) > 1:
                raise FixtureError("fixture_status_version_template_ambiguous")
            if versions:
                matches.setdefault("status_version", set()).update(versions)
        elif name == "reassign_task":
            try:
                revisions = _handoff_state_revisions(json.loads(output))
            except json.JSONDecodeError:
                revisions = set()
            if len(revisions) > 1:
                raise FixtureError("fixture_handoff_state_revision_template_ambiguous")
            if revisions:
                matches.setdefault("handoff_state_revision", set()).update(revisions)
        elif name == BRIDGE_APPROVE_TOOL:
            statuses: set[str] = set()
            for text in _tool_output_texts(name, output):
                try:
                    statuses.update(_successful_approval_statuses(json.loads(text)))
                except json.JSONDecodeError:
                    statuses.update(_successful_approval_statuses(text))
            if len(statuses) > 1:
                raise FixtureError("fixture_approval_status_template_ambiguous")
            if statuses and statuses != {"published"}:
                raise FixtureError("fixture_candidate_not_published")
            if statuses:
                matches.setdefault("approval_status", set()).update(statuses)
    for field, values in matches.items():
        if len(values) > 1:
            raise FixtureError(f"fixture_{field}_template_ambiguous")
    return {field: next(iter(values)) for field, values in matches.items()}


def _resolve_script_arguments(
    arguments: dict[str, Any],
    input_items: Any,
    tool_script: tuple[ScriptedToolCall, ...] = (),
) -> dict[str, Any]:
    bindings = _script_bindings_from_input(input_items, tool_script)

    def resolve(value: Any) -> Any:
        if type(value) is str:

            def replace(match: re.Match[str]) -> str:
                field = match.group(1)
                if field not in _TEMPLATE_FIELDS:
                    raise FixtureError("fixture_template_field_not_allowed")
                if field not in bindings:
                    raise FixtureError(f"fixture_{field}_template_missing")
                return bindings[field]

            return re.sub(r"\$\{([a-z_]+)\}", replace, value)
        if type(value) is list:
            return [resolve(item) for item in value]
        if type(value) is dict:
            resolved: dict[str, Any] = {}
            for key, item in value.items():
                if key == "expectedStatusVersion" and item == "${status_version}":
                    version = bindings.get("status_version")
                    if version is None or not version.isdecimal():
                        raise FixtureError("fixture_status_version_template_missing")
                    resolved[key] = int(version)
                else:
                    resolved[key] = resolve(item)
            return resolved
        return value

    return resolve(arguments)


class _State:
    def __init__(self, reply: str, tool_script: tuple[ScriptedToolCall, ...]):
        self.reply = reply
        self.tool_script = tool_script
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.rejected_auth_count = 0

    def observe(self, body: bytes, payload: dict[str, Any]) -> int:
        tool_names = []
        for tool in payload.get("tools", []):
            if type(tool) is dict and type(tool.get("name")) is str:
                tool_names.append(tool["name"])
        record = {
            "input_sha256": hashlib.sha256(body).hexdigest(),
            "model": payload.get("model"),
            "stream": payload.get("stream") is True,
            "tool_names": sorted(set(tool_names)),
            "fixture_output_count": _function_call_output_count(
                payload.get("input"), self.tool_script
            ),
            "all_tool_output_count": _function_call_output_occurrence_count(payload.get("input")),
        }
        with self.lock:
            self.requests.append(record)
            return len(self.requests)

    def reject_auth(self) -> None:
        with self.lock:
            self.rejected_auth_count += 1

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        with self.lock:
            return tuple(dict(item) for item in self.requests)

    def rejected_auth_snapshot(self) -> int:
        with self.lock:
            return self.rejected_auth_count

    def tool_name_sets(self) -> tuple[list[str], ...]:
        with self.lock:
            return tuple(list(item["tool_names"]) for item in self.requests)

    def script_progress(self) -> tuple[tuple[int, int], ...]:
        with self.lock:
            return tuple(
                (item["fixture_output_count"], item["all_tool_output_count"])
                for item in self.requests
            )


def _function_call_output_count(value: Any, tool_script: tuple[ScriptedToolCall, ...] = ()) -> int:
    """Count contiguous, paired fixture outputs, ignoring duplicates/native data."""
    outputs = _fixture_tool_outputs(value)
    count = 0
    while (
        count + 1 in outputs
        and count < len(tool_script)
        and outputs[count + 1][0] == tool_script[count].name
    ):
        count += 1
    return count


def _function_call_output_occurrence_count(value: Any) -> int:
    """Count output items without retaining their contents for fixture diagnostics."""
    items = value if type(value) is list else [value]
    return sum(type(item) is dict and item.get("type") == "function_call_output" for item in items)


def _response(
    response_id: str,
    model: str,
    reply: str,
    *,
    status: str = "completed",
    tool_call: ScriptedToolCall | None = None,
    tool_call_id: str | None = None,
    tools: list[Any] | None = None,
) -> dict:
    output = []
    if status == "completed":
        if tool_call is not None:
            output = [
                {
                    "id": f"fc_{response_id}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": tool_call_id or f"call_{response_id}",
                    "name": tool_call.name,
                    "arguments": json.dumps(
                        tool_call.arguments, sort_keys=True, separators=(",", ":")
                    ),
                }
            ]
        else:
            output = [
                {
                    "id": f"msg_{response_id}",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": reply, "annotations": []}],
                }
            ]
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "model": model,
        "output": output,
        "output_text": reply if status == "completed" and tool_call is None else "",
        "parallel_tool_calls": False,
        "temperature": 0,
        "tool_choice": "auto",
        "tools": tools or [],
        "top_p": 1,
        "truncation": "disabled",
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


class PaperclipResponsesFixture:
    """Small explicit-lifecycle HTTP fixture; only loopback bind addresses are accepted."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3311,
        reply: str = DEFAULT_REPLY,
        tool_script: tuple[ScriptedToolCall, ...] | list[ScriptedToolCall] = (),
    ):
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise FixtureError("fixture_host_must_be_loopback_ip") from exc
        if not address.is_loopback:
            raise FixtureError("fixture_host_must_be_loopback_ip")
        if type(port) is not int or not 0 <= port <= 65535:
            raise FixtureError("fixture_port_invalid")
        if type(reply) is not str or not reply or len(reply.encode("utf-8")) > 4096:
            raise FixtureError("fixture_reply_invalid")
        if type(tool_script) not in (tuple, list) or len(tool_script) > 16:
            raise FixtureError("fixture_tool_script_invalid")
        if any(not isinstance(step, ScriptedToolCall) for step in tool_script):
            raise FixtureError("fixture_tool_script_invalid")
        self._state = _State(reply, tuple(tool_script))
        state = self._state

        class Handler(BaseHTTPRequestHandler):
            server_version = "BrainPaperclipFixture/1"

            def log_message(self, _format: str, *args: Any) -> None:
                # Do not log prompts, auth headers, or provider-like request details.
                return

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, value: dict) -> None:
                self._send(
                    status,
                    json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                    "application/json",
                )

            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path == "/healthz":
                    self._json(
                        200,
                        {
                            "status": "ok",
                            "mode": "synthetic",
                            "request_count": len(state.snapshot()),
                            "rejected_auth_count": state.rejected_auth_snapshot(),
                            "tool_name_sets": state.tool_name_sets(),
                            "script_progress": state.script_progress(),
                        },
                    )
                elif path == "/v1/models":
                    self._json(
                        200,
                        {
                            "object": "list",
                            "data": [
                                {
                                    "id": FIXTURE_MODEL,
                                    "object": "model",
                                    "owned_by": "local-fixture",
                                }
                            ],
                        },
                    )
                else:
                    self._json(404, {"error": {"message": "fixture_route_not_found"}})

            def do_POST(self) -> None:
                if urlsplit(self.path).path != "/v1/responses":
                    self._json(404, {"error": {"message": "fixture_route_not_found"}})
                    return
                if self.headers.get("Authorization") is not None:
                    state.reject_auth()
                    self._json(401, {"error": {"message": "fixture_authentication_forbidden"}})
                    return
                try:
                    raw_length = self.headers.get("Content-Length", "")
                    if not raw_length.isascii() or not raw_length.isdecimal():
                        raise FixtureError("fixture_content_length_invalid")
                    length = int(raw_length)
                    if length <= 0 or length > MAX_REQUEST_BYTES:
                        raise FixtureError("fixture_request_size_invalid")
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise FixtureError("fixture_request_truncated")
                    payload = json.loads(raw)
                    model = payload.get("model") if type(payload) is dict else None
                    if (
                        type(payload) is not dict
                        or type(model) is not str
                        or not model
                        or len(model) > 128
                    ):
                        raise FixtureError("fixture_model_or_body_invalid")
                    if model != FIXTURE_MODEL:
                        raise FixtureError("fixture_model_not_supported")
                    if "input" not in payload:
                        raise FixtureError("fixture_input_missing")
                except (UnicodeDecodeError, json.JSONDecodeError, FixtureError) as exc:
                    self._json(400, {"error": {"message": str(exc)}})
                    return

                request_number = state.observe(raw, payload)
                response_id = f"resp_fixture_{request_number:06d}"
                script_index = _function_call_output_count(payload["input"], state.tool_script)
                tool_call = (
                    state.tool_script[script_index]
                    if script_index < len(state.tool_script)
                    else None
                )
                if tool_call is not None:
                    try:
                        tool_call = ScriptedToolCall(
                            tool_call.name,
                            _resolve_script_arguments(
                                tool_call.arguments, payload["input"], state.tool_script
                            ),
                        )
                    except FixtureError as exc:
                        self._json(422, {"error": {"message": str(exc)}})
                        return
                available_tools = {
                    tool["name"]
                    for tool in payload.get("tools", [])
                    if type(tool) is dict and type(tool.get("name")) is str
                }
                if tool_call is not None and tool_call.name not in available_tools:
                    self._json(422, {"error": {"message": "fixture_script_tool_not_advertised"}})
                    return
                completed = _response(
                    response_id,
                    model,
                    state.reply,
                    tool_call=tool_call,
                    tool_call_id=(
                        f"{FIXTURE_CALL_PREFIX}{script_index + 1:06d}"
                        if tool_call is not None
                        else None
                    ),
                    tools=payload.get("tools"),
                )
                if payload.get("stream") is True:
                    self._stream(response_id, model, completed, tool_call)
                else:
                    self._json(200, completed)

            def _stream(
                self,
                response_id: str,
                model: str,
                completed: dict,
                tool_call: ScriptedToolCall | None,
            ) -> None:
                item = completed["output"][0]
                events = [
                    {
                        "type": "response.created",
                        "response": _response(
                            response_id, model, state.reply, status="in_progress"
                        ),
                    },
                    {
                        "type": "response.in_progress",
                        "response": _response(
                            response_id, model, state.reply, status="in_progress"
                        ),
                    },
                ]
                if tool_call is None:
                    part = item["content"][0]
                    events.extend(
                        [
                            {
                                "type": "response.output_item.added",
                                "output_index": 0,
                                "item": {**item, "status": "in_progress", "content": []},
                            },
                            {
                                "type": "response.content_part.added",
                                "item_id": item["id"],
                                "output_index": 0,
                                "content_index": 0,
                                "part": {"type": "output_text", "text": "", "annotations": []},
                            },
                            {
                                "type": "response.output_text.delta",
                                "item_id": item["id"],
                                "output_index": 0,
                                "content_index": 0,
                                "delta": state.reply,
                            },
                            {
                                "type": "response.output_text.done",
                                "item_id": item["id"],
                                "output_index": 0,
                                "content_index": 0,
                                "text": state.reply,
                            },
                            {
                                "type": "response.content_part.done",
                                "item_id": item["id"],
                                "output_index": 0,
                                "content_index": 0,
                                "part": part,
                            },
                            {"type": "response.output_item.done", "output_index": 0, "item": item},
                        ]
                    )
                else:
                    arguments = item["arguments"]
                    events.extend(
                        [
                            {
                                "type": "response.output_item.added",
                                "output_index": 0,
                                "item": {**item, "status": "in_progress", "arguments": ""},
                            },
                            {
                                "type": "response.function_call_arguments.delta",
                                "item_id": item["id"],
                                "output_index": 0,
                                "delta": arguments,
                            },
                            {
                                "type": "response.function_call_arguments.done",
                                "item_id": item["id"],
                                "output_index": 0,
                                "arguments": arguments,
                            },
                            {"type": "response.output_item.done", "output_index": 0, "item": item},
                        ]
                    )
                events.append({"type": "response.completed", "response": completed})
                chunks = []
                for sequence, event in enumerate(events, start=1):
                    event["sequence_number"] = sequence
                    chunks.append(
                        ("data: " + json.dumps(event, separators=(",", ":")) + "\n\n").encode()
                    )
                body = b"".join(chunks) + b"data: [DONE]\n\n"
                self._send(200, body, "text/event-stream")

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        rendered_host = f"[{host}]" if ":" in host else host
        return f"http://{rendered_host}:{port}/v1"

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> PaperclipResponsesFixture:
        if self._thread is not None:
            raise FixtureError("fixture_already_started")
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=2)
            self._thread = None
        self._server.server_close()

    def requests(self) -> tuple[dict[str, Any], ...]:
        return self._state.snapshot()

    def rejected_auth_count(self) -> int:
        return self._state.rejected_auth_snapshot()

    def tool_name_sets(self) -> tuple[list[str], ...]:
        return self._state.tool_name_sets()

    def script_progress(self) -> tuple[tuple[int, int], ...]:
        return self._state.script_progress()

    def __enter__(self) -> PaperclipResponsesFixture:
        return self.start()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="loopback IP only")
    parser.add_argument("--port", type=int, default=3311)
    parser.add_argument("--reply", default=DEFAULT_REPLY)
    parser.add_argument("--tool-script", type=Path)
    args = parser.parse_args()
    script: list[ScriptedToolCall] = []
    if args.tool_script is not None:
        if args.tool_script.stat().st_mode & 0o077:
            parser.error("tool script must be private (mode 0600 or stricter)")
        raw_script = args.tool_script.read_bytes()
        if len(raw_script) > 65_536:
            parser.error("tool script exceeds 64 KiB")
        try:
            entries = json.loads(raw_script)
            if type(entries) is not list:
                raise FixtureError("fixture_tool_script_invalid")
            for entry in entries:
                if type(entry) is not dict or set(entry) != {"name", "arguments"}:
                    raise FixtureError("fixture_tool_script_invalid")
                script.append(ScriptedToolCall(entry["name"], entry["arguments"]))
        except (json.JSONDecodeError, FixtureError) as exc:
            parser.error(str(exc))
    fixture = PaperclipResponsesFixture(args.host, args.port, args.reply, script).start()
    print(
        json.dumps(
            {
                "base_url": fixture.base_url,
                "model": FIXTURE_MODEL,
                "mode": "synthetic",
                "scripted_tool_count": len(script),
            }
        ),
        flush=True,
    )
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        fixture.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
