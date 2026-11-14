"""Fixed environment recipes a device may be asked to run.

Security model: the coordinator can only name a recipe from PROVISION_RECIPES.
Every step below is code in this file, reviewed with the rest of the repo. There
is no code path that turns an incoming string into a shell command.

A worker only runs recipes it was started with --allow-provision ...; without
that flag it refuses and reports why.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""
    command: str | None = None  # what a human would run, shown in dry-run


@dataclass
class RecipeResult:
    recipe: str
    dry_run: bool
    ok: bool
    steps: list[Step] = field(default_factory=list)

    @property
    def log(self) -> str:
        lines = [f"recipe={self.recipe} dry_run={self.dry_run} ok={self.ok}"]
        for step in self.steps:
            mark = "OK" if step.ok else "MISS"
            lines.append(f"[{mark}] {step.name}: {step.detail}")
            if step.command:
                lines.append(f"    run: {step.command}")
        return "\n".join(lines)


def _python_has(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def _file_sha(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _providers() -> list[str]:
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception:  # noqa: BLE001
        return []


def _installer_argv(package: str) -> list[str]:
    """uv-managed venvs have no pip; prefer uv when it is what created this env."""
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if Path(uv).exists():
        return [uv, "pip", "install", "--python", sys.executable, package]
    return [sys.executable, "-m", "pip", "install", "--quiet", package]


def _pip_install(package: str) -> Step:
    argv = _installer_argv(package)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=1800, check=False)
    except Exception as exc:  # noqa: BLE001
        return Step(f"install {package}", False, f"{type(exc).__name__}: {exc}", " ".join(argv))
    if proc.returncode != 0:
        return Step(f"install {package}", False, (proc.stderr or proc.stdout)[-500:], " ".join(argv))
    return Step(f"install {package}", True, "installed")


def _infer(model_dir: Path, dry_run: bool) -> RecipeResult:
    result = RecipeResult("infer", dry_run, True)
    has_gpu = bool(shutil.which("nvidia-smi") or Path("/usr/lib/wsl/lib/nvidia-smi").exists())
    package = "onnxruntime-gpu" if has_gpu else "onnxruntime"
    if _python_has("onnxruntime"):
        engine = "cuda" if "CUDAExecutionProvider" in _providers() else "cpu"
        result.steps.append(Step("onnxruntime", True, f"already installed (engine={engine})"))
    elif dry_run:
        result.steps.append(Step("onnxruntime", False, "missing", " ".join(_installer_argv(package))))
        result.ok = False
    else:
        result.steps.append(_pip_install(package))
        result.ok = all(step.ok for step in result.steps)

    # "an .onnx exists" is not enough: the controlled comparison needs the exact
    # bytes the protocol fixed, so every required model is hash-checked here.
    from .models import required_shas

    required = required_shas()
    if not required:
        result.steps.append(Step("models", False, "manifest 里没有 required 模型", "检查 packages/cef-worker/models.json"))
        result.ok = False
        return result

    for name, want in required.items():
        path = model_dir / f"{name}.onnx"
        if not path.exists():
            result.steps.append(
                Step(f"model {name}", False, f"missing at {path}", f"把 {name}.onnx 放到 {model_dir}")
            )
            result.ok = False
            continue
        actual = _file_sha(path)
        if actual == want:
            result.steps.append(Step(f"model {name}", True, f"sha256 {actual[:16]}… matches manifest"))
        else:
            result.steps.append(
                Step(
                    f"model {name}",
                    False,
                    f"sha mismatch: manifest {want[:16]}… / on disk {actual[:16]}…",
                    f"用协议里的那份覆盖 {path} 后重启 worker",
                )
            )
            result.ok = False
    return result


def _blender(dry_run: bool) -> RecipeResult:
    result = RecipeResult("blender", dry_run, True)
    found = shutil.which("blender") or shutil.which("blender.exe")
    if not found:
        result.steps.append(
            Step("binary", False, "blender not found in PATH", "安装 Blender 4.2+，或 CEF_BLENDER=/path/to/blender")
        )
        result.ok = False
        return result
    result.steps.append(Step("binary", True, found))
    try:
        proc = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=30, check=False)
        version = (proc.stdout or "").strip().splitlines()[0] if proc.stdout else "unknown"
        result.steps.append(Step("version", True, version))
    except Exception as exc:  # noqa: BLE001
        result.steps.append(Step("version", False, f"{type(exc).__name__}: {exc}"))
        result.ok = False
    return result


def run_recipe(recipe: str, model_dir: Path, dry_run: bool = True) -> RecipeResult:
    if recipe == "infer":
        return _infer(model_dir, dry_run)
    if recipe == "blender":
        return _blender(dry_run)
    result = RecipeResult(recipe, dry_run, False)
    result.steps.append(Step("recipe", False, f"unknown recipe: {recipe}"))
    return result
