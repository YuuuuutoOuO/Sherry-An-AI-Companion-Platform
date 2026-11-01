"""
=============================================================================
Sherry Platform - PNGTuber Remix to Web UI Companion Converter
=============================================================================
This tool extracts and packages any PNGTuber Remix (.pngRemix) model into
a fully modular, web-ready companion model for Sherry Web UI.

Usage:
    python convert.py <path_to_model.pngRemix> [options]

Options:
    --output, -o      Output directory (default: web_ui/static/sherry_live2d/models/<model_id>)
    --id              Model identifier (default: derived from filename)
    --name            Display name for UI (default: derived from filename)
    --godot           Path to Godot 4 console executable
                      (default: checks standard locations or PNGTuber-Remix.console.exe)
=============================================================================
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

DEFAULT_GODOT_CANDIDATES: list[str] = [
    r"C:\Users\Yuuto\Downloads\（我没有PNGTuberRemix）橘雪莉桌宠251004\PNGTubeRemix_App\PNGTubeRemix(Windows)\PNGTuber-Remix.console.exe",
    r"C:\Users\Yuuto\Downloads\PNGTubeRemix_App\PNGTubeRemix(Windows)\PNGTuber-Remix.console.exe",
    "godot",
    "godot.exe",
]


def find_godot(override_path: str | None = None) -> str | None:
    if override_path and os.path.isfile(override_path):
        return override_path

    for candidate in DEFAULT_GODOT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
        if shutil.which(candidate):
            return candidate

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert .pngRemix model to Sherry Web UI Companion module")
    parser.add_argument("input", help="Path to the .pngRemix file")
    parser.add_argument("--output", "-o", help="Output directory for the extracted model")
    parser.add_argument("--id", help="Model ID (e.g. remix_chibi)")
    parser.add_argument("--name", help="Display name for Web UI")
    parser.add_argument("--godot", help="Path to Godot 4 console executable")

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.is_file():
        print(f"[Error] Input file not found: {input_path}")
        sys.exit(1)

    godot_bin = find_godot(args.godot)
    if not godot_bin:
        print("[Error] Godot 4 binary not found. Please provide --godot <path_to_godot.console.exe>")
        sys.exit(1)

    model_id = args.id or input_path.stem.replace(" ", "_").lower()
    display_name = args.name or input_path.stem

    script_dir = Path(__file__).parent.resolve()
    project_root = script_dir.parent.parent

    if args.output:
        output_dir = Path(args.output).resolve()
    else:
        output_dir = project_root / "web_ui" / "static" / "sherry_live2d" / "models" / model_id

    output_dir.mkdir(parents=True, exist_ok=True)
    textures_dir = output_dir / "textures"
    textures_dir.mkdir(parents=True, exist_ok=True)

    gdscript_path = script_dir / "extractor.gd"

    print("=" * 60)
    print(f"[*] Converting:   {input_path}")
    print(f"[*] Target Model: {model_id} ({display_name})")
    print(f"[*] Output Dir:   {output_dir}")
    print(f"[*] Godot Binary: {godot_bin}")
    print("=" * 60)

    # Run Godot headless conversion script
    cmd = [
        godot_bin,
        "--headless",
        "-s",
        str(gdscript_path),
        "--",
        str(input_path),
        str(output_dir),
        model_id,
        display_name,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(result.stdout)
    if result.returncode != 0:
        print(f"[Warning/Error] Godot exited with code {result.returncode}")
        if result.stderr:
            print(result.stderr)

    manifest_path = output_dir / "model.json"
    if manifest_path.is_file():
        print(f"\n[+] Successfully generated module at: {output_dir}")
        print(f"[+] Model manifest: {manifest_path}")
    else:
        print(f"\n[-] Extraction completed, please verify files in: {output_dir}")


if __name__ == "__main__":
    main()
