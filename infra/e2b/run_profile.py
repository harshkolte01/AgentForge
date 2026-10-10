"""Trusted root launcher; drops to forge before all repository execution."""

import ctypes
import os
import platform
import socket
import resource
import sys

PROFILES = {
    "python_unittest": ("/usr/local/bin/python3", "-I", "-m", "unittest", "discover", "-s", ".", "-v"),
    "python_pytest": ("/usr/local/bin/python3", "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider"),
    "node_test": ("/usr/bin/node", "--test"),
    "node_npm_test": ("/usr/bin/npm", "test"),
    "node_build": ("/usr/bin/npm", "run", "build"),
}


def deny_network_sockets(libc) -> None:
    """Kernel-enforced network denial, including the provider's metadata link.

    E2B's egress policy permits its link-local IMDS endpoint. Allow only local
    Unix IPC sockets; deny io_uring so it cannot create sockets outside this
    syscall filter. The filter persists across exec and cannot be removed.
    """
    architectures = {"x86_64": (0xC000003E, 41, 101, 310, 311),
                     "aarch64": (0xC00000B7, 198, 117, 270, 271)}
    if platform.machine() not in architectures:
        raise RuntimeError("sandbox_seccomp_architecture_unsupported")
    arch, socket_syscall, ptrace, process_vm_readv, process_vm_writev = architectures[platform.machine()]

    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]

    # seccomp_data: nr at offset 0, audit architecture at 4, first arg at 16.
    # Unexpected architecture kills; x32 and io_uring fail with EPERM.
    rules = [
        (0x20, 0, 0, 4), (0x15, 1, 0, arch), (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
    ]
    deny_branches = [len(rules)]
    rules.append((0x45, 0, 0, 0x40000000))
    for syscall in (425, ptrace, process_vm_readv, process_vm_writev, 438):
        deny_branches.append(len(rules))
        rules.append((0x15, 0, 0, syscall))
    socket_branch = len(rules)
    rules.extend([(0x15, 0, 0, socket_syscall), (0x20, 0, 0, 16)])
    unix_branch = len(rules)
    rules.append((0x15, 0, 0, socket.AF_UNIX))
    deny_index = len(rules)
    rules.append((0x06, 0, 0, 0x00050001))
    allow_index = len(rules)
    rules.append((0x06, 0, 0, 0x7FFF0000))
    for index in deny_branches:
        code, _, jf, value = rules[index]
        rules[index] = (code, deny_index - index - 1, jf, value)
    rules[socket_branch] = (0x15, 0, allow_index - socket_branch - 1, socket_syscall)
    rules[unix_branch] = (0x15, allow_index - unix_branch - 1, 0, socket.AF_UNIX)
    filters = (Filter * len(rules))(*(Filter(*row) for row in rules))
    program = Program(len(rules), filters)
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise RuntimeError("sandbox_network_seccomp_failed")


def main() -> None:
    if os.getuid() != 0 or os.getgid() != 0 or len(sys.argv) != 2:
        raise RuntimeError("sandbox_execution_user_invalid")
    argv = PROFILES.get(sys.argv[1])
    if argv is None:
        raise ValueError("unknown_command_profile")
    # The SDK starts only this fixed trusted script as root. Drop all privileges
    # before installing limits or executing repository code. Root-owned ancestors
    # cannot be ptraced by the untrusted execution UID to bypass its filter.
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    if os.getuid() != 10001 or os.getgid() != 10001 or os.getgroups():
        raise RuntimeError("sandbox_privilege_drop_failed")
    status = open("/proc/self/status", encoding="ascii").read()
    caps = next(line.split(":", 1)[1].strip() for line in status.splitlines() if line.startswith("CapEff:"))
    if int(caps, 16) != 0:
        raise RuntimeError("sandbox_capabilities_remain")
    # Linux PR_SET_NO_NEW_PRIVS: exec may never gain privileges through setuid,
    # file capabilities, or a future template package change.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("sandbox_no_new_privileges_failed")
    # No inherited non-stdio socket or control descriptor may bypass seccomp.
    for descriptor in os.listdir("/proc/self/fd"):
        if int(descriptor) > 2:
            try:
                os.close(int(descriptor))
            except OSError:
                pass  # /proc enumeration's own descriptor is already closed.
    deny_network_sockets(libc)
    for kind, limit in ((resource.RLIMIT_CPU, 180), (resource.RLIMIT_NPROC, 64),
                        (resource.RLIMIT_NOFILE, 128), (resource.RLIMIT_FSIZE, 8_000_000),
                        (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (limit, limit))
    os.umask(0o077)
    # Clear all inherited template/control-plane environment, including tokens.
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/workspace/scratch", "TMPDIR": "/workspace/scratch",
           "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "CI": "1", "npm_config_cache": "/workspace/scratch/npm-cache", "npm_config_update_notifier": "false"}
    os.chdir("/workspace/repo")
    os.execve(argv[0], argv, env)


if __name__ == "__main__":
    main()
