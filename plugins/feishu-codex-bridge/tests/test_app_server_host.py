from __future__ import annotations

from hashlib import sha256
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import math
import subprocess
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "app_server_host.py"
SPEC = importlib.util.spec_from_file_location("app_server_host", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("app_server_host module could not be loaded")
APP_SERVER_HOST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP_SERVER_HOST)


def _static_pass() -> dict:
    return {
        "status": "pass",
        "read_only_mvp_protocol_available": True,
        "ephemeral_thread_path_nullable": True,
        "desktop_task_coordination_certified": False,
        "activation_allowed": False,
    }


def _core_pass() -> dict:
    return {
        "schema_version": 2,
        "command": "app-server-read-only-probe",
        "status": "pass",
        "failure_phase": "none",
        "control_thread_started": True,
        "control_thread_ephemeral": True,
        "codex_app_connected": True,
        "list_threads_available": True,
        "list_projects_available": True,
        "list_threads_result_valid": True,
        "list_projects_result_valid": True,
        "control_thread_hidden_from_desktop_catalog": True,
        "model_turn_started": False,
        "responder_mutation_attempted": False,
        "queue_claimed": False,
        "activation_allowed": False,
        "desktop_task_coordination_certified": False,
        "runtime_attestation_passed": False,
        "issues": [],
    }


def _core_fail() -> dict:
    result = _core_pass()
    result.update(
        {
            "status": "fail",
            "failure_phase": "mcp_status",
            "codex_app_connected": False,
            "list_threads_available": False,
            "list_projects_available": False,
            "list_threads_result_valid": False,
            "list_projects_result_valid": False,
            "control_thread_hidden_from_desktop_catalog": False,
            "issues": ["sensitive-value"],
        }
    )
    return result


class _FakeStdin(io.BytesIO):
    def __init__(self, process: "_FakeProcess", *, exit_on_close: bool) -> None:
        super().__init__()
        self._process = process
        self._exit_on_close = exit_on_close

    def close(self) -> None:
        if self._exit_on_close:
            self._process.returncode = 0
        super().close()


class _FakeProcess:
    def __init__(self, *, exit_on_stdin_close: bool = True) -> None:
        self.returncode: int | None = None
        self._handle = 123
        self.stdin = _FakeStdin(self, exit_on_close=exit_on_stdin_close)
        self.stdout = io.BytesIO(b"")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("codex.exe", timeout)
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 1

    def kill(self) -> None:
        self.returncode = 1


class _FakeJob:
    def __init__(self) -> None:
        self.process: _FakeProcess | None = None
        self.assigned = False
        self.terminated = False
        self.resumed = False
        self.closed = False

    def assign(self, process) -> None:
        self.process = process
        self.assigned = True

    def terminate(self) -> None:
        self.terminated = True
        if self.process is not None:
            self.process.returncode = 1

    def resume(self, process) -> None:
        if process is not self.process or not self.assigned:
            raise OSError("resume_before_assign")
        self.resumed = True

    def close(self) -> None:
        self.closed = True


class _RecordingPins:
    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.closed = False

    def pin(self, paths: list[Path]) -> None:
        self.paths.extend(paths)

    def close(self) -> None:
        self.closed = True


class _BlockingStdout:
    def __init__(self) -> None:
        self.release = Event()

    def readline(self, _limit: int) -> bytes:
        self.release.wait(5)
        return b""


class _TransportProcess:
    def __init__(self, stdout) -> None:
        self.stdin = io.BytesIO()
        self.stdout = stdout


class AppServerHostTests(unittest.TestCase):
    def _paths(self, root: Path):
        executable = root / "codex.exe"
        executable.write_bytes(b"exact-native-codex")
        schema = root / "protocol"
        schema.mkdir()
        manifest = root / "desktop-mcp.json"
        manifest.write_text("{}", encoding="utf-8")
        server = root / "server.mjs"
        server.write_text("// static", encoding="utf-8")
        cwd = root / "probe"
        cwd.mkdir()
        return executable, schema, manifest, server, cwd

    def _run(self, root: Path, *, process: _FakeProcess | None = None):
        executable, schema, manifest, server, cwd = self._paths(root)
        observed: list[tuple[list[str], dict]] = []
        child = process or _FakeProcess()
        job = _FakeJob()

        def process_factory(argv, **kwargs):
            observed.append((argv, kwargs))
            return child

        with (
            mock.patch.object(APP_SERVER_HOST, "audit_contract", return_value=_static_pass()),
            mock.patch.object(APP_SERVER_HOST, "run_read_only_probe", return_value=_core_pass()),
        ):
            result = APP_SERVER_HOST.run_live_read_only_probe(
                codex_executable=executable,
                expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                probe_cwd=cwd,
                schema_root=schema,
                desktop_mcp_manifest=manifest,
                desktop_mcp_server=server,
                hard_timeout_seconds=5.0,
                process_factory=process_factory,
                job_factory=lambda: job,
            )
        return result, observed, child, job

    def test_core_success_fails_closed_without_runtime_source_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result, observed, child, job = self._run(Path(temp_dir))

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["failure_phase"], "runtime_provenance")
        self.assertTrue(result["probe_cwd_absolute"])
        self.assertTrue(result["probe_cwd_empty"])
        self.assertTrue(result["probe_cwd_identity_verified"])
        self.assertTrue(result["probe_cwd_no_reparse"])
        self.assertFalse(result["runtime_attestation_passed"])
        self.assertFalse(result["read_only_desktop_task_coordination_attested"])
        self.assertFalse(result["desktop_task_coordination_certified"])
        self.assertTrue(result["app_server_process_started"])
        self.assertTrue(result["app_server_process_stopped"])
        self.assertTrue(result["owned_job_assigned_before_resume"])
        self.assertFalse(result["activation_allowed"])
        self.assertFalse(result["queue_claimed"])
        self.assertFalse(result["responder_mutation_attempted"])
        self.assertFalse(result["fallback_attempted"])
        self.assertEqual(result["issues"], ["runtime_provenance_unavailable"])
        provenance = result["provenance"]
        self.assertEqual(provenance["recipe"], APP_SERVER_HOST.PROVENANCE_RECIPE)
        self.assertTrue(provenance["static_inputs_bound"])
        self.assertFalse(provenance["runtime_source_bound"])
        self.assertFalse(provenance["bound"])
        for name in (
            "attestation_host_sha256",
            "probe_core_sha256",
            "static_auditor_sha256",
            "codex_executable_sha256",
            "schema_tree_bytes_sha256",
            "schema_tree_canonical_sha256",
            "desktop_mcp_manifest_sha256",
            "desktop_mcp_manifest_canonical_sha256",
            "desktop_mcp_server_sha256",
            "static_contract_canonical_sha256",
            "binding_sha256",
        ):
            self.assertRegex(provenance[name], r"^[0-9a-f]{64}$")
        binding_material = {
            key: value
            for key, value in provenance.items()
            if key not in {"bound", "binding_sha256"}
        }
        self.assertEqual(
            provenance["binding_sha256"],
            APP_SERVER_HOST._canonical_sha256(binding_material),
        )
        self.assertNotIn(temp_dir, str(result))
        self.assertEqual(len(observed), 1)
        argv, kwargs = observed[0]
        self.assertEqual(
            argv[1:], ["app-server", "--listen", "stdio://"]
        )
        self.assertTrue(argv[0].endswith("codex.exe"))
        self.assertIsNone(kwargs["env"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(
            kwargs["creationflags"]
            & getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
        )
        self.assertTrue(job.assigned)
        self.assertTrue(job.resumed)
        self.assertTrue(job.closed)
        self.assertEqual(child.returncode, 0)

    def test_exact_probe_cwd_is_in_the_shutdown_lifetime_pin_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pins = _RecordingPins()
            with mock.patch.object(
                APP_SERVER_HOST,
                "WindowsSourcePins",
                return_value=pins,
            ):
                result, _observed, _child, _job = self._run(root)
            expected_cwd = (root / "probe").resolve(strict=True)
            self.assertIn(expected_cwd, pins.paths)
            self.assertTrue(pins.closed)
        self.assertEqual(result["failure_phase"], "runtime_provenance")

    def test_late_probe_cwd_fill_fails_post_shutdown_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            child = _FakeProcess()
            job = _FakeJob()
            pins = _RecordingPins()

            def fill_cwd_after_resume(*_args, **_kwargs):
                (cwd / "late-fill.txt").write_text("late", encoding="utf-8")
                return _core_pass()

            with (
                mock.patch.object(APP_SERVER_HOST, "audit_contract", return_value=_static_pass()),
                mock.patch.object(
                    APP_SERVER_HOST,
                    "run_read_only_probe",
                    side_effect=fill_cwd_after_resume,
                ),
                mock.patch.object(
                    APP_SERVER_HOST,
                    "WindowsSourcePins",
                    return_value=pins,
                ),
            ):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    hard_timeout_seconds=5.0,
                    process_factory=lambda *_args, **_kwargs: child,
                    job_factory=lambda: job,
                )

        self.assertEqual(result["failure_phase"], "runtime_validation")
        self.assertEqual(result["issues"], ["runtime_attestation_invalid"])
        self.assertTrue(result["app_server_process_stopped"])
        self.assertTrue(result["control_thread_started"])
        self.assertFalse(result["runtime_attestation_passed"])
        self.assertTrue(pins.closed)

    def test_static_source_mutation_after_resume_fails_post_shutdown_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            child = _FakeProcess()
            job = _FakeJob()
            pins = _RecordingPins()

            def mutate_source_after_resume(*_args, **_kwargs):
                server.write_text("// changed during probe", encoding="utf-8")
                return _core_pass()

            with (
                mock.patch.object(APP_SERVER_HOST, "audit_contract", return_value=_static_pass()),
                mock.patch.object(
                    APP_SERVER_HOST,
                    "run_read_only_probe",
                    side_effect=mutate_source_after_resume,
                ),
                mock.patch.object(
                    APP_SERVER_HOST,
                    "WindowsSourcePins",
                    return_value=pins,
                ),
            ):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    hard_timeout_seconds=5.0,
                    process_factory=lambda *_args, **_kwargs: child,
                    job_factory=lambda: job,
                )

        self.assertEqual(result["failure_phase"], "runtime_validation")
        self.assertEqual(result["issues"], ["runtime_attestation_invalid"])
        self.assertTrue(result["app_server_process_stopped"])
        self.assertTrue(result["control_thread_started"])
        self.assertFalse(result["runtime_attestation_passed"])
        self.assertTrue(pins.closed)

    def test_preflight_hash_failure_never_spawns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            calls = 0

            def forbidden_factory(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                raise AssertionError("must not spawn")

            result = APP_SERVER_HOST.run_live_read_only_probe(
                codex_executable=executable,
                expected_codex_sha256="0" * 64,
                probe_cwd=cwd,
                schema_root=schema,
                desktop_mcp_manifest=manifest,
                desktop_mcp_server=server,
                process_factory=forbidden_factory,
                job_factory=_FakeJob,
            )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["issues"], ["host_preflight_failed"])
        self.assertEqual(result["failure_phase"], "static_contract")
        self.assertFalse(result["app_server_process_started"])
        self.assertEqual(calls, 0)

    def test_non_finite_timeout_is_rejected_without_spawning(self) -> None:
        for timeout in (math.nan, math.inf, -math.inf):
            with self.subTest(timeout=timeout):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=Path("unused.exe"),
                    expected_codex_sha256="0" * 64,
                    probe_cwd=Path("unused"),
                    schema_root=Path("unused-schema"),
                    desktop_mcp_manifest=Path("unused-manifest"),
                    desktop_mcp_server=Path("unused-server"),
                    hard_timeout_seconds=timeout,
                    process_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("must not spawn")
                    ),
                    job_factory=_FakeJob,
                )
                self.assertEqual(result["issues"], ["host_timeout_invalid"])
                self.assertFalse(result["app_server_process_started"])

    def test_invalid_cli_arguments_emit_one_answer_free_json_object(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = APP_SERVER_HOST.main([])
        lines = [line for line in stdout.getvalue().splitlines() if line]
        self.assertEqual(exit_code, 2)
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["failure_phase"], "host_input")
        self.assertEqual(payload["issues"], ["host_input_invalid"])
        self.assertEqual(stderr.getvalue(), "")

    def test_core_failure_is_normalized_without_remote_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            child = _FakeProcess()
            with (
                mock.patch.object(APP_SERVER_HOST, "audit_contract", return_value=_static_pass()),
                mock.patch.object(
                    APP_SERVER_HOST,
                    "run_read_only_probe",
                    return_value=_core_fail(),
                ),
            ):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    hard_timeout_seconds=5.0,
                    process_factory=lambda *_args, **_kwargs: child,
                    job_factory=_FakeJob,
                )
        self.assertEqual(result["issues"], ["desktop_task_port_unavailable"])
        self.assertEqual(result["failure_phase"], "mcp_status")
        self.assertTrue(result["control_thread_started"])
        self.assertTrue(result["control_thread_ephemeral"])
        self.assertFalse(result["codex_app_connected"])
        self.assertNotIn("sensitive-value", str(result))

    def test_provenance_is_canonical_and_binds_server_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, _cwd = self._paths(root)
            expected = sha256(executable.read_bytes()).hexdigest()
            with mock.patch.object(
                APP_SERVER_HOST,
                "audit_contract",
                return_value=_static_pass(),
            ):
                first = APP_SERVER_HOST._static_preflight(
                    executable=executable,
                    expected_codex_sha256=expected,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                )
                manifest.write_text("{\n}\n", encoding="utf-8")
                canonical_equivalent = APP_SERVER_HOST._static_preflight(
                    executable=executable,
                    expected_codex_sha256=expected,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                )
                server.write_text("// changed static bytes", encoding="utf-8")
                server_changed = APP_SERVER_HOST._static_preflight(
                    executable=executable,
                    expected_codex_sha256=expected,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                )

        self.assertEqual(
            first["desktop_mcp_manifest_canonical_sha256"],
            canonical_equivalent["desktop_mcp_manifest_canonical_sha256"],
        )
        self.assertNotEqual(
            first["desktop_mcp_manifest_sha256"],
            canonical_equivalent["desktop_mcp_manifest_sha256"],
        )
        self.assertNotEqual(
            first["binding_sha256"],
            canonical_equivalent["binding_sha256"],
        )
        self.assertNotEqual(
            canonical_equivalent["desktop_mcp_server_sha256"],
            server_changed["desktop_mcp_server_sha256"],
        )
        self.assertNotEqual(
            canonical_equivalent["binding_sha256"],
            server_changed["binding_sha256"],
        )

    def test_probe_cwd_must_be_absolute_empty_and_not_reparse(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            common = {
                "codex_executable": executable,
                "expected_codex_sha256": sha256(executable.read_bytes()).hexdigest(),
                "schema_root": schema,
                "desktop_mcp_manifest": manifest,
                "desktop_mcp_server": server,
                "process_factory": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("must not spawn")
                ),
                "job_factory": _FakeJob,
            }

            relative = APP_SERVER_HOST.run_live_read_only_probe(
                probe_cwd=Path("relative-probe"),
                **common,
            )
            self.assertEqual(relative["failure_phase"], "probe_cwd")
            self.assertFalse(relative["probe_cwd_absolute"])

            (cwd / "leftover.txt").write_text("stale", encoding="utf-8")
            nonempty = APP_SERVER_HOST.run_live_read_only_probe(
                probe_cwd=cwd,
                **common,
            )
            self.assertTrue(nonempty["probe_cwd_absolute"])
            self.assertTrue(nonempty["probe_cwd_no_reparse"])
            self.assertFalse(nonempty["probe_cwd_empty"])
            (cwd / "leftover.txt").unlink()

            with mock.patch.object(
                APP_SERVER_HOST,
                "_path_is_reparse_point",
                side_effect=lambda path: path == cwd,
            ):
                reparse = APP_SERVER_HOST.run_live_read_only_probe(
                    probe_cwd=cwd,
                    **common,
                )
            self.assertTrue(reparse["probe_cwd_absolute"])
            self.assertFalse(reparse["probe_cwd_no_reparse"])
            self.assertFalse(reparse["probe_cwd_empty"])

    def test_probe_cwd_rejects_reparse_in_parent_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            cwd_parent = root / "cwd-parent"
            cwd_parent.mkdir()
            nested_cwd = cwd_parent / "probe"
            cwd.rename(nested_cwd)
            cwd = nested_cwd
            observed: list[Path] = []

            def parent_is_reparse(path: Path) -> bool:
                observed.append(path)
                return path == cwd_parent

            with mock.patch.object(
                APP_SERVER_HOST,
                "_path_is_reparse_point",
                side_effect=parent_is_reparse,
            ):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    process_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("must not spawn")
                    ),
                    job_factory=_FakeJob,
                )
        self.assertEqual(result["failure_phase"], "probe_cwd")
        self.assertFalse(result["probe_cwd_no_reparse"])
        self.assertIn(cwd_parent, observed)

    def test_static_inputs_must_be_absolute_and_have_no_reparse_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            expected = sha256(executable.read_bytes()).hexdigest()

            relative = APP_SERVER_HOST.run_live_read_only_probe(
                codex_executable=executable,
                expected_codex_sha256=expected,
                probe_cwd=cwd,
                schema_root=Path("relative-schema"),
                desktop_mcp_manifest=manifest,
                desktop_mcp_server=server,
                process_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("must not spawn")
                ),
                job_factory=_FakeJob,
            )
            self.assertEqual(relative["failure_phase"], "static_contract")
            self.assertEqual(relative["issues"], ["host_preflight_failed"])

            original_reparse = APP_SERVER_HOST._path_is_reparse_point

            def schema_is_reparse(path: Path) -> bool:
                if path == schema:
                    return True
                return original_reparse(path)

            with mock.patch.object(
                APP_SERVER_HOST,
                "_path_is_reparse_point",
                side_effect=schema_is_reparse,
            ):
                reparse = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=expected,
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    process_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("must not spawn")
                    ),
                    job_factory=_FakeJob,
                )
            self.assertEqual(reparse["failure_phase"], "static_contract")
            self.assertEqual(reparse["issues"], ["host_preflight_failed"])

    def test_static_input_change_during_audit_fails_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, _cwd = self._paths(root)

            def mutate_during_audit(**_kwargs):
                server.write_text("// changed during audit", encoding="utf-8")
                return _static_pass()

            with mock.patch.object(
                APP_SERVER_HOST,
                "audit_contract",
                side_effect=mutate_during_audit,
            ):
                with self.assertRaises(OSError):
                    APP_SERVER_HOST._static_preflight(
                        executable=executable,
                        expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                        schema_root=schema,
                        desktop_mcp_manifest=manifest,
                        desktop_mcp_server=server,
                    )

    def test_static_input_change_before_spawn_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            original_resolve = APP_SERVER_HOST._resolve_probe_cwd
            resolve_count = 0
            mutation_blocked = False
            spawn_calls = 0
            original_server = server.read_bytes()
            child = _FakeProcess()

            def mutate_on_revalidation(path: Path, evidence: dict):
                nonlocal resolve_count, mutation_blocked
                resolve_count += 1
                resolved = original_resolve(path, evidence)
                if resolve_count == 2:
                    try:
                        server.write_text("// changed before spawn", encoding="utf-8")
                    except OSError:
                        mutation_blocked = True
                return resolved

            def observed_factory(*_args, **_kwargs):
                nonlocal spawn_calls
                spawn_calls += 1
                return child

            with (
                mock.patch.object(APP_SERVER_HOST, "audit_contract", return_value=_static_pass()),
                mock.patch.object(
                    APP_SERVER_HOST,
                    "_resolve_probe_cwd",
                    side_effect=mutate_on_revalidation,
                ),
                mock.patch.object(APP_SERVER_HOST, "run_read_only_probe", return_value=_core_pass()),
            ):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    process_factory=observed_factory,
                    job_factory=_FakeJob,
                )
            if sys.platform == "win32":
                self.assertTrue(mutation_blocked)
                self.assertEqual(server.read_bytes(), original_server)
                # Initial validation, pre-launch revalidation, the last pre-spawn
                # check, suspended-child check, and post-shutdown check all retain
                # the same source identity.  The Windows read pin prevents the
                # attempted pre-spawn mutation itself.
                self.assertEqual(resolve_count, 5)
                self.assertEqual(spawn_calls, 1)
                self.assertEqual(result["status"], "fail")
                self.assertEqual(result["failure_phase"], "runtime_provenance")
                self.assertEqual(result["issues"], ["runtime_provenance_unavailable"])
            else:
                self.assertNotEqual(server.read_bytes(), original_server)
                self.assertEqual(resolve_count, 2)
                self.assertEqual(spawn_calls, 0)
                self.assertEqual(result["failure_phase"], "static_contract")
                self.assertEqual(result["issues"], ["host_preflight_failed"])
                self.assertFalse(result["app_server_process_started"])

    def test_cwd_change_during_static_rehash_is_rejected_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            original_current = APP_SERVER_HOST._provenance_inputs_still_current
            current_calls = 0
            spawn_calls = 0

            def mutate_cwd_after_rehash(**kwargs):
                nonlocal current_calls
                current_calls += 1
                current = original_current(**kwargs)
                if current_calls == 1:
                    (cwd / "raced.txt").write_text("changed", encoding="utf-8")
                return current

            def forbidden_factory(*_args, **_kwargs):
                nonlocal spawn_calls
                spawn_calls += 1
                raise AssertionError("must not spawn")

            with (
                mock.patch.object(APP_SERVER_HOST, "audit_contract", return_value=_static_pass()),
                mock.patch.object(
                    APP_SERVER_HOST,
                    "_provenance_inputs_still_current",
                    side_effect=mutate_cwd_after_rehash,
                ),
            ):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    hard_timeout_seconds=5.0,
                    process_factory=forbidden_factory,
                    job_factory=_FakeJob,
                )
        self.assertEqual(result["failure_phase"], "child_start")
        self.assertEqual(result["issues"], ["app_server_start_failed"])
        self.assertEqual(spawn_calls, 0)

    def test_executable_is_pinned_or_change_is_caught_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            child = _FakeProcess()
            job = _FakeJob()
            replacement_blocked = False

            def mutating_factory(*_args, **_kwargs):
                nonlocal replacement_blocked
                try:
                    executable.write_bytes(b"replaced-after-preflight")
                except OSError:
                    replacement_blocked = True
                return child

            with (
                mock.patch.object(APP_SERVER_HOST, "audit_contract", return_value=_static_pass()),
                mock.patch.object(APP_SERVER_HOST, "run_read_only_probe", return_value=_core_pass()),
            ):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    hard_timeout_seconds=5.0,
                    process_factory=mutating_factory,
                    job_factory=lambda: job,
                )
        if sys.platform == "win32":
            self.assertTrue(replacement_blocked)
            self.assertEqual(result["status"], "fail")
            self.assertEqual(result["failure_phase"], "runtime_provenance")
            self.assertEqual(result["issues"], ["runtime_provenance_unavailable"])
            self.assertTrue(job.resumed)
        else:
            self.assertEqual(result["failure_phase"], "child_start")
            self.assertEqual(result["issues"], ["desktop_task_port_unavailable"])
            self.assertTrue(result["app_server_process_started"])
            self.assertTrue(result["app_server_process_stopped"])
            self.assertFalse(job.resumed)

    def test_malformed_core_cannot_smuggle_mutation_or_unclosed_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable, schema, manifest, server, cwd = self._paths(root)
            child = _FakeProcess()
            malformed = _core_fail()
            malformed["failure_phase"] = "remote-private-value"
            malformed["responder_mutation_attempted"] = True
            with (
                mock.patch.object(APP_SERVER_HOST, "audit_contract", return_value=_static_pass()),
                mock.patch.object(APP_SERVER_HOST, "run_read_only_probe", return_value=malformed),
            ):
                result = APP_SERVER_HOST.run_live_read_only_probe(
                    codex_executable=executable,
                    expected_codex_sha256=sha256(executable.read_bytes()).hexdigest(),
                    probe_cwd=cwd,
                    schema_root=schema,
                    desktop_mcp_manifest=manifest,
                    desktop_mcp_server=server,
                    hard_timeout_seconds=5.0,
                    process_factory=lambda *_args, **_kwargs: child,
                    job_factory=_FakeJob,
                )
        self.assertEqual(result["failure_phase"], "runtime_validation")
        self.assertEqual(result["issues"], ["runtime_attestation_invalid"])
        self.assertFalse(result["responder_mutation_attempted"])
        self.assertNotIn("remote-private-value", json.dumps(result))

    def test_core_partial_evidence_must_be_monotonic_and_match_phase(self) -> None:
        forged = _core_fail()
        forged["failure_phase"] = "initialize"
        forged["control_thread_started"] = False
        forged["control_thread_ephemeral"] = False
        forged["list_projects_result_valid"] = True
        self.assertFalse(APP_SERVER_HOST._core_envelope_valid(forged))

        forged = _core_fail()
        forged["failure_phase"] = "list_threads"
        forged["list_threads_result_valid"] = True
        self.assertFalse(APP_SERVER_HOST._core_envelope_valid(forged))

        forged = _core_fail()
        forged["failure_phase"] = "tool_catalog"
        forged["codex_app_connected"] = False
        self.assertFalse(APP_SERVER_HOST._core_envelope_valid(forged))

    def test_ungraceful_child_is_stopped_through_owned_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result, _observed, child, job = self._run(
                Path(temp_dir),
                process=_FakeProcess(exit_on_stdin_close=False),
            )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_phase"], "runtime_provenance")
        self.assertEqual(result["issues"], ["runtime_provenance_unavailable"])
        self.assertTrue(result["child_forced_stop"])
        self.assertTrue(job.terminated)
        self.assertEqual(child.returncode, 1)

    def test_transport_has_session_cap_and_absolute_read_deadline(self) -> None:
        stream = io.BytesIO(b"{}\n{}\n{}\n")
        process = _TransportProcess(stream)
        with mock.patch.object(APP_SERVER_HOST, "MAX_SESSION_FRAMES", 2):
            transport = APP_SERVER_HOST.StdioLineTransport(
                process,
                io_deadline=time.monotonic() + 1.0,
            )
            self.assertTrue(transport._overflow.wait(1.0))
            with self.assertRaises(OSError):
                transport.read_line(1024, 1.0)

        blocked = _BlockingStdout()
        transport = APP_SERVER_HOST.StdioLineTransport(
            _TransportProcess(blocked),
            io_deadline=time.monotonic() + 0.05,
        )
        with self.assertRaises(OSError):
            transport.read_line(1024, 1.0)
        blocked.release.set()

    def test_source_has_no_pipe_lookup_responder_or_fallback_surface(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "CODEX_APP_TOOLS_PIPE_PATH",
            "os.environ",
            "shell=True",
            "thread/resume",
            "turn/start",
            "thread/compact/start",
            "send_message_to_thread",
            "beeper_queue_cli",
            "bridge_core",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn('[str(executable), "app-server", "--listen", "stdio://"]', source)
        self.assertIn("MAX_SESSION_FRAMES", source)
        self.assertIn("MAX_SESSION_BYTES", source)


if __name__ == "__main__":
    unittest.main()
