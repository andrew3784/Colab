from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the project notebook to executed HTML for browser print-to-PDF.")
    parser.add_argument("--notebook", type=Path, default=Path("cs620_project_efs.ipynb"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/gis"))
    parser.add_argument("--output-name", default="cs620_project_efs_report.html")
    parser.add_argument("--timeout", type=int, default=1_200_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "jupyter",
        "nbconvert",
        "--to",
        "html",
        "--execute",
        str(args.notebook),
        "--output",
        args.output_name,
        "--output-dir",
        str(args.output_dir),
        f"--ExecutePreprocessor.timeout={args.timeout}",
    ]
    env = os.environ.copy()
    env["PYTHONWARNINGS"] = "ignore:Cell is missing an id field"
    subprocess.run(command, check=True, env=env)
    print(f"exported={args.output_dir / args.output_name}")


if __name__ == "__main__":
    main()
