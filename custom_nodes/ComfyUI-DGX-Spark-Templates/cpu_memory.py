"""Give PyTorch's freed CPU memory back to the system once the queue is idle.

PyTorch's aarch64 build allocates CPU tensors with a copy of mimalloc inside
libc10.so. mimalloc keeps freed pages and purges them only during its own later
allocation work, which an idle server never does, so a model that was moved to
the CPU and released stays resident until a restart: about 16 GB after a
HeartMuLa run. Neither `/free` nor `malloc_trim` reaches it.

mimalloc's `mi_collect(true)` returns all of it, but libc10 does not export the
function. This module finds it in the library's symbol table and takes the load
address from functions the dynamic linker did export. The pages belong to the
prompt worker thread, and only that thread can purge them, so once the queue
has been empty for COMFY_CPU_PURGE_IDLE_SECONDS (default 60, 0 turns the
watcher off) a watcher sets a queue flag. Setting a flag wakes the worker,
which reads the flags at once; a wrapper on that read does the purge there.
Back-to-back jobs keep reusing the memory in between; purging on every free
instead (MIMALLOC_PURGE_DELAY=0) made heavy workflows up to 20% slower.

When anything does not check out (another platform, a torch without mimalloc
or without a symbol table), it logs why at startup and does nothing.
"""

import ctypes
import logging
import os
import struct
import sys
import threading
import time

_log = logging.getLogger(__name__)

POLL_SECONDS = 5
FLAG = "dgx_cpu_purge"
_SHT_SYMTAB, _SHT_DYNSYM, _STT_FUNC = 2, 11, 2
_state = {"collect": None, "version": None}
_lock = threading.Lock()


def _idle_seconds():
    try:
        return float(os.environ.get("COMFY_CPU_PURGE_IDLE_SECONDS", "60") or 0)
    except ValueError:
        return 60.0


def _functions(path, table, names=None, limit=None):
    """Defined function symbols of a little-endian ELF64 file: name -> value."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not a little-endian ELF64 file")
    shoff, = struct.unpack_from("<Q", data, 0x28)
    shentsize, shnum = struct.unpack_from("<HH", data, 0x3A)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize) for i in range(shnum)]
    found = {}
    for _, sh_type, _, _, offset, size, link, _, _, entsize in sections:
        if sh_type != table or not entsize:
            continue
        strings = sections[link][4]
        for i in range(size // entsize):
            name_off, info, _, shndx, value, _ = struct.unpack_from("<IBBHQQ", data, offset + i * entsize)
            if not shndx or (info & 0xF) != _STT_FUNC or not value:
                continue
            end = data.index(b"\0", strings + name_off)
            name = data[strings + name_off:end].decode("ascii", "replace")
            if names is None or name in names:
                found[name] = value
                if limit and len(found) >= limit:
                    return found
    return found


def _resolve():
    """(mi_collect, mimalloc version) from the libc10.so torch loaded, or None and why."""
    if not sys.platform.startswith("linux"):
        return None, "not Linux"
    import torch

    path = os.path.realpath(os.path.join(os.path.dirname(torch.__file__), "lib", "libc10.so"))
    with open("/proc/self/maps") as handle:
        loaded = any(line.rstrip().endswith(".so") and os.path.realpath(line.split()[-1]) == path
                     for line in handle if "libc10.so" in line)
    if not loaded:
        return None, f"{path} is not loaded"
    hidden = _functions(path, _SHT_SYMTAB, {"mi_collect", "mi_version"})
    if len(hidden) != 2:
        return None, "libc10.so carries no mimalloc symbols"
    lib = ctypes.CDLL(path)
    exported = _functions(path, _SHT_DYNSYM, limit=8)
    bases = {ctypes.cast(getattr(lib, name), ctypes.c_void_p).value - value
             for name, value in exported.items() if hasattr(lib, name)}
    if len(exported) < 4 or len(bases) != 1:
        return None, "could not pin the load address of libc10.so"
    base = bases.pop()
    version = ctypes.CFUNCTYPE(ctypes.c_int)(base + hidden["mi_version"])()
    if not 100 <= version < 1000:
        return None, f"implausible mimalloc version {version}"
    return (ctypes.CFUNCTYPE(None, ctypes.c_bool)(base + hidden["mi_collect"]), version), None


def _rss_anon_mib():
    try:
        with open("/proc/self/status") as handle:
            for line in handle:
                if line.startswith("RssAnon:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def release(reason="request"):
    """Return the CPU memory the calling thread's allocator has freed.

    Pages freed on another thread stay until that thread collects, so call this
    from the prompt worker: inside a node, or through the queue flag below.
    """
    collect = _state["collect"]
    if collect is None:
        return False
    with _lock:
        before, start = _rss_anon_mib(), time.monotonic()
        collect(True)
        after = _rss_anon_mib()
    if before is not None and after is not None:
        _log.info("cpu-purge (%s): returned %d MiB of freed CPU memory in %.2f s",
                  reason, before - after, time.monotonic() - start)
    return True


def _wrap_get_flags(queue):
    """Purge on the prompt worker when it reads the queue flags and finds ours."""
    original = queue.get_flags

    def get_flags(reset=True):
        flags = original(reset=reset)
        if FLAG not in flags:
            return flags
        flags = dict(flags)
        flags.pop(FLAG)
        if reset and queue.get_tasks_remaining() == 0:
            try:
                release("idle")
            except Exception as exc:
                _log.warning("cpu-purge failed: %s", exc)
        return flags

    queue.get_flags = get_flags


def _watch(server, idle_seconds):
    last_busy, purged, last_number = time.monotonic(), True, getattr(server, "number", 0)
    warned = False
    while True:
        time.sleep(POLL_SECONDS)
        try:
            remaining = server.prompt_queue.get_tasks_remaining()
            number = getattr(server, "number", 0)
        except Exception as exc:
            if not warned:
                _log.warning("cpu-purge: cannot read the prompt queue (%s); idle purge paused", exc)
                warned = True
            continue
        now = time.monotonic()
        if remaining or number != last_number:
            last_busy, purged, last_number = now, False, number
        elif not purged and now - last_busy >= idle_seconds:
            try:
                server.prompt_queue.set_flag(FLAG, True)
            except Exception as exc:
                _log.warning("cpu-purge: cannot signal the prompt worker (%s); watcher stopped", exc)
                return
            purged = True


def start():
    try:
        found, reason = _resolve()
    except Exception as exc:
        found, reason = None, f"{type(exc).__name__}: {exc}"
    if found is None:
        _log.info("cpu-purge: disabled, %s", reason)
        return
    _state["collect"], _state["version"] = found
    version = f"{found[1] // 100}.{found[1] // 10 % 10}.{found[1] % 10}"
    idle_seconds = _idle_seconds()
    if idle_seconds <= 0:
        _log.info("cpu-purge: mimalloc %s found; idle watcher off, RAM Cleanup still purges", version)
        return
    try:
        from server import PromptServer
        server = PromptServer.instance
    except Exception as exc:
        _log.info("cpu-purge: mimalloc %s found but no prompt server to watch (%s)", version, exc)
        return
    _wrap_get_flags(server.prompt_queue)
    threading.Thread(target=_watch, args=(server, idle_seconds), name="cpu-purge", daemon=True).start()
    _log.info("cpu-purge: mimalloc %s found; returning freed CPU memory after %.0f s idle",
              version, idle_seconds)
