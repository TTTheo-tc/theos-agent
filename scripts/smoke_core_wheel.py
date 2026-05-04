"""Smoke test the built core wheel in an isolated virtual environment."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fail(message: str) -> None:
    raise SystemExit(f"core wheel smoke failed: {message}")


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _venv_exe(venv_dir: Path, name: str) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        rendered = " ".join(cmd)
        details = "\n".join(part for part in (result.stdout, result.stderr) if part)
        _fail(f"`{rendered}` exited {result.returncode}\n{details}")


def _latest_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("theos_agent-*.whl"), key=lambda path: path.stat().st_mtime)
    if not wheels:
        _fail(f"no wheel found under {dist_dir}; run `uv build --wheel` first")
    return wheels[-1]


def _install_wheel(wheel: Path, python: Path, *, cwd: Path, env: dict[str, str]) -> None:
    uv = shutil.which("uv")
    if uv:
        _run([uv, "pip", "install", "--python", str(python), str(wheel)], cwd=cwd, env=env)
        return

    _run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
        cwd=cwd,
        env=env,
    )


def smoke_wheel(wheel: Path) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"

    with tempfile.TemporaryDirectory(prefix="theos-core-wheel-") as tmp:
        tmp_dir = Path(tmp)
        venv_dir = tmp_dir / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        python = _venv_python(venv_dir)

        _install_wheel(wheel, python, cwd=tmp_dir, env=env)

        theos = _venv_exe(venv_dir, "theos")
        for args in (
            [str(theos), "--help"],
            [str(theos), "agent", "--help"],
            [str(theos), "gateway", "--help"],
            [str(theos), "cron", "--help"],
            [str(theos), "report", "--help"],
            [str(theos), "ui", "--help"],
        ):
            _run(args, cwd=tmp_dir, env=env)

        _run(
            [
                str(python),
                "-I",
                str(ROOT / "scripts" / "smoke_core_runtime.py"),
                "--strict-installed",
            ],
            cwd=tmp_dir,
            env=env,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="Path to the built theos-agent wheel. Defaults to latest dist/*.whl.",
    )
    args = parser.parse_args()

    wheel = args.wheel if args.wheel is not None else _latest_wheel(ROOT / "dist")
    smoke_wheel(wheel.resolve())
    print(f"core wheel smoke OK: {wheel.name}")


if __name__ == "__main__":
    main()
