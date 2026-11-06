"""Offline contract and failure-path tests; never start a real Codex run."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


HELPER = Path(__file__).resolve().parents[1] / "skills" / "duel" / "scripts" / "duel.py"
SPEC = importlib.util.spec_from_file_location("duel", HELPER)
duel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(duel)


def packet(round_number=1):
    history = [
        {"round": 1, "critic": "C1: A flat price assumes every team has the same usage.",
         "response": "Revised C1: use a base plan with an explicit usage allowance."},
        {"round": 2, "critic": "C2: The allowance is unsupported without cost data.",
         "response": "Revised C2: measure costs in a pilot before selecting the allowance."},
    ]
    return {
        "task": "Design an initial pricing experiment for a small software product.",
        "answer": "Pilot a base plan and measure cost and usage before choosing its allowance.",
        "round": round_number,
        "history": history[:round_number - 1],
        "context": "Three volunteer teams are available for the pilot.",
        "claims": [{"id": "C1", "text": "Usage affects cost."},
                   {"id": "C2", "text": "The allowance should follow pilot measurements."}],
    }


def codex_events(text="C2 needs pilot data before the allowance is fixed.", extra=()):
    events = [{"type": "thread.started", "thread_id": "thread_test"}, {"type": "turn.started"}]
    events += list(extra)
    events += [{"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Reviewing."}},
               {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}},
               {"type": "turn.completed", "usage": {"input_tokens": 300, "output_tokens": 40}}]
    return events


TOOL_ATTEMPT = {"type": "item.started", "item": {"id": "x", "type": "command_execution", "command": "dir"}}


class FakeCodex:
    """Stands in for a Codex process: records stdin and replays JSONL events."""

    def __init__(self, events, returncode=0):
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        self.stdout = io.BytesIO(b"".join(json.dumps(event).encode("utf-8") + b"\n" for event in events))
        self.returncode = returncode
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self):
        return self.returncode


class OfflineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.packet_path = self.directory / "packet.json"
        self.output_path = self.directory / "result.json"
        self.packet_path.write_text(json.dumps(packet()), encoding="utf-8")
        self.environment = mock.patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # All tests are offline even if an unexpected code path is introduced.
        self.popen_patch = mock.patch.object(duel.subprocess, "Popen",
                                             side_effect=AssertionError("Unexpected Codex run in offline test"))
        self.popen = self.popen_patch.start()
        self.addCleanup(self.popen_patch.stop)
        self.which_patch = mock.patch.object(duel.shutil, "which", return_value="C:/codex/codex.exe")
        self.which = self.which_patch.start()
        self.addCleanup(self.which_patch.stop)

    def run_cli(self, *extra, command="critique"):
        arguments = [command]
        if command == "critique":
            arguments += ["--packet", str(self.packet_path), "--output", str(self.output_path)]
        arguments += list(extra)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = duel.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def serve(self, events=None, returncode=0):
        process = FakeCodex(codex_events() if events is None else events, returncode)
        self.popen.side_effect = None
        self.popen.return_value = process
        return process

    def write_packet(self, value):
        self.packet_path.write_text(json.dumps(value), encoding="utf-8")


class PacketTests(OfflineTest):
    def test_realistic_three_round_histories_validate(self):
        for round_number in range(1, 4):
            value = packet(round_number)
            self.assertIs(duel.validate_packet(value), value)

    def test_minimal_packet_and_optional_empty_values(self):
        value = {"task": "Task", "answer": "Answer", "round": 1, "history": []}
        duel.validate_packet(value)
        value.update(context="", claims=[])
        duel.validate_packet(value)

    def test_schema_rejects_invalid_packet_without_echoing_content(self):
        cases = [
            [],
            {key: value for key, value in packet().items() if key != "task"},
            dict(packet(), unrelated="PRIVATE INPUT"),
            dict(packet(), task="  "),
            dict(packet(), answer=23),
            dict(packet(), round=True),
            dict(packet(), round=0),
            dict(packet(), round=4),
            dict(packet(), round=1.0),
            dict(packet(), history={}),
            dict(packet(3), history=packet(2)["history"]),
            dict(packet(), context=[]),
            dict(packet(), claims={}),
            dict(packet(), claims=[{"id": "C1", "text": "PRIVATE INPUT", "extra": "x"}]),
            dict(packet(), claims=[{"id": "", "text": "PRIVATE INPUT"}]),
            dict(packet(), claims=[{"id": "C1", "text": "one"}, {"id": "C1", "text": "two"}]),
        ]
        for value in cases:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(duel.DuelError) as caught:
                    duel.validate_packet(value)
                self.assertNotIn("PRIVATE INPUT", str(caught.exception))

    def test_history_order_and_entries_must_be_complete(self):
        cases = [
            [{"round": 2, "critic": "x", "response": "y"}],
            [{"round": True, "critic": "x", "response": "y"}],
            [{"round": 1, "critic": "", "response": "y"}],
            [{"round": 1, "critic": "x", "response": None}],
            [{"round": 1, "critic": "x"}],
            ["PRIVATE INPUT"],
        ]
        for history in cases:
            with self.subTest(history=history):
                with self.assertRaises(duel.DuelError):
                    duel.validate_packet(dict(packet(2), history=history))
        value = packet(3)
        value["history"].reverse()
        with self.assertRaises(duel.DuelError):
            duel.validate_packet(value)

    def test_character_limit_counts_history_context_and_claims(self):
        value = {"task": "t", "answer": "a" * (duel.MAX_TEXT_CHARACTERS - 1), "round": 1, "history": []}
        duel.validate_packet(value)
        for field, extra in [("context", "x"), ("claims", [{"id": "C1", "text": "x"}])]:
            with self.subTest(field=field):
                with self.assertRaisesRegex(duel.DuelError, "100000"):
                    duel.validate_packet(dict(value, **{field: extra}))
        value = {"task": "t", "answer": "a", "round": 2,
                 "history": [{"round": 1, "critic": "x" * 100000, "response": "y"}]}
        with self.assertRaisesRegex(duel.DuelError, "100000"):
            duel.validate_packet(value)

    def test_byte_limit_is_independent_of_character_limit(self):
        # Multibyte text is under the character limit but over the byte limit.
        value = dict(packet(), answer="猫" * 50000)
        self.packet_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(duel.DuelError, "128 KiB"):
            duel.read_packet(self.packet_path)

    def test_file_limit_exact_boundary(self):
        raw = json.dumps(packet()).encode("utf-8")
        self.packet_path.write_bytes(raw + b" " * (duel.MAX_PACKET_BYTES - len(raw)))
        self.assertEqual(duel.read_packet(self.packet_path), packet())
        with self.packet_path.open("ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(duel.DuelError, "128 KiB"):
            duel.read_packet(self.packet_path)

    def test_rejects_invalid_utf8_json_duplicates_and_nonfinite_numbers(self):
        for raw in [b"\xff", b"{bad JSON}", b'{"task":"a","task":"b"}', b'{"round":NaN}', b"[" * 2000]:
            with self.subTest(raw_length=len(raw)):
                self.packet_path.write_bytes(raw)
                with self.assertRaises(duel.DuelError):
                    duel.read_packet(self.packet_path)

    def test_rejects_unpaired_surrogate(self):
        self.write_packet(dict(packet(), answer="\ud800"))
        with self.assertRaisesRegex(duel.DuelError, "Unicode"):
            duel.read_packet(self.packet_path)

    def test_unreadable_packet_has_safe_error(self):
        with self.assertRaisesRegex(duel.DuelError, "could not be read"):
            duel.read_packet(self.directory / "absent-private-name.json")


class ConfigurationTests(OfflineTest):
    def test_explicit_model_overrides_environment(self):
        os.environ["DUEL_OPENAI_MODEL"] = "environment-model"
        self.assertEqual(duel.configured_model("explicit-model"), "explicit-model")
        self.assertEqual(duel.configured_model(), "environment-model")

    def test_default_model_and_invalid_model_do_not_echo_value(self):
        self.assertEqual(duel.configured_model(), "gpt-6-astra")
        for value in ["", "PRIVATE MODEL\n", "https://example.com", "m" * 129]:
            with self.assertRaises(duel.DuelError) as caught:
                duel.configured_model(value)
            self.assertNotIn("PRIVATE MODEL", str(caught.exception))

    def test_command_is_read_only_ephemeral_and_keeps_packet_off_the_command_line(self):
        value = dict(packet(), answer="PRIVATE ANSWER")
        command = duel.build_codex_command("codex", "/empty", "C:\\temp\\critic.md", "chosen-model")
        self.assertEqual(command[:2], ["codex", "exec"])
        for flag in ["--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"]:
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("--cd") + 1], "/empty")
        self.assertEqual(command[command.index("--model") + 1], "chosen-model")
        disabled = {command[i + 1] for i, part in enumerate(command) if part == "--disable"}
        self.assertTrue({"shell_tool", "unified_exec", "computer_use", "browser_use"} <= disabled)
        settings = {command[i + 1] for i, part in enumerate(command) if part == "-c"}
        self.assertIn('web_search="disabled"', settings)
        self.assertIn("skills.include_instructions=false", settings)
        self.assertTrue(set(duel.CODEX_PROMPT_TRIMS) <= settings)
        self.assertEqual(command[-1], "-")
        instructions = next(part for part in settings if part.startswith("model_instructions_file="))
        # TOML basic string: the Windows path survives with its backslashes.
        self.assertEqual(json.loads(instructions.split("=", 1)[1]), "C:\\temp\\critic.md")
        self.assertNotIn("PRIVATE ANSWER", " ".join(command))
        self.assertIn("PRIVATE ANSWER", duel.codex_prompt(value))

    def test_environment_passes_only_named_variables(self):
        os.environ.update(OPENAI_API_KEY="secret", CODEX_API_KEY="secret", OPENAI_BASE_URL="https://untrusted.invalid",
                          UNRELATED_TOKEN="secret", PATH="kept", CODEX_HOME="kept-home")
        environment = duel.codex_environment()
        self.assertEqual(environment, {"PATH": "kept", "CODEX_HOME": "kept-home"})

    def test_flag_bounds_fail_before_codex(self):
        for value in ["0", "601", "nan", "inf"]:
            with self.subTest(timeout=value):
                code, _, _ = self.run_cli("--timeout", value)
                self.assertEqual(code, 1)
                self.assertFalse(self.output_path.exists())
                self.popen.assert_not_called()

    def test_dry_run_needs_no_codex_and_shows_command_instructions_and_stdin(self):
        self.which.return_value = None
        code, stdout, stderr = self.run_cli("--dry-run")
        self.assertEqual((code, stderr), (0, ""))
        result = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["request"]["command"][:2], ["codex", "exec"])
        self.assertEqual(result["request"]["instructions"], duel.critic_instructions())
        self.assertIn(json.dumps(packet(), separators=(",", ":")), result["request"]["stdin"])
        self.assertEqual(json.loads(stdout)["status"], "dry_run")
        self.popen.assert_not_called()

    def test_check_reports_codex_state_without_running_a_critique(self):
        with mock.patch.object(duel.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=b"codex-cli 9.9.9\n")
            code, stdout, _ = self.run_cli(command="check")
            self.assertEqual(code, 0)
            result = json.loads(stdout)
            self.assertEqual((result["status"], result["codex_version"]), ("ready", "codex-cli 9.9.9"))
            self.assertFalse(result["network_check"])
            run.return_value = mock.Mock(returncode=1, stdout=b"")
            code, stdout, _ = self.run_cli(command="check")
            self.assertEqual((code, json.loads(stdout)["status"]), (1, "not_logged_in"))
        self.which.return_value = None
        code, stdout, _ = self.run_cli(command="check")
        self.assertEqual((code, json.loads(stdout)["status"]), (1, "missing_codex"))
        self.popen.assert_not_called()


class EventTests(OfflineTest):
    def test_parses_last_agent_message_usage_and_unverified_model(self):
        result = duel.parse_codex_events(codex_events(), 2, "requested-model")
        self.assertEqual(set(result), {"status", "round", "model", "model_verified", "response_id", "critique", "usage"})
        self.assertEqual(result["critique"], "C2 needs pilot data before the allowance is fixed.")
        self.assertEqual(result["response_id"], "thread_test")
        self.assertEqual(result["usage"]["input_tokens"], 300)
        self.assertEqual((result["round"], result["model"]), (2, "requested-model"))
        self.assertIs(result["model_verified"], False)

    def test_absent_usage_defaults_to_empty_object(self):
        events = codex_events()
        events[-1] = {"type": "turn.completed"}
        self.assertEqual(duel.parse_codex_events(events, 1, "m")["usage"], {})

    def test_warning_items_are_tolerated(self):
        warning = {"type": "item.completed", "item": {"id": "w", "type": "error", "message": "config notice"}}
        self.assertEqual(duel.parse_codex_events(codex_events(extra=[warning]), 1, "m")["status"], "completed")

    def test_failures_map_to_safe_messages(self):
        cases = [("The 'x' model requires a newer version of Codex. PRIVATE", "codex update"),
                 ("The 'x' model is not supported when using Codex with a ChatGPT account. PRIVATE", "not available"),
                 ("status 401 Unauthorized PRIVATE", "codex login"),
                 ("You've hit your usage limit. PRIVATE", "limit"),
                 ("PRIVATE unexpected failure", "did not complete")]
        for message, expected in cases:
            with self.subTest(expected=expected):
                events = [{"type": "thread.started", "thread_id": "t"},
                          {"type": "turn.failed", "error": {"message": message}}]
                with self.assertRaisesRegex(duel.DuelError, expected) as caught:
                    duel.parse_codex_events(events, 1, "m")
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertIn("No automatic retry", str(caught.exception))

    def test_incomplete_empty_and_malformed_runs_rejected(self):
        cases = [codex_events()[:-1], codex_events(" \n "), codex_events()[1:], [None],
                 codex_events() + [{"type": "error", "message": "stream failed"}],
                 [dict(event, usage=[]) if event["type"] == "turn.completed" else event for event in codex_events()]]
        for events in cases:
            with self.assertRaises(duel.DuelError):
                duel.parse_codex_events(events, 1, "m")

    def test_tool_attempt_discards_critique(self):
        with self.assertRaisesRegex(duel.DuelError, "tool call"):
            duel.parse_codex_events(codex_events(extra=[TOOL_ATTEMPT]), 1, "m")


class RunTests(OfflineTest):
    def test_critique_runs_codex_once_with_packet_on_stdin(self):
        os.environ["OPENAI_API_KEY"] = "test-secret"
        process = self.serve()
        seen = {}

        def inspect_run(command, **kwargs):
            setting = next(part for part in command if part.startswith("model_instructions_file="))
            seen["instructions"] = Path(json.loads(setting.split("=", 1)[1])).read_text(encoding="utf-8")
            seen["workdir"] = os.listdir(command[command.index("--cd") + 1])
            return process

        self.popen.side_effect = inspect_run
        code, stdout, stderr = self.run_cli("--model", "chosen-model", "--timeout", "12")
        self.assertEqual((code, stderr), (0, ""))
        self.popen.assert_called_once()
        self.assertEqual(seen, {"instructions": duel.critic_instructions(), "workdir": []})
        command = self.popen.call_args.args[0]
        self.assertEqual(command[0], "C:/codex/codex.exe")
        self.assertEqual(command[command.index("--model") + 1], "chosen-model")
        self.assertNotIn("OPENAI_API_KEY", self.popen.call_args.kwargs["env"])
        self.assertEqual(self.popen.call_args.kwargs["cwd"], command[command.index("--cd") + 1])
        submitted = process.stdin.getvalue().decode("utf-8").split("\n\n", 1)[1]
        self.assertEqual(json.loads(submitted), packet())
        result = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(json.loads(stdout), {"status": "completed", "round": 1, "model": "chosen-model"})

    def test_tool_attempt_stops_process_and_saves_nothing(self):
        process = self.serve(codex_events(extra=[TOOL_ATTEMPT]))
        code, _, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertTrue(process.killed)
        self.assertIn("tool call", stderr)
        self.assertFalse(self.output_path.exists())

    def test_invalid_event_json_is_safe(self):
        process = self.serve([])
        process.stdout = io.BytesIO(b"PRIVATE NOT JSON\n")
        code, stdout, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("invalid JSON", stderr)
        self.assertNotIn("PRIVATE", stdout + stderr)
        self.assertFalse(self.output_path.exists())

    def test_event_output_size_is_bounded(self):
        process = self.serve([])
        process.stdout = io.BytesIO(b"x" * (duel.MAX_RESPONSE_BYTES + 1) + b"\n")
        code, _, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("supported size", stderr)
        self.assertTrue(process.killed)

    def test_start_failure_is_safe(self):
        self.popen.side_effect = OSError("PRIVATE")
        code, stdout, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("could not be started", stderr)
        self.assertNotIn("PRIVATE", stdout + stderr)
        self.assertFalse(self.output_path.exists())

    def test_missing_codex_fails_before_reserving_output(self):
        self.which.return_value = None
        code, _, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("Nothing was sent", stderr)
        self.assertFalse(self.output_path.exists())
        self.popen.assert_not_called()

    def test_existing_output_never_overwritten_or_sent(self):
        self.output_path.write_text("preserve me", encoding="utf-8")
        for arguments in [[], ["--dry-run"]]:
            code, _, stderr = self.run_cli(*arguments)
            self.assertEqual(code, 1)
            self.assertIn("already exists", stderr)
            self.assertEqual(self.output_path.read_text(encoding="utf-8"), "preserve me")
            self.popen.assert_not_called()

    def test_dangling_symlink_output_never_overwritten_or_sent(self):
        target = self.directory / "nonexistent-target.json"
        self.output_path.symlink_to(target)
        code, _, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("already exists", stderr)
        self.assertFalse(target.exists())
        self.popen.assert_not_called()

    def test_missing_output_directory_fails_before_codex_run(self):
        self.output_path = self.directory / "absent-directory" / "out.json"
        code, _, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("Nothing was sent", stderr)
        self.popen.assert_not_called()

    def test_output_is_reserved_before_codex_run(self):
        process = self.serve()

        def inspect_reservation(*args, **kwargs):
            self.assertTrue(self.output_path.is_file())
            self.assertEqual(self.output_path.stat().st_size, 0)
            return process

        self.popen.side_effect = inspect_reservation
        code, _, stderr = self.run_cli()
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(self.output_path.read_text(encoding="utf-8"))["status"], "completed")

    @unittest.skipIf(os.name == "nt", "POSIX file mode check")
    def test_output_is_private_by_default(self):
        code, _, stderr = self.run_cli("--dry-run")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(self.output_path.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "Windows cannot unlink a file that is still open")
    def test_failed_call_does_not_delete_another_process_replacement(self):
        with self.assertRaises(duel.DuelError):
            with duel.reserve_output(self.output_path):
                self.output_path.unlink()
                self.output_path.write_text("another writer", encoding="utf-8")
                raise duel.DuelError("failure")
        self.assertEqual(self.output_path.read_text(encoding="utf-8"), "another writer")

    def test_three_round_flow_preserves_explicit_history_and_stable_claim_ids(self):
        for round_number in range(1, 4):
            self.write_packet(packet(round_number))
            self.output_path = self.directory / "round-{}.json".format(round_number)
            process = self.serve(codex_events("Round {} critique: verify C2 with pilot data.".format(round_number)))
            code, _, stderr = self.run_cli()
            self.assertEqual((code, stderr), (0, ""))
            submitted = json.loads(process.stdin.getvalue().decode("utf-8").split("\n\n", 1)[1])
            self.assertEqual(submitted, packet(round_number))
            self.assertEqual([claim["id"] for claim in submitted["claims"]], ["C1", "C2"])
            result = json.loads(self.output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["round"], round_number)
            self.assertIn("Round {}".format(round_number), result["critique"])
        self.assertEqual(self.popen.call_count, 3)
        self.assertEqual(sorted(path.name for path in self.directory.iterdir()),
                         ["packet.json", "round-1.json", "round-2.json", "round-3.json"])


if __name__ == "__main__":
    unittest.main()
