import builtins
import importlib.util
import os
import sys
from pathlib import Path

real_import = builtins.__import__
test_dirs: tuple[str, ...] = ()
caller_cache: dict[str, bool] = {}
stderr_fd = os.fdopen(os.dup(2), "w")


def log_failed(msg: str) -> None:
    stderr_fd.write(msg + "\n")
    stderr_fd.flush()


def load_test_dirs() -> tuple[str, ...]:
    try:
        lines = Path("/tmp/test_files.txt").read_text().splitlines()
    except FileNotFoundError:
        return ()
    dirs = sorted({str(Path(l.strip()).resolve().parent) + "/" for l in lines if l.strip()})
    # Drop dirs that are subpaths of earlier (shorter) ones.
    return tuple(d for i, d in enumerate(dirs) if not any(d.startswith(dirs[j]) for j in range(i)))


def caller_is_test(caller: str) -> bool:
    if caller in caller_cache:
        return caller_cache[caller]
    try:
        resolved = str(Path(caller).resolve())
        hit = resolved.endswith("/conftest.py") or any(resolved.startswith(d) for d in test_dirs)
    except (OSError, ValueError):
        hit = False
    caller_cache[caller] = hit
    return hit


def patched_import(name, globals=None, locals=None, fromlist=(), level=0):
    if not fromlist:
        return real_import(name, globals, locals, fromlist, level)
    
    caller = (globals or {}).get("__file__", "")
    if not caller or not caller_is_test(caller):
        return real_import(name, globals, locals, fromlist, level)
    
    if level:
        package = (globals or {}).get("__package__")
        module_name = importlib.util.resolve_name("." * level + name, package) if package else name
    else:
        module_name = name
        
    try:
        mod = real_import(name, globals, locals, fromlist, level)
    except (ImportError, ModuleNotFoundError) as exc:
        mod = sys.modules.get(module_name)
        if mod is None:
            raise
        for attr in fromlist:
            if attr == "*" or hasattr(mod, attr):
                continue
            log_failed(f"IMPORT_FAILED: from {module_name} import {attr} ({exc})")
            setattr(mod, attr, None)
        return mod
    
    for attr in fromlist:
        if not hasattr(mod, attr):
            log_failed(f"IMPORT_FAILED: from {module_name} import {attr}")
            setattr(mod, attr, None)
            
    return mod


def pytest_configure(config):
    global test_dirs
    test_dirs = load_test_dirs()
    caller_cache.clear()  # module-load cached decisions under possibly-empty test_dirs


def pytest_unconfigure(config):
    builtins.__import__ = real_import


# Patch at module import time so pytest's own early imports are covered, including plugins that import in global scope.
test_dirs = load_test_dirs()
builtins.__import__ = patched_import
