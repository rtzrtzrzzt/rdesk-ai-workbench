"""Pure platform path selection. No environment reads, I/O, mkdir or data moves."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath


@dataclass(frozen=True)
class PlatformPaths:
    data_dir: PurePath
    config_dir: PurePath
    cache_dir: PurePath
    state_dir: PurePath
    log_dir: PurePath


def application_paths(
    *,
    system: str,
    home: str | PurePath,
    environ: Mapping[str, str] | None = None,
    cwd: str | PurePath | None = None,
) -> PlatformPaths:
    """Resolve only lexical paths from caller-supplied context.

    system accepts sys.platform or platform.system() names. XDG variables must be
    absolute per the specification. A relative explicit WORKBENCH_DATA_DIR needs
    an explicit absolute cwd; it is not silently interpreted relative to HOME.
    """
    env = environ or {}
    windows = system.lower() in {"win32", "windows"}
    mac = system.lower() in {"darwin", "macos"}
    if not windows and not mac and not system.lower().startswith("linux"):
        raise ValueError("Unsupported native platform")
    path = PureWindowsPath if windows else PurePosixPath
    user_home = path(home)
    if not user_home.is_absolute():
        raise ValueError("home must be absolute")

    def base(name, default):
        candidate = path(env.get(name, "") or default)
        return candidate if candidate.is_absolute() else path(default)

    if windows:
        local = base("LOCALAPPDATA", user_home / "AppData" / "Local")
        roaming = base("APPDATA", user_home / "AppData" / "Roaming")
        data = local / "AIWorkbench"
        config = roaming / "AIWorkbench"
        cache = data / "Cache"
        state = data / "State"
        logs = data / "Logs"
    elif mac:
        data = user_home / "Library" / "Application Support" / "AIWorkbench"
        config = data / "config"
        cache = user_home / "Library" / "Caches" / "AIWorkbench"
        state = data / "state"
        logs = user_home / "Library" / "Logs" / "AIWorkbench"
    else:
        data = base("XDG_DATA_HOME", user_home / ".local" / "share") / "ai-workbench"
        config = base("XDG_CONFIG_HOME", user_home / ".config") / "ai-workbench"
        cache = base("XDG_CACHE_HOME", user_home / ".cache") / "ai-workbench"
        state = base("XDG_STATE_HOME", user_home / ".local" / "state") / "ai-workbench"
        logs = state / "logs"
    override = env.get("WORKBENCH_DATA_DIR")
    if override:
        if override == "~":
            override = str(user_home)
        elif override.startswith(("~/", "~\\")):
            override = str(user_home / path(override[2:]))
        selected = path(override)
        if not selected.is_absolute():
            if cwd is None or not path(cwd).is_absolute():
                raise ValueError("relative WORKBENCH_DATA_DIR requires an absolute cwd")
            selected = path(cwd) / selected
            if not selected.is_absolute():
                raise ValueError("drive-relative data directory is ambiguous")
        # PurePath retains '..': normalize lexically without consulting symlinks.
        parts = []
        for part in selected.parts:
            if part == "..":
                if parts and parts[-1] != selected.anchor:
                    parts.pop()
            elif part != ".":
                parts.append(part)
        data = path(*parts)
    return PlatformPaths(data, config, cache, state, logs)
