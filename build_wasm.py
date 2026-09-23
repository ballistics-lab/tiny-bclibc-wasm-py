"""Compile tiny_bclibc + its wasm wrapper into the two .wasm modules the package loads.

    src/tiny_bclibc/tiny_bclibc_dp.wasm   real_t = double
    src/tiny_bclibc/tiny_bclibc_sp.wasm   real_t = float (TINY_BCLIBC_SINGLE_PRECISION)

Runs automatically from setup.py on every build (wheel, sdist -> wheel, editable `uv sync`), and by
hand as `uv run python build_wasm.py`. Plain Python so it works on Windows too; the same flags as
bclibc's tiny_bclibc/build_wasm.sh.

Sources: bclibc/tiny_bclibc/{include,wasm} from the git submodule (shipped inside the sdist).
Compiler: $TINY_BCLIBC_CC if set (e.g. "clang --sysroot=/opt/wasi-sdk/share/wasi-sysroot"), else
`python -m ziglang cc` -- ziglang is a build requirement in pyproject.toml, so a plain `pip install`
/ `uv build` needs nothing preinstalled. The result must import nothing (checked here), so any bare
WebAssembly host can instantiate it with an empty import object.
"""

import os
import shlex
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TINY_BCLIBC_DIR = os.environ.get("TINY_BCLIBC_DIR") or os.path.join(ROOT, "bclibc", "tiny_bclibc")
OUT_DIR = os.path.join(ROOT, "src", "tiny_bclibc")
# Written into the sdist (see setup.py), where the submodule's git metadata is not available.
VERSION_FILE = os.path.join(ROOT, "src", "tiny_bclibc", "_bclibc_version.txt")

TARGETS = (
    ("tiny_bclibc_dp.wasm", []),
    ("tiny_bclibc_sp.wasm", ["-DTINY_BCLIBC_SINGLE_PRECISION"]),
)


def bclibc_version():
    """`git describe` of the submodule, else the version recorded in the sdist, else "unknown"."""
    try:
        out = subprocess.run(
            ["git", "-C", TINY_BCLIBC_DIR, "describe", "--tags", "--always"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if out:
            return out.removeprefix("v")
    except (OSError, subprocess.CalledProcessError):
        pass
    try:
        with open(VERSION_FILE) as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"


def _compiler():
    cc = os.environ.get("TINY_BCLIBC_CC")
    if cc:
        return shlex.split(cc) + ["--target=wasm32-wasi"]
    return [sys.executable, "-m", "ziglang", "cc", "-target", "wasm32-wasi"]


def _leb128(data, pos):
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def wasm_import_count(data):
    """Number of entries in a module's import section (0 when it has none)."""
    if data[:4] != b"\0asm":
        raise ValueError("not a WebAssembly module")
    pos = 8
    while pos < len(data):
        section_id = data[pos]
        size, pos = _leb128(data, pos + 1)
        if section_id == 2:
            return _leb128(data, pos)[0]
        pos += size
    return 0


def build(out_dir=OUT_DIR, verbose=True):
    """Build both modules into out_dir; return their paths."""
    source = os.path.join(TINY_BCLIBC_DIR, "wasm", "tiny_bclibc_wasm.c")
    if not os.path.isfile(source):
        raise SystemExit(
            f"{source} not found: run `git submodule update --init` (the bclibc submodule must be on a "
            "commit that has tiny_bclibc/wasm/)."
        )
    version = bclibc_version()
    os.makedirs(out_dir, exist_ok=True)
    built = []
    for name, extra in TARGETS:
        out = os.path.join(out_dir, name)
        cmd = _compiler() + [
            "-O2",
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-mexec-model=reactor",
            "-Wl,--no-entry",
            "-s",
            "-I" + os.path.join(TINY_BCLIBC_DIR, "include"),
            f'-DTBW_VERSION="{version}"',
            *extra,
            source,
            "-o",
            out,
        ]
        subprocess.run(cmd, check=True)
        with open(out, "rb") as f:
            data = f.read()
        n = wasm_import_count(data)
        if n:
            raise SystemExit(f"{out} imports {n} item(s); it must import nothing")
        if verbose:
            print(f"Built {out} ({len(data)} bytes, tiny_bclibc {version})")
        built.append(out)
    return built


if __name__ == "__main__":
    build()
