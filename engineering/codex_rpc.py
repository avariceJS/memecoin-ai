"""Pinned Codex app-server adapter. Models receive one brokered, confined tool.

Authentication stays in the trusted harness; neither its home nor its sockets are
granted to model-generated commands. Native tools are disabled where supported;
the remaining native filesystem policy is read-only and explicitly restricted.
"""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class RpcError(RuntimeError):
    pass


class Codex:
    def __init__(
        self, cwd: Path, *, codex: str = "codex", event_sink=None, harness_cwd: Path | None = None
    ):
        self.cwd = cwd.resolve()
        harness_cwd = harness_cwd or Path(tempfile.mkdtemp(prefix="brain-org-harness-"))
        if harness_cwd == self.cwd or self.cwd in harness_cwd.parents:
            raise RpcError("harness_must_be_outside_worker_checkout")
        for ancestor in (self.cwd, *self.cwd.parents, harness_cwd, *harness_cwd.parents):
            if (ancestor / ".codex/config.toml").is_file() and ancestor != Path.home():
                raise RpcError("project_codex_config_rejected")
        self.sink = event_sink or (lambda event: None)
        cfg = {
            "notify": [],
            "features.shell_tool": False,
            "features.unified_exec": False,
            "features.multi_agent": False,
            "features.apps": False,
            "features.hooks": False,
            "features.browser_use": False,
            "features.browser_use_external": False,
            "features.computer_use": False,
            "features.image_generation": False,
            "features.view_image": False,
            "features.skill_search": False,
            "features.tool_suggest": False,
            "features.shell_snapshot": False,
            "features.code_mode_host": False,
            "features.code_mode": False,
            "plugins._default.enabled": False,
            "skills.include_instructions": False,
            "web_search": "disabled",
            "project_doc_max_bytes": 0,
            "approval_policy": "never",
            "allow_login_shell": False,
            "shell_environment_policy.inherit": "none",
            "default_permissions": "org-native",
            "permissions.org-native.filesystem": {
                # :minimal includes required Darwin loader/runtime operations,
                # and otherwise implicitly grants shared temporary storage.
                # Remove those ambient temp grants; only the assigned checkout
                # is an additional model-readable root. Platform runtime reads
                # remain permitted; this is not a cwd-only filesystem claim.
                ":minimal": "read",
                "/tmp": "none",
                "/private/tmp": "none",
                "/var/tmp": "none",
                "/private/var/tmp": "none",
                "/var/folders": "none",
                "/private/var/folders": "none",
                str(self.cwd): "read",
            },
            "permissions.org-native.network.enabled": False,
        }
        # Fail closed if task-local config exists. Worker write grants never include
        # checkout root, .git, .codex or this trusted harness directory.
        if (self.cwd / ".codex").exists():
            raise RpcError("task_local_codex_config_rejected")
        import tomllib

        config_path = Path.home() / ".codex/config.toml"
        config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
        for name in config.get("mcp_servers", {}):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise RpcError("unsupported_mcp_config_key")
            cfg["mcp_servers." + name + ".enabled"] = False
        cfg["plugins"] = {name: {"enabled": False} for name in config.get("plugins", {})}
        cfg["plugins"]["_default"] = {"enabled": False}

        # TOML inline dictionaries use '=' rather than JSON's ':'.
        def toml(v):
            if isinstance(v, dict):
                return "{" + ",".join(json.dumps(k) + "=" + toml(x) for k, x in v.items()) + "}"
            return json.dumps(v)

        args = [codex, "app-server", "--stdio"]
        for k, v in cfg.items():
            args.extend(["-c", k + "=" + toml(v)])
        env = {k: os.environ[k] for k in ("HOME", "PATH", "TMPDIR", "LANG") if k in os.environ}
        self.proc = subprocess.Popen(
            args,
            cwd=harness_cwd or self.cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self.sink({"kind": "app_server_spawned", "pid": self.proc.pid})
        self.messages = queue.Queue()
        self.sequence = 0
        self.notifications = []
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.call(
                "initialize",
                {
                    "clientInfo": {"name": "brain_engineering", "version": "0.1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
        except Exception:
            self.close()
            raise RpcError("app_server_initialization_failed") from None

        def drain_stderr():
            for _ in self.proc.stderr:
                pass

        threading.Thread(target=drain_stderr, daemon=True).start()
        self.send({"method": "initialized"})

    def _read(self):
        try:
            for line in self.proc.stdout:
                self.messages.put(json.loads(line))
        finally:
            self.messages.put({"_closed": True})

    def send(self, value):
        with self.lock:
            self.proc.stdin.write(json.dumps(value) + "\n")
            self.proc.stdin.flush()

    def call(self, method, params=None, timeout=40):
        self.sequence += 1
        ident = self.sequence
        payload = {"id": ident, "method": method}
        if params is not None:
            payload["params"] = params
        self.send(payload)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msg = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise RpcError("rpc_timeout:" + method) from exc
            if msg.get("_closed"):
                raise RpcError("app_server_closed")
            if msg.get("id") == ident and "method" not in msg:
                if "error" in msg:
                    raise RpcError(json.dumps(msg["error"]))
                return msg.get("result", {})
            self.notifications.append(msg)
        raise RpcError("rpc_timeout:" + method)

    def start(self, model, instructions):
        result = self.call(
            "thread/start",
            {
                "cwd": str(self.cwd),
                "model": model,
                "approvalPolicy": "never",
                "permissions": "org-native",
                "ephemeral": False,
                "allowProviderModelFallback": False,
                "developerInstructions": instructions,
                "dynamicTools": [
                    {
                        "type": "function",
                        "name": "org_exec",
                        "description": (
                            "Run bounded argv in the assigned OS sandbox. cwd is the task "
                            "checkout; no network, owner files, credentials, publisher, "
                            "other tasks or control state. Use Python for file edits. "
                            "All paths outside assigned roots are denied."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "argv": {"type": "array", "items": {"type": "string"}},
                                "timeout": {"type": "integer", "minimum": 1, "maximum": 90},
                            },
                            "required": ["argv", "timeout"],
                            "additionalProperties": False,
                        },
                    }
                ],
            },
        )
        return result["thread"]["id"]

    def resume(self, thread_id, model):
        return self.call(
            "thread/resume",
            {
                "threadId": thread_id,
                "excludeTurns": True,
                "cwd": str(self.cwd),
                "model": model,
                "approvalPolicy": "never",
                "permissions": "org-native",
            },
        )

    def turn(
        self,
        thread_id,
        prompt,
        *,
        model,
        tool,
        deadline_seconds=600,
        heartbeat=None,
        output_schema=None,
        started=None,
    ):
        params = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "model": model,
            "effort": "high",
            "approvalPolicy": "never",
            "permissions": "org-native",
        }
        if output_schema:
            params["outputSchema"] = output_schema
        result = self.call("turn/start", params)
        turn_id = result["turn"]["id"]
        if started:
            started(turn_id)
        self.sink(
            {"kind": "turn_started", "thread_id": thread_id, "turn_id": turn_id, "model": model}
        )
        end = time.monotonic() + deadline_seconds
        last_heartbeat = 0.0
        outputs, usage = [], {}
        while time.monotonic() < end:
            now = time.monotonic()
            if heartbeat and now - last_heartbeat > 5:
                heartbeat()
                last_heartbeat = now
            if self.notifications:
                msg = self.notifications.pop(0)
            else:
                try:
                    msg = self.messages.get(timeout=1)
                except queue.Empty:
                    continue
            if msg.get("_closed"):
                raise RpcError("app_server_closed")
            method, p = msg.get("method"), msg.get("params", {})
            if method == "item/tool/call":
                if (
                    p.get("tool") != "org_exec"
                    or p.get("threadId") != thread_id
                    or p.get("turnId") != turn_id
                ):
                    response = {
                        "success": False,
                        "contentItems": [{"type": "inputText", "text": "unauthorized_tool"}],
                    }
                else:
                    try:
                        args = p["arguments"]
                        if isinstance(args, str):
                            args = json.loads(args)
                        value = tool(args)
                        response = {
                            "success": True,
                            "contentItems": [{"type": "inputText", "text": json.dumps(value)}],
                        }
                    except Exception as exc:
                        response = {
                            "success": False,
                            "contentItems": [
                                {
                                    "type": "inputText",
                                    "text": type(exc).__name__ + ": " + str(exc)[:500],
                                }
                            ],
                        }
                self.send({"id": msg["id"], "result": response})
                self.sink(
                    {
                        "kind": "tool",
                        "thread_id": thread_id,
                        "tool": p.get("tool"),
                        "success": response["success"],
                    }
                )
            elif "id" in msg and method:
                # No approval, credential, browser or other interactive capabilities.
                self.send(
                    {
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "organization_capability_denied"},
                    }
                )
            elif (
                method == "item/completed"
                and p.get("threadId") == thread_id
                and p.get("turnId") == turn_id
            ):
                item = p.get("item", {})
                if item.get("type") == "agentMessage":
                    outputs.append(item.get("text", ""))
            elif method == "thread/tokenUsage/updated" and p.get("threadId") == thread_id:
                usage = p.get("tokenUsage", {})
                self.sink({"kind": "token_usage", "thread_id": thread_id, "usage": usage})
                if usage.get("total", {}).get("totalTokens", 0) > 180000:
                    raise RpcError("turn_token_ceiling")
            elif method == "model/rerouted":
                raise RpcError("model_reroute_rejected")
            elif method == "turn/completed" and p.get("turn", {}).get("id") == turn_id:
                status = p["turn"].get("status")
                self.sink(
                    {
                        "kind": "turn_completed",
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "status": status,
                        "usage": usage,
                    }
                )
                if status != "completed":
                    raise RpcError("turn_" + str(status) + ":" + json.dumps(p["turn"].get("error")))
                return {"text": "\n".join(outputs), "usage": usage, "turn_id": turn_id}
        try:
            self.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10)
        finally:
            raise RpcError("turn_deadline")

    def close(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
        if hasattr(self, "reader"):
            self.reader.join(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream and not stream.closed:
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
