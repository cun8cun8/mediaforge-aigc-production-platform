from __future__ import annotations

import argparse
from pathlib import Path

from .vertical_slice import run_vertical_slice


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the MediaForge P-1 vertical slice.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/p1-probe"),
        help="Directory for generated artifacts and the manifest.",
    )
    args = parser.parse_args()

    result = run_vertical_slice(args.output)
    print(f"status={result['status']}")
    print(f"jobs={len(result['jobs'])}")
    print(f"final_mp4={result['final_mp4']}")
    print(f"manifest={result['manifest']}")
    print(f"ffmpeg={result['ffmpeg']['executable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
