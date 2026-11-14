"""Fixed environment recipes a device may be asked to run.

Security model: the coordinator can only name a recipe from PROVISION_RECIPES.
Every step below is code in this file, reviewed with the rest of the repo. There
is no code path that turns an incoming string into a shell command.

A worker only runs recipes it was started with \`--allow-provision ...\`; without
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


def _providers() -> list[str]:
    try:
        import onnxruntime as ort

        return list(ort.get_available_providers())
    except Exception:  # noqa: BLE001
        return []


def _pip_install(package: str) -> Step:
    argv = [sys.executable, "-m", "pip", "install", "--quiet", package]
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
        result.steps.append(Step("onnxruntime", False, "missing", f"{sys.executable} -m pip install {package}"))
        result.ok = False
    else:
        result.steps.append(_pip_install(package))
        result.ok = all(step.ok for step in result.steps)

    models = sorted(p.name for p in model_dir.glob("*.onnx")) if model_dir.is_dir() else []
    if models:
        result.steps.append(Step("models", True, ", ".join(models)))
    else:
        result.steps.append(
            Step(
                "models",
                False,
                f"no .onnx in {model_dir}",
                "把 yolo11n.onnx 放进 " + str(model_dir),
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
