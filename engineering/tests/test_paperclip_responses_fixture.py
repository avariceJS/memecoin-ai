"""Offline protocol and custody checks for the Paperclip native-runner fixture."""

from __future__ import annotations

import hashlib
import json
import unittest
import urllib.error
import urllib.request

from engineering.paperclip_responses_fixture import (
    BRIDGE_SUBMIT_TOOL,
    BRIDGE_TOOL_ALIASES,
    DEFAULT_REPLY,
    FIXTURE_MODEL,
    FixtureError,
    PaperclipResponsesFixture,
    ScriptedToolCall,
    _resolve_script_arguments,
)


def _request(url: str, payload: dict, *, authorization: str | None = None):
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=2)


class PaperclipResponsesFixtureTests(unittest.TestCase):
    def test_bridge_fixture_names_match_fixed_native_responses_aliases(self):
        self.assertEqual(
            BRIDGE_TOOL_ALIASES,
            {
                "app_brain_autonomous_bri_submit_candidate_2362480aea40": "submit_candidate",
                "app_brain_autonomous_bri_inspect_candidate_1b8157ecda7e": "inspect_candidate",
                "app_brain_autonomous_bri_approve_candidate_35fdd3d22fbd": "approve_candidate",
            },
        )
        for name in (
            "brain.autonomous-bridge:submit_candidate",
            "brain.autonomous-bridge:inspect_candidate",
            "brain.autonomous-bridge:approve_candidate",
        ):
            with self.assertRaisesRegex(FixtureError, "tool_name_not_allowed"):
                ScriptedToolCall(name, {})

    def test_fixture_is_loopback_only_and_serves_deterministic_nonstream_response(self):
        with self.assertRaisesRegex(FixtureError, "loopback"):
            PaperclipResponsesFixture("0.0.0.0", 3311)

        with PaperclipResponsesFixture(port=0) as fixture:
            health = json.load(
                urllib.request.urlopen(fixture.base_url.removesuffix("/v1") + "/healthz")
            )
            self.assertEqual(
                health,
                {
                    "status": "ok",
                    "mode": "synthetic",
                    "request_count": 0,
                    "rejected_auth_count": 0,
                    "tool_name_sets": [],
                    "script_progress": [],
                },
            )

            models = json.load(urllib.request.urlopen(fixture.base_url + "/models"))
            self.assertEqual(models["data"][0]["id"], FIXTURE_MODEL)

            prompt = "synthetic Paperclip issue with a unique marker: ISSUE-LOCAL-17"
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": prompt},
            ) as response:
                result = json.load(response)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["output_text"], DEFAULT_REPLY)
            self.assertEqual(result["model"], FIXTURE_MODEL)
            self.assertEqual(
                fixture.requests(),
                (
                    {
                        "input_sha256": hashlib.sha256(
                            json.dumps({"model": FIXTURE_MODEL, "input": prompt}).encode("utf-8")
                        ).hexdigest(),
                        "model": FIXTURE_MODEL,
                        "stream": False,
                        "tool_names": [],
                        "fixture_output_count": 0,
                        "all_tool_output_count": 0,
                    },
                ),
            )
            self.assertEqual(fixture.tool_name_sets(), ([],))
            self.assertEqual(fixture.script_progress(), ((0, 0),))
            self.assertNotIn("ISSUE-LOCAL-17", repr(fixture.requests()))

    def test_fixture_fails_closed_on_auth_and_streams_completed_events(self):
        with PaperclipResponsesFixture(port=0) as fixture:
            with self.assertRaises(urllib.error.HTTPError) as error:
                _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": "never retain this"},
                    authorization="Bearer synthetic-secret-marker",
                )
            self.assertEqual(error.exception.code, 401)
            self.assertEqual(fixture.requests(), ())
            self.assertEqual(fixture.rejected_auth_count(), 1)

            with _request(
                fixture.base_url + "/responses",
                {
                    "model": FIXTURE_MODEL,
                    "input": [{"role": "user", "content": "offline"}],
                    "stream": True,
                },
            ) as response:
                stream = response.read().decode("utf-8")

            self.assertEqual(response.headers["Content-Type"], "text/event-stream")
            self.assertIn("response.output_text.delta", stream)
            self.assertIn("response.completed", stream)
            self.assertTrue(stream.rstrip().endswith("data: [DONE]"))
            self.assertEqual(fixture.rejected_auth_count(), 1)
            self.assertNotIn("authorization", repr(fixture.requests()).lower())
            self.assertNotIn("synthetic-secret-marker", repr(fixture.requests()))

    def test_fixture_rejects_models_outside_its_synthetic_protocol(self):
        with PaperclipResponsesFixture(port=0) as fixture:
            with self.assertRaises(urllib.error.HTTPError) as error:
                _request(
                    fixture.base_url + "/responses",
                    {"model": "unconfigured-model", "input": "offline"},
                )
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(
                json.loads(error.exception.read())["error"]["message"],
                "fixture_model_not_supported",
            )
            self.assertEqual(fixture.requests(), ())

    def test_scripted_tool_calls_advance_from_outputs_and_require_advertised_tools(self):
        script = (
            ScriptedToolCall("list_agents", {}),
            ScriptedToolCall("search_tasks", {"query": "synthetic candidate"}),
        )
        tools = [
            {"type": "function", "name": "list_agents", "parameters": {"type": "object"}},
            {"type": "function", "name": "search_tasks", "parameters": {"type": "object"}},
        ]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            first_input = [{"role": "user", "content": "delegate synthetic task"}]
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": first_input, "tools": tools},
            ) as response:
                first = json.load(response)
            self.assertEqual(first["output"][0]["name"], "list_agents")
            self.assertEqual(json.loads(first["output"][0]["arguments"]), {})

            second_input = first_input + [
                {
                    "type": "function_call",
                    "call_id": first["output"][0]["call_id"],
                    "name": "list_agents",
                },
                {
                    "type": "function_call_output",
                    "call_id": first["output"][0]["call_id"],
                    "output": "synthetic Sol/Luna roster",
                },
            ]
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": second_input, "tools": tools, "stream": True},
            ) as response:
                second_stream = response.read().decode("utf-8")
            self.assertIn("response.function_call_arguments.done", second_stream)
            self.assertIn('"name":"search_tasks"', second_stream)

            second_response = json.loads(
                next(
                    line[6:]
                    for line in reversed(second_stream.splitlines())
                    if line.startswith("data: {") and '"type":"response.completed"' in line
                )
            )["response"]
            final_input = second_input + [
                {
                    "type": "function_call",
                    "call_id": second_response["output"][0]["call_id"],
                    "name": "search_tasks",
                },
                {
                    "type": "function_call_output",
                    "call_id": second_response["output"][0]["call_id"],
                    "output": "one synthetic backlog task",
                },
            ]
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": final_input, "tools": tools},
            ) as response:
                final = json.load(response)
            self.assertEqual(final["output_text"], DEFAULT_REPLY)
            self.assertEqual(
                [entry["tool_names"] for entry in fixture.requests()],
                [
                    ["list_agents", "search_tasks"],
                    ["list_agents", "search_tasks"],
                    ["list_agents", "search_tasks"],
                ],
            )

    def test_script_rejects_a_tool_not_advertised_by_the_runner(self):
        tool = {"type": "function", "name": "finish_task", "parameters": {"type": "object"}}
        with PaperclipResponsesFixture(
            port=0, tool_script=(ScriptedToolCall("finish_task", {"summary": "done"}),)
        ) as fixture:
            with self.assertRaises(urllib.error.HTTPError) as error:
                with _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": "run", "tools": []},
                ):
                    pass
            self.assertEqual(error.exception.code, 422)
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": "first valid run", "tools": [tool]},
            ) as response:
                first = json.load(response)
            with _request(
                fixture.base_url + "/responses",
                {
                    "model": FIXTURE_MODEL,
                    "input": "independent run with empty history",
                    "tools": [tool],
                },
            ) as response:
                second = json.load(response)
            self.assertEqual(first["output"][0]["call_id"], "call_resp_fixture_000001")
            self.assertEqual(second["output"][0]["call_id"], "call_resp_fixture_000001")
            self.assertNotEqual(first["id"], second["id"])

    def test_missing_candidate_template_is_a_controlled_fixture_error(self):
        script = (ScriptedToolCall("paperclip_finish", {"summary": "${candidate_sha}"}),)
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with self.assertRaises(urllib.error.HTTPError) as error:
                with _request(
                    fixture.base_url + "/responses",
                    {
                        "model": FIXTURE_MODEL,
                        "input": "no candidate exists yet",
                        "tools": [
                            {
                                "type": "function",
                                "name": "paperclip_finish",
                                "parameters": {"type": "object"},
                            }
                        ],
                    },
                ) as response:
                    response.read()
            self.assertEqual(error.exception.code, 422)
            body = json.loads(error.exception.read())
            self.assertEqual(body["error"]["message"], "fixture_candidate_sha_template_missing")

    def test_script_can_bind_completion_to_a_candidate_sha_from_tool_output(self):
        candidate_sha = "a" * 40
        resolved = _resolve_script_arguments(
            {"summary": "Review ${candidate_sha}", "evidence": ["git:${candidate_sha}"]},
            [
                {
                    "type": "function_call",
                    "call_id": "call_resp_fixture_000001",
                    "name": "exec_command",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_resp_fixture_000001",
                    "output": "base_sha:" + "b" * 40 + "\ncandidate_sha:" + candidate_sha + "\n",
                },
            ],
            (ScriptedToolCall("exec_command", {}),),
        )
        self.assertEqual(
            resolved,
            {
                "summary": "Review " + candidate_sha,
                "evidence": ["git:" + candidate_sha],
            },
        )
        with self.assertRaisesRegex(FixtureError, "template_missing"):
            _resolve_script_arguments({"summary": "Review ${candidate_sha}"}, [])

    def test_script_ignores_nonfixture_tool_outputs_when_advancing(self):
        script = (
            ScriptedToolCall("list_agents", {}),
            ScriptedToolCall("exec_command", {"cmd": "verify synthetic candidate"}),
        )
        tools = [
            {"type": "function", "name": "list_agents", "parameters": {"type": "object"}},
            {"type": "function", "name": "exec_command", "parameters": {"type": "object"}},
        ]
        first_input = [
            {
                "type": "function_call_output",
                "call_id": "paperclip_native_1",
                "output": "unrelated",
            },
            {"role": "user", "content": "verify synthetic candidate"},
        ]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": first_input, "tools": tools},
            ) as response:
                first = json.load(response)
            self.assertEqual(first["output"][0]["name"], "list_agents")

            next_input = first_input + [
                {
                    "type": "function_call",
                    "call_id": "call_resp_fixture_000001",
                    "name": "list_agents",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_resp_fixture_000001",
                    "output": "synthetic agent roster",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_resp_fixture_000001",
                    "output": "synthetic agent roster",
                },
                {
                    "duplicate_transport_envelope": {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000001",
                        "output": "synthetic agent roster",
                    }
                },
                {
                    "type": "function_call_output",
                    "call_id": "paperclip_native_2",
                    "output": "another unrelated response",
                },
            ]
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": next_input, "tools": tools},
            ) as response:
                second = json.load(response)
            self.assertEqual(second["output"][0]["name"], "exec_command")
            self.assertEqual(
                json.loads(second["output"][0]["arguments"]),
                {"cmd": "verify synthetic candidate"},
            )

    def test_script_progress_requires_a_contiguous_matching_top_level_call_pair(self):
        script = (
            ScriptedToolCall("list_agents", {}),
            ScriptedToolCall("exec_command", {"cmd": "verify"}),
        )
        unrelated_or_embedded = [
            {
                "type": "function_call_output",
                "call_id": "call_resp_fixture_000001",
                "output": "fabricated without a function_call",
            },
            {
                "wrapper": {
                    "type": "function_call",
                    "call_id": "call_resp_fixture_000001",
                    "name": "list_agents",
                }
            },
            {
                "wrapper": {
                    "type": "function_call_output",
                    "call_id": "call_resp_fixture_000001",
                    "output": "candidate_sha:" + "a" * 40,
                }
            },
        ]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with _request(
                fixture.base_url + "/responses",
                {
                    "model": FIXTURE_MODEL,
                    "input": unrelated_or_embedded,
                    "tools": [
                        {
                            "type": "function",
                            "name": "list_agents",
                            "parameters": {"type": "object"},
                        }
                    ],
                },
            ) as response:
                first = json.load(response)
            self.assertEqual(first["output"][0]["name"], "list_agents")

    def test_script_can_bind_a_finish_call_to_the_paired_exec_output(self):
        candidate_sha = "c" * 40
        script = (
            ScriptedToolCall("get_task_context", {}),
            ScriptedToolCall("exec_command", {"cmd": "verify candidate"}),
            ScriptedToolCall("paperclip_finish", {"summary": "Candidate ${candidate_sha}"}),
        )
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}}
            for name in ("get_task_context", "exec_command", "paperclip_finish")
        ]
        input_items = [{"role": "user", "content": "verify candidate"}]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            for number, (name, output) in enumerate(
                [
                    ("get_task_context", "synthetic task context"),
                    ("exec_command", f"candidate_sha:{candidate_sha}\n"),
                ],
                start=1,
            ):
                with _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
                ) as response:
                    result = json.load(response)
                self.assertEqual(result["output"][0]["name"], name)
                call_id = f"call_resp_fixture_{number:06d}"
                input_items.extend(
                    [
                        {"type": "function_call", "call_id": call_id, "name": name},
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output,
                        },
                    ]
                )
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                finish = json.load(response)
            self.assertEqual(finish["output"][0]["name"], "paperclip_finish")
            self.assertEqual(
                json.loads(finish["output"][0]["arguments"]),
                {"summary": f"Candidate {candidate_sha}"},
            )

    def test_exec_session_is_polled_by_exact_id_until_terminal_before_candidate_binding(self):
        session_id = 4815162342
        base_sha = "b" * 40
        candidate_sha = "c" * 40
        script = (
            ScriptedToolCall(
                "exec_command", {"cmd": "recover exact existing candidate", "yield_time_ms": 250}
            ),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall(
                BRIDGE_SUBMIT_TOOL, {"base_sha": "${base_sha}", "submitted_sha": "${candidate_sha}"}
            ),
        )
        names = ["exec_command", "write_stdin", BRIDGE_SUBMIT_TOOL]
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}} for name in names
        ]
        input_items = [{"role": "user", "content": "recover the existing candidate"}]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                first = json.load(response)
            self.assertEqual(first["output"][0]["call_id"], "call_resp_fixture_000001")
            self.assertEqual(first["output"][0]["name"], "exec_command")

            input_items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000001",
                        "name": "exec_command",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000001",
                        "output": f"Process running with session ID: {session_id}\n",
                    },
                ]
            )
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                poll = json.load(response)
            self.assertEqual(poll["output"][0]["call_id"], "call_resp_fixture_000002")
            self.assertEqual(poll["output"][0]["name"], "write_stdin")
            self.assertEqual(
                json.loads(poll["output"][0]["arguments"]),
                {"session_id": session_id, "yield_time_ms": 10_000},
            )

            input_items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000002",
                        "name": "write_stdin",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000002",
                        "output": json.dumps(
                            {"output": "still running", "session_id": session_id, "exit_code": None}
                        ),
                    },
                ]
            )
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                poll_again = json.load(response)
            self.assertEqual(poll_again["output"][0]["call_id"], "call_resp_fixture_000003")
            self.assertEqual(poll_again["output"][0]["name"], "write_stdin")

            input_items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000003",
                        "name": "write_stdin",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000003",
                        "output": json.dumps(
                            {
                                "output": "still running",
                                "session_id": session_id,
                                "exit_code": None,
                            }
                        ),
                    },
                ]
            )
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                poll_last = json.load(response)
            self.assertEqual(poll_last["output"][0]["call_id"], "call_resp_fixture_000004")
            self.assertEqual(poll_last["output"][0]["name"], "write_stdin")

            input_items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000004",
                        "name": "write_stdin",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000004",
                        "output": json.dumps(
                            {
                                "output": (
                                    f"Process exited with code 0\nbase_sha:{base_sha}"
                                    f"\ncandidate_sha:{candidate_sha}\n"
                                ),
                            }
                        ),
                    },
                ]
            )
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                submit = json.load(response)
            self.assertEqual(submit["output"][0]["call_id"], "call_resp_fixture_000005")
            self.assertEqual(submit["output"][0]["name"], names[2])
            self.assertEqual(
                json.loads(submit["output"][0]["arguments"]),
                {"base_sha": base_sha, "submitted_sha": candidate_sha},
            )
            self.assertEqual(fixture.script_progress(), ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4)))

    def test_completed_exec_skips_optional_session_poll_group(self):
        base_sha = "b" * 40
        candidate_sha = "c" * 40
        script = (
            ScriptedToolCall("exec_command", {"cmd": "fast verified commit"}),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall(
                BRIDGE_SUBMIT_TOOL, {"base_sha": "${base_sha}", "submitted_sha": "${candidate_sha}"}
            ),
        )
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}}
            for name in ("exec_command", "write_stdin", BRIDGE_SUBMIT_TOOL)
        ]
        input_items = [
            {"role": "user", "content": "fast run"},
            {
                "type": "function_call",
                "call_id": "call_resp_fixture_000001",
                "name": "exec_command",
            },
            {
                "type": "function_call_output",
                "call_id": "call_resp_fixture_000001",
                "output": json.dumps(
                    {
                        "output": f"base_sha:{base_sha}\ncandidate_sha:{candidate_sha}\n",
                        "exit_code": 0,
                    }
                ),
            },
        ]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                submit = json.load(response)
            self.assertEqual(submit["output"][0]["call_id"], "call_resp_fixture_000002")
            self.assertEqual(submit["output"][0]["name"], BRIDGE_SUBMIT_TOOL)
            self.assertEqual(
                json.loads(submit["output"][0]["arguments"]),
                {"base_sha": base_sha, "submitted_sha": candidate_sha},
            )

    def test_exec_session_poll_fails_closed_without_exact_session_or_terminal_status(self):
        session_id = 7
        base_sha = "b" * 40
        candidate_sha = "c" * 40
        script = (
            ScriptedToolCall("exec_command", {"cmd": "recover"}),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall("paperclip_finish", {"summary": "${candidate_sha}"}),
        )
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}}
            for name in ("exec_command", "write_stdin", "paperclip_finish")
        ]
        exec_result = json.dumps({"output": "", "session_id": session_id, "exit_code": None})
        for poll_result, expected_error in (
            (
                json.dumps({"output": f"base_sha:{base_sha}\ncandidate_sha:{candidate_sha}\n"}),
                "fixture_exec_session_result_unknown",
            ),
            (
                json.dumps({"output": "failed", "session_id": session_id, "exit_code": 1}),
                "fixture_exec_session_exit_nonzero",
            ),
        ):
            input_items = [{"role": "user", "content": "recover safely"}]
            with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
                with _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
                ) as response:
                    first = json.load(response)
                self.assertEqual(first["output"][0]["name"], "exec_command")
                input_items.extend(
                    [
                        {
                            "type": "function_call",
                            "call_id": "call_resp_fixture_000001",
                            "name": "exec_command",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "call_resp_fixture_000001",
                            "output": exec_result,
                        },
                    ]
                )
                with _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
                ) as response:
                    poll = json.load(response)
                self.assertEqual(poll["output"][0]["name"], "write_stdin")
                input_items.extend(
                    [
                        {
                            "type": "function_call",
                            "call_id": "call_resp_fixture_000002",
                            "name": "write_stdin",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "call_resp_fixture_000002",
                            "output": poll_result,
                        },
                    ]
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    _request(
                        fixture.base_url + "/responses",
                        {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
                    )
                self.assertEqual(error.exception.code, 422)
                self.assertEqual(
                    json.loads(error.exception.read())["error"]["message"], expected_error
                )

    def test_write_stdin_script_uses_only_empty_input_and_bounded_polls(self):
        with self.assertRaisesRegex(FixtureError, "bounded_session_poll"):
            ScriptedToolCall(
                "write_stdin",
                {"session_id": "${session_id}", "chars": "x", "yield_time_ms": 10_000},
            )
        with self.assertRaisesRegex(FixtureError, "bounded_session_poll"):
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 30_001}
            )
        with self.assertRaisesRegex(FixtureError, "exec_session_template"):
            PaperclipResponsesFixture(
                port=0,
                tool_script=(
                    ScriptedToolCall("exec_command", {"cmd": "start"}),
                    ScriptedToolCall("write_stdin", {"session_id": 14, "yield_time_ms": 1000}),
                ),
            )
        with self.assertRaisesRegex(FixtureError, "must_poll_prior_exec"):
            PaperclipResponsesFixture(
                port=0,
                tool_script=(
                    ScriptedToolCall("list_agents", {}),
                    ScriptedToolCall(
                        "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 1000}
                    ),
                ),
            )

    def test_exec_session_poll_limit_fails_closed_without_submitting_candidate(self):
        session_id = 27
        script = (
            ScriptedToolCall("exec_command", {"cmd": "bounded recover"}),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall(
                "write_stdin", {"session_id": "${session_id}", "yield_time_ms": 10_000}
            ),
            ScriptedToolCall(
                BRIDGE_SUBMIT_TOOL, {"base_sha": "${base_sha}", "submitted_sha": "${candidate_sha}"}
            ),
        )
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}}
            for name in ("exec_command", "write_stdin", BRIDGE_SUBMIT_TOOL)
        ]
        input_items = [{"role": "user", "content": "bounded recover"}]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                first = json.load(response)
            self.assertEqual(first["output"][0]["call_id"], "call_resp_fixture_000001")
            input_items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000001",
                        "name": "exec_command",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000001",
                        "output": json.dumps(
                            {"output": "", "session_id": session_id, "exit_code": None}
                        ),
                    },
                ]
            )
            for call_number in (2, 3, 4):
                with _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
                ) as response:
                    poll = json.load(response)
                expected_call = f"call_resp_fixture_{call_number:06d}"
                self.assertEqual(poll["output"][0]["call_id"], expected_call)
                input_items.extend(
                    [
                        {"type": "function_call", "call_id": expected_call, "name": "write_stdin"},
                        {
                            "type": "function_call_output",
                            "call_id": expected_call,
                            "output": json.dumps(
                                {
                                    "output": "still running",
                                    "session_id": session_id,
                                    "exit_code": None,
                                }
                            ),
                        },
                    ]
                )
            with self.assertRaises(urllib.error.HTTPError) as error:
                _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
                )
            self.assertEqual(error.exception.code, 422)
            self.assertEqual(
                json.loads(error.exception.read())["error"]["message"],
                "fixture_exec_session_poll_limit_reached",
            )
            health = json.load(
                urllib.request.urlopen(fixture.base_url.removesuffix("/v1") + "/healthz")
            )
            self.assertEqual(health["request_count"], 5)
            self.assertEqual(health["script_progress"][-1], [4, 4])

    def test_candidate_binding_rejects_ambiguous_or_unpaired_outputs(self):
        call = {
            "type": "function_call",
            "call_id": "call_resp_fixture_000001",
            "name": "exec_command",
        }
        output = {
            "type": "function_call_output",
            "call_id": "call_resp_fixture_000001",
            "output": "candidate_sha:" + "a" * 40,
        }
        with self.assertRaisesRegex(FixtureError, "template_missing"):
            _resolve_script_arguments(
                {"sha": "${candidate_sha}"},
                [output],
                (ScriptedToolCall("exec_command", {}),),
            )
        with self.assertRaisesRegex(FixtureError, "template_ambiguous"):
            _resolve_script_arguments(
                {"sha": "${candidate_sha}"},
                [
                    call,
                    output,
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000002",
                        "name": "exec_command",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000002",
                        "output": "candidate_sha:" + "b" * 40,
                    },
                ],
                (
                    ScriptedToolCall("exec_command", {}),
                    ScriptedToolCall("exec_command", {}),
                ),
            )

    def test_reassign_status_version_binds_only_from_exact_search_result_issue(self):
        worker_id = "696e3682-6094-4b5f-8d06-04df10c6dd08"
        other_id = "bcef2b68-b5b6-4f7f-9594-46c57416f201"
        script = (
            ScriptedToolCall("search_tasks", {"query": "BRAA-2"}),
            ScriptedToolCall(
                "reassign_task",
                {"taskId": worker_id, "expectedStatusVersion": "${status_version}"},
            ),
        )
        input_items = [
            {
                "type": "function_call",
                "call_id": "call_resp_fixture_000001",
                "name": "search_tasks",
            },
            {
                "type": "function_call_output",
                "call_id": "call_resp_fixture_000001",
                "output": json.dumps(
                    {
                        "issues": [
                            {"id": other_id, "statusVersion": 14},
                            {"id": worker_id, "statusVersion": 7},
                        ]
                    }
                ),
            },
        ]
        self.assertEqual(
            _resolve_script_arguments(script[1].arguments, input_items, script),
            {"taskId": worker_id, "expectedStatusVersion": 7},
        )
        input_items[1]["output"] = json.dumps({"id": other_id, "statusVersion": 14})
        with self.assertRaisesRegex(FixtureError, "status_version_template_missing"):
            _resolve_script_arguments(script[1].arguments, input_items, script)

    def test_lead_finish_binds_only_after_reassignment_receipt(self):
        worker_id = "696e3682-6094-4b5f-8d06-04df10c6dd08"
        script = (
            ScriptedToolCall("get_task_context", {}),
            ScriptedToolCall("search_tasks", {"query": "BRAA-2"}),
            ScriptedToolCall("reassign_task", {"taskId": worker_id}),
            ScriptedToolCall(
                "paperclip_finish", {"summary": "Handoff revision ${handoff_state_revision}"}
            ),
        )
        input_items = []
        outputs = [
            ("get_task_context", "{}"),
            ("search_tasks", json.dumps({"tasks": [{"id": worker_id, "statusVersion": 8}]})),
            ("reassign_task", json.dumps({"stateRevision": 9, "scheduledWakeIds": ["wake"]})),
        ]
        for number, (name, output) in enumerate(outputs, start=1):
            call_id = f"call_resp_fixture_{number:06d}"
            input_items.extend(
                [
                    {"type": "function_call", "call_id": call_id, "name": name},
                    {"type": "function_call_output", "call_id": call_id, "output": output},
                ]
            )
        self.assertEqual(
            _resolve_script_arguments(script[3].arguments, input_items, script),
            {"summary": "Handoff revision 9"},
        )
        input_items[-1]["output"] = json.dumps({"outcome": "failed", "message": "stale"})
        with self.assertRaisesRegex(FixtureError, "handoff_state_revision_template_missing"):
            _resolve_script_arguments(script[3].arguments, input_items, script)

    def test_reviewer_finish_requires_published_approval_result(self):
        operation_key = "a" * 64
        candidate_sha = "b" * 40
        inspection_digest = "c" * 64
        script = (
            ScriptedToolCall("get_task_context", {}),
            ScriptedToolCall("get_task_history", {"limit": 20}),
            ScriptedToolCall(
                "app_brain_autonomous_bri_inspect_candidate_1b8157ecda7e",
                {"operation_key": "${operation_key}", "candidate_sha": "${candidate_sha}"},
            ),
            ScriptedToolCall(
                "app_brain_autonomous_bri_approve_candidate_35fdd3d22fbd",
                {"operation_key": "${operation_key}", "candidate_sha": "${candidate_sha}"},
            ),
            ScriptedToolCall("paperclip_finish", {"summary": "Bridge status ${approval_status}"}),
        )
        input_items = []
        outputs = [
            ("get_task_context", "{}"),
            (
                "get_task_history",
                f'{{"comments":[{{"body":"operation_key:{operation_key}\\ncandidate_sha:{candidate_sha}"}}]}}',
            ),
            (
                "app_brain_autonomous_bri_inspect_candidate_1b8157ecda7e",
                json.dumps(
                    {
                        "operation_key": operation_key,
                        "candidate_sha": candidate_sha,
                        "inspection_digest": inspection_digest,
                    }
                )
                + "\n\n--- Exact candidate diff ---\nsynthetic diff",
            ),
            (
                "app_brain_autonomous_bri_approve_candidate_35fdd3d22fbd",
                f"status:published\ncandidate_sha:{candidate_sha}",
            ),
        ]
        for number, (name, output) in enumerate(outputs, start=1):
            call_id = f"call_resp_fixture_{number:06d}"
            input_items.extend(
                [
                    {"type": "function_call", "call_id": call_id, "name": name},
                    {"type": "function_call_output", "call_id": call_id, "output": output},
                ]
            )
        self.assertEqual(
            _resolve_script_arguments(script[4].arguments, input_items, script),
            {"summary": "Bridge status published"},
        )
        input_items[-1]["output"] = "status:review_rejected\ncandidate_sha:" + candidate_sha
        with self.assertRaisesRegex(FixtureError, "fixture_candidate_not_published"):
            _resolve_script_arguments(script[4].arguments, input_items, script)
        with self.assertRaisesRegex(FixtureError, "template_missing"):
            _resolve_script_arguments(
                {"sha": "${candidate_sha}"},
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000001",
                        "name": "list_agents",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000001",
                        "output": "candidate_sha:" + "a" * 40,
                    },
                ],
                (ScriptedToolCall("list_agents", {}),),
            )

    def test_tool_call_id_is_bound_to_script_position_after_rejected_request(self):
        script = (
            ScriptedToolCall("list_agents", {}),
            ScriptedToolCall("exec_command", {"cmd": "verify"}),
        )
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}}
            for name in ("list_agents", "exec_command")
        ]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with self.assertRaises(urllib.error.HTTPError):
                _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": "bad first request", "tools": []},
                )
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": "good first request", "tools": tools},
            ) as response:
                first = json.load(response)
            self.assertEqual(first["output"][0]["call_id"], "call_resp_fixture_000001")
            next_input = [
                {
                    "type": "function_call",
                    "call_id": first["output"][0]["call_id"],
                    "name": "list_agents",
                },
                {
                    "type": "function_call_output",
                    "call_id": first["output"][0]["call_id"],
                    "output": "roster",
                },
            ]
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": next_input, "tools": tools},
            ) as response:
                second = json.load(response)
            self.assertEqual(second["output"][0]["name"], "exec_command")
            self.assertEqual(second["output"][0]["call_id"], "call_resp_fixture_000002")

    def test_candidate_template_422_does_not_advance_the_fixture_call_id(self):
        candidate_sha = "d" * 40
        script = (
            ScriptedToolCall("exec_command", {"cmd": "verify candidate"}),
            ScriptedToolCall(
                "app_brain_autonomous_bri_submit_candidate_2362480aea40",
                {"base_sha": "e" * 40, "submitted_sha": "${candidate_sha}"},
            ),
        )
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}}
            for name in ("exec_command", "app_brain_autonomous_bri_submit_candidate_2362480aea40")
        ]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": "verify candidate", "tools": tools},
            ) as response:
                first = json.load(response)
            self.assertEqual(first["output"][0]["call_id"], "call_resp_fixture_000001")

            unbound_input = [
                {
                    "type": "function_call",
                    "call_id": "call_resp_fixture_000001",
                    "name": "exec_command",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_resp_fixture_000001",
                    "output": "verification completed without a candidate SHA",
                },
            ]
            with self.assertRaises(urllib.error.HTTPError) as error:
                _request(
                    fixture.base_url + "/responses",
                    {"model": FIXTURE_MODEL, "input": unbound_input, "tools": tools},
                )
            self.assertEqual(error.exception.code, 422)
            self.assertEqual(
                json.loads(error.exception.read())["error"]["message"],
                "fixture_candidate_sha_template_missing",
            )

            bound_input = [
                *unbound_input[:-1],
                {
                    "type": "function_call_output",
                    "call_id": "call_resp_fixture_000001",
                    "output": f"candidate_sha:{candidate_sha}\n",
                },
            ]
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": bound_input, "tools": tools},
            ) as response:
                submit = json.load(response)
            self.assertEqual(
                submit["output"][0]["name"],
                "app_brain_autonomous_bri_submit_candidate_2362480aea40",
            )
            self.assertEqual(submit["output"][0]["call_id"], "call_resp_fixture_000002")
            self.assertEqual(
                json.loads(submit["output"][0]["arguments"]),
                {"base_sha": "e" * 40, "submitted_sha": candidate_sha},
            )

    def test_worker_script_binds_base_and_candidate_sha_before_submit(self):
        base_sha = "a" * 40
        candidate_sha = "b" * 40
        script = (
            ScriptedToolCall("exec_command", {"cmd": "build candidate"}),
            ScriptedToolCall(
                "app_brain_autonomous_bri_submit_candidate_2362480aea40",
                {"base_sha": "${base_sha}", "submitted_sha": "${candidate_sha}"},
            ),
        )
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}}
            for name in ("exec_command", "app_brain_autonomous_bri_submit_candidate_2362480aea40")
        ]
        input_items = [{"role": "user", "content": "submit and review synthetic work"}]
        with PaperclipResponsesFixture(port=0, tool_script=script) as fixture:
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                build = json.load(response)
            self.assertEqual(build["output"][0]["call_id"], "call_resp_fixture_000001")
            input_items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000001",
                        "name": "exec_command",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000001",
                        "output": f"base_sha:{base_sha}\ncandidate_sha:{candidate_sha}\n",
                    },
                ]
            )
            with _request(
                fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                submit = json.load(response)
            self.assertEqual(submit["output"][0]["call_id"], "call_resp_fixture_000002")
            self.assertEqual(
                json.loads(submit["output"][0]["arguments"]),
                {"base_sha": base_sha, "submitted_sha": candidate_sha},
            )

    def test_worker_finish_binds_submit_result_from_native_json_content_wrapper(self):
        operation_key = "a" * 64
        candidate_sha = "b" * 40
        submit_name = "app_brain_autonomous_bri_submit_candidate_2362480aea40"
        script = (
            ScriptedToolCall(submit_name, {"base_sha": "c" * 40, "submitted_sha": candidate_sha}),
            ScriptedToolCall(
                "paperclip_finish",
                {
                    "summary": "Submitted ${candidate_sha} with operation ${operation_key}",
                    "completionClaim": {"criterionId": "${operation_key}"},
                },
            ),
        )
        input_items = [
            {"type": "function_call", "call_id": "call_resp_fixture_000001", "name": submit_name},
            {
                "type": "function_call_output",
                "call_id": "call_resp_fixture_000001",
                "output": json.dumps(
                    {"content": f"operation_key:{operation_key}\ncandidate_sha:{candidate_sha}"}
                ),
            },
        ]
        self.assertEqual(
            _resolve_script_arguments(script[1].arguments, input_items, script),
            {
                "summary": f"Submitted {candidate_sha} with operation {operation_key}",
                "completionClaim": {"criterionId": operation_key},
            },
        )

    def test_reviewer_inspects_exact_candidate_before_approval(self):
        candidate_sha = "f" * 40
        operation_key = "1" * 64
        inspection_digest = "2" * 64
        script = (
            ScriptedToolCall("get_task_context", {}),
            ScriptedToolCall(
                "app_brain_autonomous_bri_inspect_candidate_1b8157ecda7e",
                {
                    "operation_key": "${operation_key}",
                    "candidate_sha": "${candidate_sha}",
                },
            ),
            ScriptedToolCall(
                "app_brain_autonomous_bri_approve_candidate_35fdd3d22fbd",
                {
                    "operation_key": "${operation_key}",
                    "candidate_sha": "${candidate_sha}",
                    "approved": True,
                    "evidence": {
                        "inspection_digest": "${inspection_digest}",
                        "verdict": "approved",
                        "rationale": "Synthetic diff matches the task.",
                        "reviewed_paths": ["synthetic_candidate.py"],
                    },
                },
            ),
        )
        tools = [
            {"type": "function", "name": name, "parameters": {"type": "object"}}
            for name in (
                "get_task_context",
                "app_brain_autonomous_bri_inspect_candidate_1b8157ecda7e",
                "app_brain_autonomous_bri_approve_candidate_35fdd3d22fbd",
            )
        ]
        context_result = json.dumps(
            {
                "ok": True,
                "result": {
                    "task": {
                        "comments": [
                            {
                                "body": (
                                    f"operation_key:{operation_key}\n"
                                    f"candidate_sha:{candidate_sha}\n"
                                    f"base_sha:{'a' * 40}\n"
                                    f"submitted_sha:{candidate_sha}\n"
                                    f"policy_digest:{'b' * 64}\n"
                                )
                            }
                        ]
                    }
                },
            },
            separators=(",", ":"),
        )
        inspect_metadata = json.dumps(
            {
                "operation_key": operation_key,
                "candidate_sha": candidate_sha,
                "submitted_sha": candidate_sha,
                "base_sha": "a" * 40,
                "integration_tip": "c" * 40,
                "changed_paths": ["synthetic_candidate.py"],
                "policy_digest": "b" * 64,
                "verifier": {
                    "state": "passed",
                    "receipt_digest": "d" * 64,
                    "receipt": {"policy_hash": "b" * 64, "result": "passed"},
                },
                "inspection_digest": inspection_digest,
            },
            indent=2,
        )
        inspect_result = json.dumps(
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            inspect_metadata
                            + "\n--- Exact candidate diff ---\n"
                            + "diff --git a/synthetic_candidate.py b/synthetic_candidate.py\n"
                        ),
                    }
                ]
            },
            separators=(",", ":"),
        )
        input_items = [{"role": "user", "content": "review this synthetic submission"}]
        with PaperclipResponsesFixture(port=0, tool_script=script) as reviewer_fixture:
            with _request(
                reviewer_fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                context = json.load(response)
            self.assertEqual(context["output"][0]["name"], "get_task_context")
            self.assertEqual(context["output"][0]["call_id"], "call_resp_fixture_000001")
            self.assertNotIn("submit_candidate", repr(input_items))
            input_items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000001",
                        "name": "get_task_context",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000001",
                        "output": context_result,
                    },
                ]
            )
            with _request(
                reviewer_fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                inspection = json.load(response)
            self.assertEqual(
                inspection["output"][0]["name"],
                "app_brain_autonomous_bri_inspect_candidate_1b8157ecda7e",
            )
            self.assertEqual(
                json.loads(inspection["output"][0]["arguments"]),
                {"operation_key": operation_key, "candidate_sha": candidate_sha},
            )
            input_items.extend(
                [
                    {
                        "type": "function_call",
                        "call_id": "call_resp_fixture_000002",
                        "name": "app_brain_autonomous_bri_inspect_candidate_1b8157ecda7e",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_resp_fixture_000002",
                        "output": inspect_result,
                    },
                ]
            )
            with _request(
                reviewer_fixture.base_url + "/responses",
                {"model": FIXTURE_MODEL, "input": input_items, "tools": tools},
            ) as response:
                approval = json.load(response)
        self.assertEqual(
            approval["output"][0]["name"],
            "app_brain_autonomous_bri_approve_candidate_35fdd3d22fbd",
        )
        self.assertEqual(approval["output"][0]["call_id"], "call_resp_fixture_000003")
        self.assertEqual(
            json.loads(approval["output"][0]["arguments"]),
            {
                "operation_key": operation_key,
                "candidate_sha": candidate_sha,
                "approved": True,
                "evidence": {
                    "inspection_digest": inspection_digest,
                    "verdict": "approved",
                    "rationale": "Synthetic diff matches the task.",
                    "reviewed_paths": ["synthetic_candidate.py"],
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
