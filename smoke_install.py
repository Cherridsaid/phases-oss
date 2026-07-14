#!/usr/bin/env python3
"""Post-install smoke test: build the package, install it into a throwaway
virtualenv, and exercise it from OUTSIDE the source tree.

`run_tests.py` proves the code against the in-tree `src/` layout. This proves
the *packaging*: that a built wheel installs, that `import phases_oss` works
without the source checkout on the path, and that both console scripts
(`phases`, `phases-multidim`) are wired and runnable. It catches pyproject /
entry-point mistakes that the unit tests cannot see.

Everything happens in temporary directories; the repo is left untouched. Exit
code 0 means the packaged product is installable and runnable.
"""

import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run(cmd, **kw):
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def _venv_bin(venv_dir: Path, name: str) -> Path:
    sub = "Scripts" if sys.platform.startswith("win") else "bin"
    exe = name + (".exe" if sys.platform.startswith("win") else "")
    return venv_dir / sub / exe


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="phases-smoke-") as tmp:
        tmp = Path(tmp)
        dist = tmp / "dist"
        venv_dir = tmp / "venv"

        # 1. build a wheel from the current tree into a temp dist dir
        _run([sys.executable, "-m", "build", "--wheel", "--outdir", str(dist), str(ROOT)])
        wheels = list(dist.glob("*.whl"))
        if not wheels:
            print("SMOKE FAIL: no wheel produced", file=sys.stderr)
            return 1

        # 2. create a clean venv and install the wheel into it
        venv.create(venv_dir, with_pip=True)
        py = _venv_bin(venv_dir, "python")
        _run([str(py), "-m", "pip", "install", "--quiet", str(wheels[0])])

        # 3. import from OUTSIDE the source tree (cwd = tmp, no src/ on path)
        _run([str(py), "-c",
              "import phases_oss; from phases_oss import phases; "
              "from phases_oss.multidim import client, server; "
              "print('import ok')"], cwd=str(tmp))

        # 4. both console scripts resolve and run
        _run([str(_venv_bin(venv_dir, "phases")), "--help"],
             stdout=subprocess.DEVNULL)
        # phases-multidim is the stdio server: feed it one initialize + EOF and
        # confirm it answers on stdout then exits cleanly
        server_out = subprocess.run(
            [str(_venv_bin(venv_dir, "phases-multidim"))],
            input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "PHASES_OSS_HOME": str(tmp / "store")},
        )
        if '"serverInfo"' not in server_out.stdout:
            print("SMOKE FAIL: phases-multidim did not answer initialize:\n%s"
                  % server_out.stdout, file=sys.stderr)
            return 1

        print("SMOKE OK: wheel builds, installs, imports and both scripts run")
        return 0


if __name__ == "__main__":
    sys.exit(main())
