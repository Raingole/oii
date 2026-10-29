"""Deployment behavior without installing packages, changing systemd or using a cloud host."""
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("oii_deployment_test", ROOT / "deploy/service_manager.py")
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(manager, "ROOT", self.root))
        self.stack.enter_context(patch.dict(os.environ, {
            "START_MEMORY_CORE": "auto", "START_MAILPILOT": "auto", "START_UI": "1", "START_MCP": "1",
            "INSTALL_PYTHON_DEPS": "auto", "UPDATE_REMOTE": "origin", "UPDATE_BRANCH": "main",
            "OII_SERVICE_NAME": "oii.service", "OII_UPDATE_CALENDAR": "hourly",
        }))
        for key in ("MAILPILOT_CONFIG", "UI_PORT", "HTTP_PORT", "BACKEND_PORT", "MCP_PORT", "MCP_AGGREGATOR_PORT"):
            self.stack.enter_context(patch.dict(os.environ))
            os.environ.pop(key, None)

    def test_mmtr_never_implicitly_starts_tencent(self):
        config = {"memory_backend": "mmtr", "tencent_memory_enabled": True}
        names = [x["name"] for x in manager.specs(config, "python")]
        self.assertNotIn("memory-core", names)
        self.assertIn("controller", names)
        self.assertIn("ui", names)

    def test_local_and_remote_tencent_selection(self):
        self.assertTrue(manager.memory_is_local({"memory_backend": "tencent", "mmtr": {"preferred": False}}))
        self.assertFalse(manager.memory_is_local({"memory_backend": "tencent", "tencent_memory_base_url": "https://memory.example.com:8420"}))
        self.assertFalse(manager.memory_is_local({"memory_backend": "legacy", "tencent_memory_enabled": False}))

    def test_configuration_ports_and_service_switches(self):
        with patch.dict(os.environ, {"START_MCP": "0", "UI_PORT": "9010", "START_MAILPILOT": "0"}):
            services = manager.specs({"server": {"http_port": 9003}}, "python")
        self.assertEqual([x["name"] for x in services], ["ui", "controller"])
        self.assertEqual(services[0]["port"], 9010)
        self.assertIn("9003", services[0]["command"])

    def test_node_version_matches_each_memory_backend(self):
        with patch.object(manager, "executable", return_value="node"), patch.object(manager, "run") as run:
            run.return_value = SimpleNamespace(stdout="22.12.0\n")
            with self.assertRaisesRegex(RuntimeError, "22.13.0"):
                manager.node_check({"memory_backend": "mmtr"})
            run.return_value = SimpleNamespace(stdout="22.13.0\n")
            self.assertEqual(manager.node_check({"memory_backend": "mmtr"}), "node")
            with self.assertRaisesRegex(RuntimeError, "22.16.0"):
                manager.node_check({"memory_backend": "tencent", "mmtr": {"preferred": False}})
            with patch.dict(os.environ, {"START_MEMORY_CORE": "1"}):
                self.assertEqual(manager.node_check({"memory_backend": "mmtr"}), "node")
                self.assertFalse(manager.start_tencent({"memory_backend": "tencent"}))

    def test_dependency_fingerprints_avoid_reinstall_until_inputs_change(self):
        requirement = self.root / "requirements.txt"
        requirement.write_text("sample==1\n")
        with patch.object(manager, "python_bin", return_value="venv/python"), \
             patch.object(manager, "load_config", return_value={"memory_backend": "mmtr"}), \
             patch.object(manager, "node_check"), patch.object(manager, "run") as run:
            self.assertTrue(manager.prepare_dependencies())
            self.assertFalse(manager.prepare_dependencies())
            requirement.write_text("sample==2\n")
            self.assertTrue(manager.prepare_dependencies())
            self.assertEqual(run.call_count, 2)

    def test_failed_install_does_not_record_a_successful_fingerprint(self):
        (self.root / "requirements.txt").write_text("sample==1")
        with patch.object(manager, "python_bin", return_value="python"), \
             patch.object(manager, "run", side_effect=RuntimeError("installation failed")):
            with self.assertRaisesRegex(RuntimeError, "installation failed"):
                manager.prepare_dependencies()
        self.assertEqual(manager.read_json(manager.state_path("dependencies.json")), {})

    def test_lock_rejects_another_operation_and_releases_after_failure(self):
        lock = self.root / "deploy.lock"
        with manager.locked(lock):
            with self.assertRaisesRegex(RuntimeError, "Another deployment"):
                with manager.locked(lock):
                    pass
        with manager.locked(lock):
            pass

    def test_runtime_unit_enables_boot_and_crash_recovery(self):
        unit = manager.runtime_unit("cloud-user")
        self.assertIn("Type=notify", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("KillMode=control-group", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertIn("User=cloud-user", unit)
        self.assertIn("auto-start.sh", unit)

    def test_update_timer_and_unit_keep_the_same_service_name(self):
        with patch.dict(os.environ, {"OII_SERVICE_NAME": "custom.service", "OII_UPDATE_CALENDAR": "daily"}):
            stem, unit, timer = manager.update_units()
        self.assertEqual(stem, "custom-update")
        self.assertIn("OII_SERVICE_NAME=custom.service", unit)
        self.assertIn("OnCalendar=daily", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("Unit=custom-update.service", timer)

    def test_unit_escaping_and_invalid_settings(self):
        self.assertEqual(manager.unit_quote('a % $ "'), '"a %% $ \\""')
        self.assertIn("$$", manager.unit_quote("$HOME", command=True))
        with patch.dict(os.environ, {"OII_SERVICE_NAME": "invalid\nExecStart=bad"}):
            with self.assertRaises(ValueError): manager.service_name()
        with patch.dict(os.environ, {"OII_UPDATE_CALENDAR": "hourly\nExecStart=bad"}):
            with self.assertRaises(ValueError): manager.update_units()
        for value in (0, 70000):
            with self.assertRaises(ValueError): manager.port(value)

    def test_installer_writes_units_only_after_dependency_success(self):
        unit_dir = self.root / "units"
        unit_dir.mkdir()
        with patch.object(manager, "UNIT_DIR", unit_dir), patch.object(manager, "linux_root"), \
             patch.object(manager, "deployment_user", return_value="cloud-user"), \
             patch.object(manager, "delegated", return_value=None), patch.object(manager, "prepare_dependencies") as prepare, \
             patch.object(manager, "executable", side_effect=str), patch.object(manager, "run"), patch.object(manager, "systemctl") as ctl:
            prepare.side_effect = RuntimeError("failed")
            with self.assertRaises(RuntimeError): manager.install_runtime()
            self.assertFalse((unit_dir / "oii.service").exists())
            prepare.side_effect = None
            manager.install_runtime()
            self.assertTrue((unit_dir / "oii.service").exists())
            ctl.assert_called_once_with("restart", "oii.service")

    def test_dead_child_prevents_ready_and_cleanup_targets_owned_process_groups(self):
        process = Mock(pid=123, returncode=7)
        process.poll.return_value = 7
        with self.assertRaisesRegex(RuntimeError, "exited 7"):
            manager.wait_ready(process, {"name": "controller", "port": 8003}, lambda: False, 1)
        with patch.object(manager.os, "killpg", create=True) as kill, patch.object(manager.signal, "SIGKILL", 9, create=True):
            manager.stop_children([process])
        self.assertEqual(kill.call_count, 2)
        self.assertTrue(all(call.args[0] == 123 for call in kill.call_args_list))

    def test_shell_entrypoints_parse_and_show_help(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("Bash unavailable")
        environment = {**os.environ, "PYTHON_BIN": sys.executable.replace("\\", "/")}
        for name in ("auto-start.sh", "auto-update.sh"):
            path = ROOT / name
            parsed = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(parsed.returncode, 0, parsed.stderr)
            result = subprocess.run([bash, str(path), "--help"], env=environment, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", result.stdout)


@unittest.skipUnless(shutil.which("git"), "Git required")
class GitUpdateTests(unittest.TestCase):
    def command(self, folder, *args):
        result = subprocess.run([shutil.which("git"), *args], cwd=folder, check=True,
                                capture_output=True, text=True, encoding="utf-8")
        return result.stdout.strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.upstream = self.base / "upstream"
        self.remote = self.base / "remote.git"
        self.root = self.base / "cloud"
        self.upstream.mkdir()
        self.command(self.upstream, "init", "-b", "main")
        self.command(self.upstream, "config", "user.name", "Test")
        self.command(self.upstream, "config", "user.email", "test@example.invalid")
        (self.upstream / "app.txt").write_text("before\n")
        self.command(self.upstream, "add", ".")
        self.command(self.upstream, "commit", "-m", "before")
        self.before = self.command(self.upstream, "rev-parse", "HEAD")
        self.command(self.base, "clone", "--bare", str(self.upstream), str(self.remote))
        self.command(self.base, "clone", str(self.remote), str(self.root))
        self.command(self.upstream, "remote", "add", "origin", str(self.remote))
        self.command(self.root, "config", "user.name", "Test")
        self.command(self.root, "config", "user.email", "test@example.invalid")
        (self.root / "data").mkdir()
        self.private = self.root / "data/.config.yaml"
        self.private.write_text("private: do-not-touch\n")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(manager, "ROOT", self.root))
        self.stack.enter_context(patch.dict(os.environ, {"UPDATE_REMOTE": "origin", "UPDATE_BRANCH": "main"}))
        self.prepare = self.stack.enter_context(patch.object(manager, "prepare_dependencies", return_value=False))
        self.restart = self.stack.enter_context(patch.object(manager, "restart_runtime"))

    def publish(self, name="app.txt", content="after\n"):
        (self.upstream / name).write_text(content)
        self.command(self.upstream, "add", ".")
        self.command(self.upstream, "commit", "-m", "after")
        self.command(self.upstream, "push", "origin", "main")
        return self.command(self.upstream, "rev-parse", "HEAD")

    def test_update_tags_previous_commit_preserves_config_and_restarts(self):
        target = self.publish()
        self.assertTrue(manager.update_repository())
        self.assertEqual(self.command(self.root, "rev-parse", "HEAD"), target)
        tags = self.command(self.root, "tag", "--list", "deploy-rollback-*").splitlines()
        self.assertEqual(len(tags), 1)
        self.assertEqual(self.command(self.root, "rev-parse", tags[0] + "^{}"), self.before)
        self.assertEqual(self.private.read_text(), "private: do-not-touch\n")
        self.restart.assert_called_once()

    def test_no_change_does_not_restart(self):
        self.assertFalse(manager.update_repository())
        self.restart.assert_not_called()

    def test_tracked_local_changes_are_preserved_and_block_update(self):
        self.publish()
        (self.root / "app.txt").write_text("local work\n")
        with self.assertRaisesRegex(RuntimeError, "local changes"):
            manager.update_repository()
        self.assertEqual((self.root / "app.txt").read_text(), "local work\n")
        self.assertEqual(self.command(self.root, "rev-parse", "HEAD"), self.before)
        self.prepare.assert_not_called()

    def test_divergent_history_is_rejected(self):
        self.publish()
        (self.root / "local.txt").write_text("local work")
        self.command(self.root, "add", "local.txt")
        self.command(self.root, "commit", "-m", "local")
        head = self.command(self.root, "rev-parse", "HEAD")
        with self.assertRaises(RuntimeError): manager.update_repository()
        self.assertEqual(self.command(self.root, "rev-parse", "HEAD"), head)
        self.restart.assert_not_called()

    def test_wrong_branch_does_not_switch(self):
        self.command(self.root, "switch", "-c", "work")
        with self.assertRaisesRegex(RuntimeError, "Checkout main"):
            manager.update_repository()
        self.assertEqual(self.command(self.root, "branch", "--show-current"), "work")

    def test_untracked_collision_is_never_overwritten(self):
        self.publish("collision.txt", "remote")
        (self.root / "collision.txt").write_text("local")
        with self.assertRaises(RuntimeError): manager.update_repository()
        self.assertEqual((self.root / "collision.txt").read_text(), "local")
        self.assertEqual(self.command(self.root, "rev-parse", "HEAD"), self.before)
        self.restart.assert_not_called()

    def test_failed_dependency_preparation_leaves_retry_marker_and_rollback_tag(self):
        target = self.publish()
        self.prepare.side_effect = RuntimeError("dependency failure")
        with self.assertRaisesRegex(RuntimeError, "dependency failure"):
            manager.update_repository()
        self.restart.assert_not_called()
        self.assertEqual(self.command(self.root, "rev-parse", "HEAD"), target)
        self.assertTrue(manager.state_path("pending-update.json").exists())
        self.prepare.side_effect = None
        self.assertTrue(manager.update_repository())
        self.restart.assert_called_once()
        self.assertFalse(manager.state_path("pending-update.json").exists())

    def test_deferred_restart_is_completed_on_next_run(self):
        self.publish()
        self.assertTrue(manager.update_repository(no_restart=True))
        self.restart.assert_not_called()
        self.assertTrue(manager.update_repository())
        self.restart.assert_called_once()


if __name__ == "__main__":
    unittest.main()
