"""Prebuilt Lean and ML images (improvement plan A2, ADR 0018).

An image carries its own tools: Lean + Mathlib + REPL (docker/lean.Dockerfile) or the ML environment
(docker/ml.Dockerfile). It runs through the container sandbox with no host system directories bound, so it works where
the host has no Lean or ML environment, and on macOS. Each image describes itself in /etc/quaera-image.json.

QUAERA_LEAN=host|image|auto and QUAERA_ML=host|image|auto choose (auto: the host environment on Linux when it is
installed, otherwise the image if it has been pulled). QUAERA_LEAN_IMAGE and QUAERA_ML_IMAGE name the images.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REGISTRY = "ghcr.io/mikailustuner"


def lean_image() -> str:
    return os.environ.get("QUAERA_LEAN_IMAGE", f"{REGISTRY}/quaera-lean:v4.34.1")    # tag: the pinned Mathlib revision


def ml_image() -> str:
    return os.environ.get("QUAERA_ML_IMAGE", f"{REGISTRY}/quaera-ml:0.1")


class ImageUnavailable(Exception):
    pass


def engine() -> str | None:
    e = os.environ.get("QUAERA_CONTAINER_ENGINE") or next((e for e in ("podman", "docker") if shutil.which(e)), None)
    return e if e and shutil.which(e) else None


def present(image: str) -> bool:
    """The image is available locally (pulling is the user's step: install.sh or `docker pull`)."""
    e = engine()
    try:
        return bool(e) and subprocess.run([e, "image", "inspect", image], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def mode(kind: str) -> str:
    """`host`, `image` or `auto` for "lean" or "ml". Off Linux there is no host mode (the sandbox cannot bind the host's
    tools into a Linux container)."""
    choice = os.environ.get(f"QUAERA_{kind.upper()}", "auto").strip().lower()
    if choice not in ("host", "image", "auto"):
        raise ImageUnavailable(f"unknown QUAERA_{kind.upper()} value: {choice!r} (host, image or auto)")
    return "image" if sys.platform != "linux" else choice


@functools.lru_cache(maxsize=8)
def layout(image: str) -> dict:
    """The image's /etc/quaera-image.json (read once per process; the container has no network)."""
    e = engine()
    if not e:
        raise ImageUnavailable("neither podman nor docker is installed")
    if not present(image):
        raise ImageUnavailable(f"image not pulled: {e} pull {image}")
    proc = subprocess.run([e, "run", "--rm", "--network", "none", image, "cat", "/etc/quaera-image.json"],
                          capture_output=True, text=True, timeout=120)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ImageUnavailable(f"{image} has no readable /etc/quaera-image.json: {proc.stderr.strip()[-300:]}") from None


def copy_out(image: str, src: str, dest: Path) -> Path:
    """Copies a directory out of the image once (e.g. the Mathlib sources for the search index)."""
    if dest.exists():
        return dest
    e = engine()
    if not e:
        raise ImageUnavailable("neither podman nor docker is installed")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    cid = subprocess.run([e, "create", image, "true"], capture_output=True, text=True, timeout=120, check=True).stdout.strip()
    try:
        subprocess.run([e, "cp", f"{cid}:{src}", str(tmp)], capture_output=True, timeout=1800, check=True)
    finally:
        subprocess.run([e, "rm", "-f", cid], capture_output=True, timeout=60)
    tmp.rename(dest)
    return dest
