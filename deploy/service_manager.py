"""OII Linux deployment: supervised services and fast-forward-only updates.

The orchestration itself uses only the Python standard library. App dependency
installation and real systemd changes occur only through explicit CLI actions.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = Path("/etc/systemd/system")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from config.memory_policy import apply_memory_policy, mmtr_preferred, selected_memory_backend
from config.observer_access import observer_token


def info(message):
    print(f"[OII] {message}", flush=True)


def run(command, *, capture=False, check=True, cwd=None, env=None):
    result = subprocess.run([str(x) for x in command], cwd=cwd or ROOT,
                            env=env, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if check and result.returncode:
        # Never echo subprocess arguments: they may include deployment secrets.
        detail = (result.stderr or "").strip() if capture else "see command output above"
        raise RuntimeError(f"{Path(str(command[0])).name} exited {result.returncode}: {detail}")
    return result


def executable(value):
    found = shutil.which(str(value))
    if not found:
        raise RuntimeError(f"Executable not found: {value}")
    return os.path.abspath(found)


def python_bin(*, bootstrap=False):
    if os.environ.get("PYTHON_BIN"):
        return executable(os.environ["PYTHON_BIN"])
    for folder in (".venv", "venv"):
        candidate = ROOT / folder / "bin/python"
        if candidate.is_file():
            # Preserve the virtualenv path; resolving its symlink loses the venv.
            return str(candidate)
    if sys.prefix != sys.base_prefix:
        return sys.executable
    if not bootstrap:
        raise RuntimeError("No virtualenv found. Run: sudo bash auto-start.sh install")
    destination = ROOT / ".venv"
    info("Creating .venv with the selected Python (Python 3.10/3.11 recommended)")
    run([sys.executable, "-m", "venv", destination])
    return str(destination / "bin/python")


def load_config(python):
    if not (ROOT / "data/.config.yaml").is_file():
        raise RuntimeError("Missing data/.config.yaml; configure the deployment before starting")
    # Use the application's interpreter so a freshly created venv can provide
    # PyYAML even when the bootstrap interpreter has no third-party packages.
    source = """import json,sys,yaml
def merge(a,b):
    for k,v in b.items():
        a[k]=merge(a[k],v) if isinstance(a.get(k),dict) and isinstance(v,dict) else v
    return a
with open(sys.argv[1],encoding='utf-8') as f: base=yaml.safe_load(f) or {}
with open(sys.argv[2],encoding='utf-8') as f: private=yaml.safe_load(f) or {}
print(json.dumps(merge(base,private)))
"""
    result = run([python, "-c", source, ROOT / "config.yaml", ROOT / "data/.config.yaml"], capture=True, check=False)
    if result.returncode:
        raise RuntimeError("Cannot read YAML configuration. Check data/.config.yaml and install Python dependencies with auto-start.sh prepare")
    return apply_memory_policy(json.loads(result.stdout))


def enabled(name, automatic):
    value = os.environ.get(name, "auto").lower()
    if value == "auto":
        return bool(automatic)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be auto, 1 or 0")


def true(value):
    return str(value).lower() in {"1", "true", "yes", "on"}


def port(value):
    result = int(value)
    if not 1 <= result <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    return result


def service_name():
    value = os.environ.get("OII_SERVICE_NAME", "oii.service")
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", value):
        raise ValueError("Invalid OII_SERVICE_NAME")
    return value


def node_check(config):
    mmtr = selected_memory_backend(config) == "mmtr"
    value = os.environ.get("NODE_BIN") or (config.get("mmtr", {}).get("node", "node") if mmtr else "node")
    node = executable(value)
    version = run([node, "-p", "process.versions.node"], capture=True).stdout.strip()
    parts = tuple(int(x) for x in version.split(".")[:3])
    tencent = start_tencent(config)
    minimum = (22, 16, 0) if tencent else (22, 13, 0)
    if parts < minimum:
        raise RuntimeError(f"Node >= {'.'.join(map(str, minimum))} required; found {version}")
    return node


def memory_is_local(config):
    if selected_memory_backend(config) == "mmtr":
        return False
    selected = true(config.get("tencent_memory_enabled")) or str(config.get("memory_backend", "")).lower() == "tencent"
    endpoint = urlsplit(str(config.get("tencent_memory_base_url", "http://127.0.0.1:8420")))
    return selected and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}


def start_tencent(config):
    # During MMTR preference even an old START_MEMORY_CORE=1 is suspended.
    return False if mmtr_preferred(config) else enabled("START_MEMORY_CORE", memory_is_local(config))


def mail_config():
    path = Path(os.environ.get("MAILPILOT_CONFIG", str(ROOT / "data/.config.yaml")))
    return path if path.is_absolute() else ROOT / path


def mail_enabled(config, python):
    automatic = bool(config.get("imap", {}).get("user"))
    if "MAILPILOT_CONFIG" in os.environ:
        automatic = mail_config().is_file()
    return enabled("START_MAILPILOT", automatic)


def fingerprint(paths, extra=""):
    digest = hashlib.sha256(extra.encode())
    for path in sorted(paths):
        if path.is_file():
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def state_path(name):
    folder = ROOT / "data/deploy"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if not path.stat().st_size:
                handle.write(b"\0"); handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another deployment operation is running") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another deployment operation is running") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def prepare_dependencies(*, skip_python=False):
    python = python_bin(bootstrap=True)
    stamp_path = state_path("dependencies.json")
    stamp = read_json(stamp_path)
    changed = False
    wanted = fingerprint([ROOT / "requirements.txt"], python)
    policy = os.environ.get("INSTALL_PYTHON_DEPS", "auto")
    if policy not in {"auto", "0", "1"}:
        raise ValueError("INSTALL_PYTHON_DEPS must be auto, 1 or 0")
    if not skip_python and policy != "0" and (policy == "1" or stamp.get("python") != wanted):
        info("Installing/updating Python dependencies")
        run([python, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])
        stamp["python"] = wanted
        write_json(stamp_path, stamp)
        changed = True
    config = load_config(python)
    mmtr = selected_memory_backend(config) == "mmtr"
    use_tencent = start_tencent(config)
    use_bilibili = bool((config.get('learning',{}) or {}).get('enabled',False))
    node = node_check(config) if mmtr or use_tencent or use_bilibili else None
    if use_bilibili:
        folder = ROOT / 'deploy/bilibili'
        wanted = fingerprint([folder / 'package.json',folder / 'package-lock.json'],'bilibili-mcp')
        if stamp.get('bilibili-mcp') != wanted or not (folder / 'node_modules/@xzxzzx/bilibili-mcp/dist/cli.js').is_file():
            environment = {**os.environ,'PATH':str(Path(node).parent)+os.pathsep+os.environ.get('PATH','')}
            npm = shutil.which(os.environ.get('NPM_BIN','npm'),path=environment['PATH'])
            if not npm:
                raise RuntimeError('npm not found; set NPM_BIN alongside NODE_BIN in .env')
            info('Preparing read-only Bilibili learning integration')
            run([npm,'ci','--ignore-scripts','--omit=dev','--no-audit','--no-fund'],cwd=folder,env=environment)
            stamp['bilibili-mcp'] = wanted
            write_json(stamp_path,stamp)
            changed = True
    if use_tencent:
        folder = ROOT / "TencentDB-Agent-Memory/MemoryCore"
        wanted = fingerprint([folder / "package.json", folder / "package-lock.json"], "memory-core")
        if stamp.get("memory-core") != wanted or not (folder / "node_modules/tsx").is_dir():
            environment = {**os.environ, "NODE_BIN": node,
                           "PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", "")}
            npm = shutil.which(os.environ.get("NPM_BIN", "npm"), path=environment["PATH"])
            if not npm:
                raise RuntimeError("npm not found; set NPM_BIN alongside NODE_BIN in .env")
            command = "ci" if (folder / "package-lock.json").is_file() else "install"
            info("Preparing Tencent MemoryCore")
            run([npm, command], cwd=folder, env=environment)
            run([npm, "run", "build"], cwd=folder, env=environment)
            stamp["memory-core"] = wanted
            write_json(stamp_path, stamp)
            changed = True
    if mail_enabled(config, python):
        binary = Path(os.environ.get("MAILPILOT_BIN", str(ROOT / "mailpilot/mailpilot")))
        if not binary.is_absolute():
            binary = ROOT / binary
        wanted = fingerprint(list((ROOT / "mailpilot").rglob("*.go")) + [ROOT / "mailpilot/go.mod", ROOT / "mailpilot/go.sum"])
        if not binary.is_file() or stamp.get("mailpilot") != wanted:
            info("Building MailPilot")
            run([executable("go"), "build", "-o", binary, "."], cwd=ROOT / "mailpilot")
            stamp["mailpilot"] = wanted
            write_json(stamp_path, stamp)
            changed = True
    return changed


def specs(config, python):
    result = []
    if start_tencent(config):
        endpoint = urlsplit(str(config.get("tencent_memory_base_url", "http://127.0.0.1:8420")))
        memory_port = port(endpoint.port or 8420)
        result.append({"name": "memory-core", "command": [python, str(ROOT / "deploy/memory-core/start_memory_core.py")],
                       "port": memory_port, "health": f"http://127.0.0.1:{memory_port}/health"})
    if enabled("START_MCP", True):
        result.extend([
            {"name": "mcp-restaurant", "command": [python, str(ROOT / "mcp_restaurant_server/server.py")], "port": port(os.environ.get("MCP_PORT", 8766))},
            {"name": "mcp-aggregator", "command": [python, str(ROOT / "mcp_aggregator/server.py")], "port": port(os.environ.get("MCP_AGGREGATOR_PORT", 8765))},
        ])
    if enabled("START_UI", True):
        backend_port = port(os.environ.get("BACKEND_PORT") or os.environ.get("HTTP_PORT") or config.get("server", {}).get("http_port", 8003))
        ui_port = port(os.environ.get("UI_PORT", 8010))
        result.append({"name": "ui", "command": [python, str(ROOT / "ui_server.py"), "--host", os.environ.get("UI_HOST", "0.0.0.0"),
                       "--port", str(ui_port), "--backend-port", str(backend_port)], "port": ui_port})
    if mail_enabled(config, python):
        binary = Path(os.environ.get("MAILPILOT_BIN", str(ROOT / "mailpilot/mailpilot")))
        if not binary.is_absolute():
            binary = ROOT / binary
        if not binary.is_file() or not mail_config().is_file():
            raise RuntimeError("MailPilot is not prepared; run auto-start.sh prepare")
        result.append({"name": "mailpilot", "command": [str(binary), "daemon", "-c", str(mail_config())]})
    result.append({"name": "controller", "command": [python, str(ROOT / "app.py")],
                   "port": port(config.get("server", {}).get("http_port", 8003))})
    return result


def port_open(value):
    try:
        with socket.create_connection(("127.0.0.1", value), timeout=0.2):
            return True
    except OSError:
        return False


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(message.encode(), address)


def wait_ready(process, spec, stopped, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stopped():
            raise InterruptedError("Service startup stopped")
        if process.poll() is not None:
            raise RuntimeError(f"{spec['name']} exited {process.returncode}; see tmp/services/{spec['name']}.log")
        if "health" in spec:
            try:
                with urlopen(spec["health"], timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
        elif "port" in spec:
            if port_open(spec["port"]):
                return
        elif time.monotonic() + timeout - deadline >= 2:
            return
        time.sleep(0.2)
    raise RuntimeError(f"{spec['name']} was not ready within {timeout}s")


def stop_children(children):
    for process in reversed(children):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 20
    for process in reversed(children):
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    # Also clean grandchildren when a wrapper exited before its child.
    for process in reversed(children):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def supervise():
    if sys.platform != "linux":
        raise RuntimeError("The service supervisor requires Linux; use --help for commands")
    python = python_bin()
    config = load_config(python)
    if selected_memory_backend(config) == "mmtr" or start_tencent(config):
        os.environ["NODE_BIN"] = node_check(config)
    planned = specs(config, python)
    ports = [item["port"] for item in planned if "port" in item]
    if len(ports) != len(set(ports)):
        raise RuntimeError("Configured services have conflicting ports")
    for value in ports:
        if port_open(value):
            raise RuntimeError(f"Port {value} is already in use; stop the previous launcher before starting OII")
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, request_stop)
    logs = ROOT / "tmp/services"
    logs.mkdir(parents=True, exist_ok=True)
    children = []
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if environment.get("NODE_BIN"):
        environment["PATH"] = str(Path(environment["NODE_BIN"]).parent) + os.pathsep + environment.get("PATH", "")
    environment.setdefault("MCP_BACKENDS", f"restaurant=ws://127.0.0.1:{port(os.environ.get('MCP_PORT', 8766))}/mcp/")
    with locked(logs / "supervisor.lock"):
        try:
            timeout = float(os.environ.get("OII_STARTUP_TIMEOUT", "90"))
            if not 1 <= timeout <= 500:
                raise ValueError("OII_STARTUP_TIMEOUT must be 1..500 seconds")
            for spec in planned:
                info(f"Starting {spec['name']}; log: tmp/services/{spec['name']}.log")
                with (logs / f"{spec['name']}.log").open("ab", buffering=0) as output:
                    process = subprocess.Popen(spec["command"], cwd=ROOT, env=environment,
                                               stdin=subprocess.DEVNULL, stdout=output,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                children.append(process)
                wait_ready(process, spec, lambda: stopping, timeout)
            if any(process.poll() is not None for process in children):
                raise RuntimeError("A service exited during startup; see tmp/services logs")
            notify("READY=1\nSTATUS=All OII services are ready")
            info("All services ready; systemd will restart the group if any service exits")
            while not stopping:
                for process, spec in zip(children, planned):
                    if process.poll() is not None:
                        raise RuntimeError(f"{spec['name']} exited {process.returncode}; restarting the service group")
                time.sleep(0.5)
        except InterruptedError:
            pass
        finally:
            try:
                notify("STOPPING=1")
            finally:
                stop_children(children)


def linux_root():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("This action requires Linux root: use sudo bash auto-start.sh / auto-update.sh")
    executable("systemctl")


def deployment_user():
    import pwd
    return pwd.getpwuid(ROOT.stat().st_uid).pw_name


def delegated(command):
    """Keep Git, virtualenv and build outputs owned by the repository owner."""
    if sys.platform == "linux" and os.geteuid() == 0 and ROOT.stat().st_uid != 0:
        user = deployment_user()
        return run([executable("runuser"), "-u", user, "--", sys.executable,
                    ROOT / "deploy/service_manager.py", *command], check=False).returncode
    return None


def unit_quote(value, *, command=False):
    value = str(value)
    if any(char in value for char in "\r\n\0"):
        raise ValueError("Invalid systemd value")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def unit_directory(value):
    # WorkingDirectory is a literal path, unlike the ExecStart argument list.
    # Its parser does not remove shell-style quotes or decode backslashes.
    value = str(value)
    if any(char in value for char in "\r\n\0") or value != value.strip():
        raise ValueError("Invalid systemd working directory")
    return value.replace("%", "%%")


def runtime_unit(user):
    return f"""[Unit]
Description=OII controller and companion services
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=notify
NotifyAccess=main
User={user}
WorkingDirectory={unit_directory(ROOT)}
ExecStart=/usr/bin/bash {unit_quote(ROOT / 'auto-start.sh', command=True)} run
Restart=always
RestartSec=5
TimeoutStartSec=600
TimeoutStopSec=40
KillMode=control-group
UMask=0077
Environment=OII_SERVICE_NAME={service_name()}

[Install]
WantedBy=multi-user.target
"""


def update_units():
    name = service_name()
    stem = name.removesuffix(".service") + "-update"
    calendar = os.environ.get("OII_UPDATE_CALENDAR", "hourly")
    if not re.fullmatch(r"[A-Za-z0-9:*/., _~=-]+", calendar):
        raise ValueError("Invalid OII_UPDATE_CALENDAR")
    unit = f"""[Unit]
Description=Update OII from Git and restart after successful preparation
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={unit_directory(ROOT)}
ExecStart=/usr/bin/bash {unit_quote(ROOT / 'auto-update.sh', command=True)} run
TimeoutStartSec=3600
UMask=0077
Environment=OII_SERVICE_NAME={name}
"""
    timer = f"""[Unit]
Description=Check OII updates automatically

[Timer]
OnCalendar={calendar}
RandomizedDelaySec=300
Persistent=true
Unit={stem}.service

[Install]
WantedBy=timers.target
"""
    return stem, unit, timer


def systemctl(action, unit, *, capture=False):
    command = [executable("systemctl"), action, unit]
    if sys.platform == "linux" and os.geteuid() != 0 and action in {"restart", "start", "stop"}:
        command = [executable("sudo"), "-n", *command]
    return run(command, capture=capture)


def install_runtime(skip_deps=False):
    linux_root()
    result = delegated(["start", "prepare", *( ["--no-deps"] if skip_deps else [])])
    if result is None:
        prepare_dependencies(skip_python=skip_deps)
    elif result:
        raise RuntimeError("Dependency preparation failed; service was not restarted")
    unit = UNIT_DIR / service_name()
    unit.write_text(runtime_unit(deployment_user()), encoding="utf-8")
    run([executable("systemd-analyze"), "verify", unit], capture=True)
    run([executable("systemctl"), "daemon-reload"])
    run([executable("systemctl"), "enable", service_name()])
    systemctl("restart", service_name())
    systemctl("is-active", service_name(), capture=True)
    info(f"Enabled on boot with automatic crash recovery: {service_name()}")


def install_update():
    linux_root()
    if not (UNIT_DIR / service_name()).is_file():
        raise RuntimeError("First install the runtime: sudo bash auto-start.sh install")
    stem, unit, timer = update_units()
    (UNIT_DIR / f"{stem}.service").write_text(unit, encoding="utf-8")
    (UNIT_DIR / f"{stem}.timer").write_text(timer, encoding="utf-8")
    run([executable("systemd-analyze"), "verify", UNIT_DIR / f"{stem}.service", UNIT_DIR / f"{stem}.timer"], capture=True)
    run([executable("systemctl"), "daemon-reload"])
    run([executable("systemctl"), "enable", "--now", f"{stem}.timer"])
    systemctl("is-active", f"{stem}.timer", capture=True)
    info(f"Automatic updates enabled: {stem}.timer (default: hourly)")


def git(*arguments, check=True):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    return run([executable("git"), *arguments], capture=True, check=check, env=env).stdout.strip()


def restart_runtime():
    if not (UNIT_DIR / service_name()).is_file():
        raise RuntimeError("Code is updated; install its supervisor with sudo bash auto-start.sh install")
    systemctl("restart", service_name())
    systemctl("is-active", service_name(), capture=True)


def prepare_updated_dependencies(*, skip_python=False):
    # A running updater retains its old Python functions after git merge.
    # Load preparation from the checkout in a fresh interpreter, including its
    # backend policy and imports, before deciding what services to install.
    source = (
        "import runpy,sys; "
        "module=runpy.run_path(sys.argv[1]); "
        "changed=module['prepare_dependencies'](skip_python=sys.argv[2]=='1'); "
        "sys.exit(0 if changed else 10)"
    )
    result = run([python_bin(), "-B", "-c", source, ROOT / "deploy/service_manager.py", "1" if skip_python else "0"], check=False)
    if result.returncode not in {0, 10}:
        raise RuntimeError("Updated dependency preparation failed; retry the update after fixing the reported error")
    return result.returncode == 0


def update_repository(*, no_deps=False, no_restart=False):
    remote = os.environ.get("UPDATE_REMOTE", "origin")
    branch = os.environ.get("UPDATE_BRANCH", "main")
    if remote.startswith("-") or branch.startswith("-"):
        raise ValueError("Invalid update remote/branch")
    lock = Path(git("rev-parse", "--git-path", "oii-update.lock"))
    if not lock.is_absolute():
        lock = ROOT / lock
    with locked(lock):
        if git("status", "--porcelain", "--untracked-files=no"):
            raise RuntimeError("Tracked files have local changes; commit or back them up before updating. Nothing was stashed or discarded")
        if git("symbolic-ref", "--quiet", "--short", "HEAD") != branch:
            raise RuntimeError(f"Checkout {branch} before updating; automatic branch switching is disabled")
        before = git("rev-parse", "HEAD")
        info(f"Fetching {remote}/{branch}")
        git("fetch", "--no-tags", remote, branch)
        target = git("rev-parse", "--verify", "FETCH_HEAD^{commit}")
        git("merge-base", "--is-ancestor", before, target)
        pending_path = state_path("pending-update.json")
        pending = read_json(pending_path)
        changed = before != target
        if changed:
            tag = "deploy-rollback-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            # Deployment tags are local and never move the user's default rollback tag.
            git("-c", "user.name=OII Deployment", "-c", "user.email=oii-deploy@localhost", "tag", "-a", tag, before, "-m", "Deployment rollback point")
            pending = {"before": before, "target": target, "rollback_tag": tag}
            write_json(pending_path, pending)
            git("merge", "--ff-only", target)
            info(f"Updated to {target[:7]}; rollback tag: {tag}")
        prepared = prepare_updated_dependencies(skip_python=no_deps)
        if changed or prepared or pending:
            if not no_restart:
                restart_runtime()
                write_json(state_path("last-update.json"), {**pending, "target": target, "at": datetime.now(timezone.utc).isoformat()})
                pending_path.unlink(missing_ok=True)
            else:
                write_json(pending_path, pending or {"target": target})
            return True
        info(f"Already current: {target[:7]}; no restart needed")
        return False


def update_command(args):
    forwarded = ["update", "run", "--delegated", "--no-restart"]
    if args.no_deps:
        forwarded.append("--no-deps")
    if not args.delegated:
        result = delegated(forwarded)
        if result is not None:
            if result not in {0, 10}:
                raise RuntimeError("Update failed; inspect the update log before retrying")
            if result == 0 and not args.no_restart:
                restart_runtime()
                pending_path = state_path("pending-update.json")
                pending = read_json(pending_path)
                write_json(state_path("last-update.json"), {**pending, "at": datetime.now(timezone.utc).isoformat()})
                pending_path.unlink(missing_ok=True)
            return 0
    changed = update_repository(no_deps=args.no_deps, no_restart=args.no_restart)
    return 10 if args.delegated and not changed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="OII service supervision and automatic updates")
    sub = parser.add_subparsers(dest="mode", required=True)
    start = sub.add_parser("start", help="run, prepare or install the service supervisor")
    start.add_argument("action", nargs="?", default="run", choices=["run", "prepare", "install", "start", "restart", "stop", "status", "logs", "check", "token"])
    start.add_argument("--no-deps", action="store_true", help="skip Python dependency installation")
    update = sub.add_parser("update", help="update now or install/disable the update timer")
    update.add_argument("action", nargs="?", default="run", choices=["run", "install", "disable", "status", "logs"])
    update.add_argument("--no-deps", action="store_true", help="skip Python dependency installation")
    update.add_argument("--no-restart", action="store_true", help="prepare code but defer the runtime restart")
    update.add_argument("--delegated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.mode == "start":
        if args.action == "run":
            supervise()
        elif args.action == "prepare":
            result = delegated(["start", "prepare", *( ["--no-deps"] if args.no_deps else [])])
            if result is not None:
                return result
            prepare_dependencies(skip_python=args.no_deps)
        elif args.action == "install":
            install_runtime(args.no_deps)
        elif args.action == "check":
            python = python_bin()
            config = load_config(python)
            for spec in specs(config, python):
                info(f"{spec['name']}: planned" + (f" on {spec['port']}" if "port" in spec else ""))
        elif args.action == "token":
            print(observer_token(load_config(python_bin()), ROOT))
        elif args.action == "logs":
            run([executable("journalctl"), "-u", service_name(), "-f", "-n", "100"])
        else:
            systemctl(args.action, service_name())
    elif args.action == "run":
        return update_command(args)
    elif args.action == "install":
        install_update()
    elif args.action == "disable":
        linux_root()
        stem, _, _ = update_units()
        run([executable("systemctl"), "disable", "--now", f"{stem}.timer"])
    elif args.action == "status":
        stem, _, _ = update_units()
        run([executable("systemctl"), "list-timers", "--all", f"{stem}.timer"])
    else:
        stem, _, _ = update_units()
        run([executable("journalctl"), "-u", f"{stem}.service", "-f", "-n", "100"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as error:
        print(f"[OII ERROR] {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
