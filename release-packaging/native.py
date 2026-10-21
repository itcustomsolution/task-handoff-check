"""Proposed capped native Linux controller; public inputs only, no publisher."""
import base64
import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import select
import shutil
import signal
import stat
import subprocess
import sys
import time

LIBC = ctypes.CDLL(None, use_errno=True)
STREAM_LIMIT = 8 * 1024**2
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
HERE = Path(__file__).resolve().parent


def require(value, code):
    if not value:
        raise RuntimeError(code)


def call(argv, seconds=10, accepted=(0,)):
    proc = subprocess.Popen(argv, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    end = time.monotonic() + seconds
    data = bytearray()
    os.set_blocking(proc.stdout.fileno(), False)
    try:
        while True:
            require(time.monotonic() < end, "native-command-timeout")
            ready, _, _ = select.select([proc.stdout], [], [], 0.05)
            if ready:
                block = os.read(proc.stdout.fileno(), 8192)
                if not block:
                    break
                data.extend(block)
                require(len(data) <= 32768, "native-output-limit")
        require(proc.wait(timeout=max(0.01, end-time.monotonic())) in accepted, "native-command-failed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1)
        proc.stdout.close()
    return data.decode("utf-8", "strict")


def mount(source, target, kind=None, flags=0, data=None):
    enc = lambda value: None if value is None else os.fsencode(value)
    require(LIBC.mount(enc(source), enc(target), enc(kind), ctypes.c_ulong(flags), enc(data)) == 0,
            "mount-refused")


def bind(source, target, readonly=True):
    source, target = Path(source), Path(target)
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
    mount(source, target, flags=4096)
    if readonly:
        mount(None, target, flags=4096 | 32 | 1 | 2 | 4)


def parameters(stage):
    p = json.loads((stage / "PARAMS.json").read_text())
    require(re.fullmatch(r"\d{1,20}", p["run"]), "run-identity")
    require(stage == Path("/run/pkgtwov5" + p["run"] + "-stage"), "stage-identity")
    require(hashlib.sha256((stage / "MANIFEST.json").read_bytes()).hexdigest() == p["manifest"], "manifest-drift")
    return p


def names(p):
    stem = "pkgtwov5" + p["run"]
    return stem, stem + ".slice", stem + "ctl.service", stem + "work.service", stem + "expiry"


def show(unit):
    raw = call(["systemctl", "show", unit, "-p", "LoadState", "-p", "ActiveState",
                "-p", "SubState", "-p", "Result", "-p", "ExecMainStatus", "-p", "ControlGroup"],
               seconds=2, accepted=(0, 1, 4))
    return dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)


def cgroup(unit, memory, tasks, cpu):
    values = show(unit)
    relative = values.get("ControlGroup", "")
    match = re.fullmatch(r"(pkgtwov5\d{1,20})(?:\.slice|(?:broker|ctl|work)\.service)", unit)
    require(match is not None, "unit-identity")
    parent = match[1] + ".slice"
    expected = "/" + parent + ("" if unit == parent else "/" + unit)
    require(relative == expected, "group-identity")
    path = Path("/sys/fs/cgroup") / relative.lstrip("/")
    require(path.is_dir() and not path.is_symlink(), "group-absent")
    require((path / "memory.max").read_text().strip() == str(memory), "memory-admission")
    require((path / "memory.swap.max").read_text().strip() == "0", "swap-admission")
    require((path / "pids.max").read_text().strip() == str(tasks), "task-admission")
    quota, period = (path / "cpu.max").read_text().split()
    require(quota != "max" and int(quota) / int(period) <= cpu, "cpu-admission")
    return {"path": str(path), "inode": path.stat().st_ino}


def systemd_start(unit, parent, memory, tasks, cpu, runtime, argv):
    require(runtime > 0, "reservation-exhausted")
    return call(["systemd-run", "--quiet", "--service-type=exec", "--unit=" + unit,
                 "--slice=" + parent, "-p", "MemoryMax=" + str(memory), "-p", "MemorySwapMax=0",
                 "-p", "TasksMax=" + str(tasks), "-p", "CPUQuota=" + str(cpu) + "%",
                 "-p", "LimitAS=4294967296", "-p", "LimitNPROC=" + str(tasks),
                 "-p", "LimitFSIZE=134217728", "-p", "LimitCORE=0", "-p", "UMask=0077",
                 "-p", "KillMode=control-group", "-p", "SendSIGKILL=yes", "-p", "TimeoutStopSec=3",
                 "-p", "RuntimeMaxSec=" + str(runtime), "-p", "TimeoutStartSec=15",
                 "-p", "PrivateMounts=yes", "-p", "ProtectSystem=strict",
                 "-p", "ReadWritePaths=/run/" + parent.removesuffix(".slice"),
                 "/usr/bin/env", "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "LANG=C.UTF-8",
                 "PYTHONDONTWRITEBYTECODE=1", *argv], seconds=15)


# This independent expiry command is fixed to the one fresh owned name.
# It is not a general cleanup command, and executes no staged source.
EXPIRY = r'''
import json,os,pathlib,re,resource,shutil,subprocess,sys
stem=sys.argv[1];assert re.fullmatch(r'pkgtwov5\d{1,20}',stem)
resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456));resource.setrlimit(resource.RLIMIT_CPU,(5,5))
env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8'}
group=pathlib.Path('/sys/fs/cgroup')/(stem+'.slice')
if (group/'cgroup.kill').exists():(group/'cgroup.kill').write_text('1')
subprocess.run(['systemctl','stop',stem+'work.service',stem+'ctl.service',stem+'broker.service',stem+'.slice'],env=env,timeout=10,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
assert not group.exists() or 'populated 0' in (group/'cgroup.events').read_text()
base=pathlib.Path('/run')/stem
if base.exists():
 mounted=any(line.split()[4]==str(base) for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines())
 if mounted:subprocess.run(['umount',str(base)],env=env,timeout=3,check=True)
 base.rmdir()
if not group.exists() or 'populated 0' in (group/'cgroup.events').read_text():
 stage=pathlib.Path('/run')/(stem+'-stage')
 if stage.exists():
  assert not stage.is_symlink() and stage.stat().st_uid==0
  p=json.loads((stage/'PARAMS.json').read_text());assert p['run']==stem.removeprefix('pkgtwov5') and p['stage']==str(stage)
  shutil.rmtree(stage)
'''


def stop_owned(p):
    stem, parent, ctl, work, expiry = names(p)
    # Broker remains alive only to unmount and write a root-owned cleanup receipt.
    # The external fixed witness later stops the entire slice after broker exit.
    pending = []
    for unit in (work, ctl):
        values = show(unit)
        require(values.get("LoadState") == "not-found" or values.get("ActiveState") in ("active", "activating", "deactivating", "inactive", "failed"), "stop-status-unavailable")
        if values.get("LoadState") != "not-found":
            pending.append(unit)
        relative = values.get("ControlGroup", "")
        if relative:
            require(relative == "/" + parent + "/" + unit, "stop-group-identity")
            group = Path("/sys/fs/cgroup") / relative.lstrip("/")
            if (group / "cgroup.kill").exists():
                (group / "cgroup.kill").write_text("1")
    if pending:
        call(["systemctl", "stop", *pending], seconds=8, accepted=(0, 5))


def witness(p, identity):
    stem, parent, ctl, work, expiry = names(p)
    for unit in (work, ctl):
        value = show(unit)
        require(value.get("LoadState") == "not-found" or
                value.get("ActiveState") in ("inactive", "failed"), "unit-not-terminal")
    group = Path(identity["path"])
    require(group == Path("/sys/fs/cgroup") / parent, "witness-path")
    if group.exists():
        require(group.stat().st_ino == identity["inode"], "witness-inode")
        
    base = Path("/run") / stem
    require(not any(line.split()[4].startswith(str(base)) for line in
                    Path("/proc/self/mountinfo").read_text().splitlines()), "owned-mount-remains")
    require(not base.exists(), "owned-path-remains")


def host_root(stage):
    require(os.geteuid() == 0 and sys.platform == "linux", "root-linux-required")
    resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
    p = parameters(stage)
    stem, parent, ctl, work, expiry = names(p)
    base = Path("/run") / stem
    require(not base.exists(), "existing-owner-refuse")
    cgroup(stem + "broker.service", 268435456, 8, 0.1)
    call(["systemctl", "set-property", "--runtime", parent, "MemoryMax=3221225472",
          "MemorySwapMax=0", "TasksMax=64", "CPUQuota=200%"])
    identity = cgroup(parent, 3 * 1024**3, 64, 2)
    require(time.monotonic() - p["start"] < 15, "late-stage")
    # Register independent expiry BEFORE any work mount or task service exists.
    remaining = p["start"] + 570
    require(time.monotonic() < remaining, "late-expiry")
    call(["systemd-run", "--quiet", "--unit=" + expiry, "--on-boot=" + str(remaining) + "s",
          "--timer-property=AccuracySec=1us", "--timer-property=RandomizedDelaySec=0",
          "-p", "RuntimeMaxSec=25", "-p", "TimeoutStopSec=5", "-p", "MemoryMax=268435456",
          "-p", "TasksMax=8", "/usr/bin/python3", "-I", "-S", "-c", EXPIRY, stem])
    packet = b""
    accepted = False
    try:
        base.mkdir(mode=0o700)
        mount("tmpfs", base, "tmpfs", 2 | 4, "size=1073741824,nr_inodes=32768,mode=0700")
        mount(None, base, flags=1 << 18)  # MS_PRIVATE, exact new mount only.
        (base / "clock.json").write_text(json.dumps(p))
        (base / "work").mkdir()
        (base / "gate").write_text("WAIT")
        runtime = math.floor(p["start"] + 570 - time.monotonic())
        systemd_start(ctl, parent, 1024**3, 32, 100, runtime,
                      ["/usr/bin/python3", "-I", "-S", str(stage / "native.py"), "controller", str(stage)])
        call(["systemctl", "set-property", "--runtime", parent, "MemoryMax=3221225472",
              "MemorySwapMax=0", "TasksMax=64", "CPUQuota=200%"])
        identity = cgroup(parent, 3 * 1024**3, 64, 2)
        (base / "group.json").write_text(json.dumps(identity))
        (base / "gate").write_text("GO")
        while time.monotonic() < p["start"] + 570:
            state = show(ctl)
            if state.get("ActiveState") in ("inactive", "failed"):
                accepted = state.get("Result") == "success" and state.get("ExecMainStatus") == "0"
                break
            time.sleep(0.5)
        if (base / "packet.jsonl").is_file():
            with (base / "packet.jsonl").open("rb") as stream:
                st = os.fstat(stream.fileno())
                require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_size <= STREAM_LIMIT - 4096,
                        "packet-limit")
                packet = stream.read(STREAM_LIMIT)
        require(accepted and bool(packet), "work-not-accepted")
    finally:
        stop_owned(p)
        # Stop expiry only after owned members are actually terminal.
        if base.exists():
            if any(line.split()[4] == str(base) for line in Path("/proc/self/mountinfo").read_text().splitlines()):
                call(["umount", str(base)], seconds=5)
            base.rmdir()
        if identity is not None:
            witness(p, identity)
        require(time.monotonic() < p["start"] + 600, "cleanup-deadline")
    require(accepted and identity is not None, "no-positive-result")
    # Only reviewed, bounded packet bytes are exported after actual owned cleanup.
    receipt = {"run": p["run"], "commit": p["commit"], "manifest": p["manifest"],
               "identity": identity, "packet_sha256": hashlib.sha256(packet).hexdigest()}
    with (stage / "BROKER-CLEANUP.json").open("x") as out:
        json.dump(receipt, out)
    # The fixed outside witness buffers these bytes and accepts them only after
    # this broker exits and the entire owned slice is terminal and empty.
    remaining = memoryview(packet)
    os.set_blocking(1, False)
    while remaining:
        require(time.monotonic() < p["start"] + 570, "export-deadline")
        _, ready, _ = select.select([], [1], [], 0.05)
        if ready:
            try:
                remaining = remaining[os.write(1, remaining):]
            except BlockingIOError:
                pass


def controller(stage):
    p = parameters(stage)
    stem, parent, ctl, work, expiry = names(p)
    base = Path("/run") / stem
    while (base / "gate").read_text() != "GO":
        require(time.monotonic() < p["start"] + 20, "gate-timeout")
        time.sleep(0.05)
    cgroup(parent, 3 * 1024**3, 64, 2)
    cgroup(ctl, 1024**3, 32, 1)
    remaining = math.floor(p["start"] + 480 - time.monotonic())
    systemd_start(work, parent, 2 * 1024**3, 32, 100, remaining,
                  ["/usr/bin/python3", "-I", "-S", str(stage / "native.py"), "work", str(stage)])
    while time.monotonic() < p["start"] + 500:
        state = show(work)
        if state.get("ActiveState") in ("inactive", "failed"):
            require(state.get("Result") == "success" and state.get("ExecMainStatus") == "0", "work-service-failed")
            require((base / "packet.jsonl").is_file(), "missing-packet")
            return
        time.sleep(0.2)
    raise RuntimeError("work-service-timeout")


def drop(uid):
    os.setgroups([])
    for cap in range(64):
        require(LIBC.prctl(24, cap, 0, 0, 0) == 0 or ctypes.get_errno() == errno.EINVAL, "cap-drop")
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)
    require(LIBC.prctl(38, 1, 0, 0, 0) == 0, "nnp")
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    text = Path("/proc/self/status").read_text()
    for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        require(int(re.search(r"^" + key + r":\s*(\w+)", text, re.M)[1], 16) == 0, "cap-remains")
    require("NoNewPrivs:\t1" in text, "nnp-readback")


def rootfs(stage, base):
    root = base / "root"
    root.mkdir()
    for name in ("usr",):
        bind("/" + name, root / name)
    for name in ("bin", "sbin", "lib", "lib64"):
        source = Path("/") / name
        if source.is_symlink():
            (root / name).symlink_to(os.readlink(source))
        elif source.exists():
            bind(source, root / name)
    for name in ("work", "tmp", "proc", "dev", "etc", "run", "inputs", "control"):
        (root / name).mkdir(exist_ok=True)
    bind(stage / "inputs", root / "inputs")
    bind(stage, root / "control")
    bind("/etc/ssl/certs", root / "etc/ssl/certs")
    bind("/etc/resolv.conf", root / "etc/resolv.conf")
    for name in ("null", "zero", "random", "urandom"):
        bind("/dev/" + name, root / "dev" / name, readonly=False)
    for name in ("downloads", "python", "wheels", "builder", "src", "sdist", "dist", "fixtures",
                 "receipts", "consumers", "tmp", "empty"):
        target = base / "work" / name
        target.mkdir()
        os.chown(target, 10001, 10001)
        target.chmod(0o755)
        (root / "work" / name).mkdir()
    (root / "etc/passwd").write_text("root:x:0:0::/nonexistent:/bin/false\npkg:x:10001:10001::/nonexistent:/bin/false\n")
    (root / "etc/group").write_text("root:x:0:\npkg:x:10001:\n")
    (root / "etc/nsswitch.conf").write_text("hosts: files dns\npasswd: files\ngroup: files\n")
    (root / "etc/hosts").write_text("127.0.0.1 localhost\n")
    return root


def phase(root, base, role, started, deadline, network=False):
    parent_ns = {name: os.readlink("/proc/self/ns/" + name) for name in ("net", "mnt", "pid")}
    child = os.fork()
    if child == 0:
        try:
            os.setsid()
            flags = 0x20000 | 0x20000000 | 0x08000000 | 0x04000000
            if not network:
                flags |= 0x40000000
            require(LIBC.unshare(flags) == 0, "namespace-refused")
            mount(None, "/", flags=16384 | (1 << 18))
            writable = {"downloads", "wheels"} if role == "download" else (
                {"python", "builder", "tmp", "receipts"} if role == "bootstrap" else
                {"receipts", "tmp"} if role == "final-check" else {"src", "sdist", "dist", "fixtures", "receipts", "consumers", "tmp"})
            for name in ("downloads", "python", "wheels", "builder", "src", "sdist", "dist", "fixtures",
                         "receipts", "consumers", "tmp", "empty"):
                bind(base / "work" / name, root / "work" / name, readonly=name not in writable)
            bind(base / "work/tmp", root / "tmp", readonly=False)
            bind(root, root)
            init = os.fork()
            if init == 0:
                mount("proc", root / "proc", "proc", 2 | 4 | 8)
                os.chroot(root)
                os.chdir("/work/empty")
                drop(10001)
                env = {**ENV, "PYTHONNOUSERSITE": "1", "PIP_CONFIG_FILE": "/dev/null",
                       "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "TMPDIR": "/work/tmp",
                       "LD_LIBRARY_PATH": "/work/python/lib", "OMP_NUM_THREADS": "1",
                       "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                       "PACKAGING_PHASE_DEADLINE": str(deadline),
                       "PACKAGING_PARENT_NAMESPACES": json.dumps(parent_ns),
                       "PACKAGING_NETWORK_PHASE": "1" if network else "0"}
                os.execve("/usr/bin/python3", ["python3", "-I", "-S", "/control/payload.py", role, str(started)], env)
            _, status = os.waitpid(init, 0)
            os._exit(os.waitstatus_to_exitcode(status) if os.WIFEXITED(status) else 1)
        except BaseException:
            os._exit(1)
    while True:
        done, status = os.waitpid(child, os.WNOHANG)
        if done:
            break
        if time.monotonic() >= deadline:
            os.killpg(child, signal.SIGKILL)
            os.waitpid(child, 0)
            raise RuntimeError("phase-timeout-" + role)
        time.sleep(0.02)
    require(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, "phase-failed-" + role)
    # Namespace init's exit kills all descendants; no orphan can survive to the next phase.
    group = next(line.split("::", 1)[1] for line in Path("/proc/self/cgroup").read_text().splitlines()
                 if line.startswith("0::"))
    members = {int(x) for x in (Path("/sys/fs/cgroup") / group.lstrip("/") / "cgroup.procs").read_text().split()}
    require(members == {os.getpid()}, "phase-descendants-remain")


def work_role(stage):
    p = parameters(stage)
    stem, parent, ctl, work, expiry = names(p)
    base = Path("/run") / stem
    cgroup(parent, 3 * 1024**3, 64, 2)
    cgroup(work, 2 * 1024**3, 32, 1)
    require(resource.getrlimit(resource.RLIMIT_AS) == (4 * 1024**3,) * 2, "AS-admission")
    require(resource.getrlimit(resource.RLIMIT_NPROC) == (32, 32), "NPROC-admission")
    require(time.monotonic() < p["start"] + 30, "late-work-entry")
    root = rootfs(stage, base)
    # Confirm that this PID really occupies the admitted exact work cgroup.
    group = next(line.split("::", 1)[1] for line in Path("/proc/self/cgroup").read_text().splitlines()
                 if line.startswith("0::"))
    require(group == "/" + parent + "/" + work, "current-worker-membership")
    phase(root, base, "download", p["start"], p["start"] + 155, network=True)
    phase(root, base, "bootstrap", p["start"], p["start"] + 180)
    require(time.monotonic() < p["start"] + 180, "acquisition-deadline")
    offline_deadline = min(p["start"] + 480, time.monotonic() + 300)
    phase(root, base, "package", p["start"], offline_deadline)
    require(time.monotonic() < p["start"] + 480, "work-deadline")
    # Rebind/check final archives from immutable inputs in a fresh network-empty phase.
    phase(root, base, "final-check", p["start"], offline_deadline)
    require(time.monotonic() < p["start"] + 480, "final-work-deadline")
    receipt = base / "work/receipts/packaging-receipt.json"
    require(receipt.is_file() and receipt.stat().st_size <= 65536, "receipt-limit")
    items = []
    manifest = json.loads((stage / "inputs/STABLE-SOURCE-INPUT-MANIFEST.json").read_text())
    for tool in manifest["proposals"]:
        for filename in tool["expected_artifacts"]:
            items.append((filename, base / "work/dist" / tool["tool"] / filename))
    require(sum(path.stat().st_size for _, path in items) <= 4 * 1024**2, "archive-total-limit")
    items.append(("packaging-receipt.json", receipt))
    total = sum(path.stat().st_size for _, path in items)
    require(total <= 4 * 1024**2 + 65536, "export-byte-limit")
    output = base / "packet.jsonl"
    with output.open("xb") as stream:
        def emit(record):
            raw = ("PKGTWO " + json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
            require(stream.tell() + len(raw) <= STREAM_LIMIT - 4096, "export-stream-limit")
            stream.write(raw)
        emit({"kind": "begin", "run": p["run"], "commit": p["commit"], "manifest": p["manifest"]})
        for filename, path in items:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 4 * 1024**2,
                        "unsafe-export-member")
                data = source.read(4 * 1024**2 + 1)
            require(len(data) == info.st_size, "export-read-size")
            encoded = base64.b64encode(data).decode("ascii")
            chunks = [encoded[i:i + 4096] for i in range(0, len(encoded), 4096)]
            emit({"kind": "file", "name": filename, "bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest(), "chunks": len(chunks)})
            for index, chunk in enumerate(chunks):
                emit({"kind": "chunk", "name": filename, "index": index, "data": chunk})
        emit({"kind": "end", "files": len(items)})


if __name__ == "__main__":
    try:
        role, stage = sys.argv[1:]
        {"host-root": host_root, "controller": controller, "work": work_role}[role](Path(stage))
    except BaseException as error:
        print("PACKAGING_REFUSED " + type(error).__name__, flush=True)
        raise SystemExit(1)
