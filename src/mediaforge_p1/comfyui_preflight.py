from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .config import (
    load_comfyui_workflow_registry,
    select_comfyui_image_template,
)


def preflight_comfyui_registry(
    registry_path: Path,
    *,
    require_pin: bool = True,
    default_template_id: str | None = None,
) -> dict[str, Any]:
    """Validate reviewed ComfyUI workflow bytes without contacting a Provider."""
    definitions = load_comfyui_workflow_registry(
        registry_path.expanduser(),
        require_pin=require_pin,
    )
    selected_template = select_comfyui_image_template(
        definitions,
        None,
        default_template_id=default_template_id,
    )
    workflows = []
    for definition in definitions.values():
        workflows.append(
            {
                "template_id": definition.template_id,
                "version": definition.version,
                "sha256": definition.sha256,
                "source_file": Path(definition.source_path or "").name,
                "model_requirements": [dict(item) for item in definition.model_requirements],
            }
        )
    return {
        "schema_version": "mediaforge-comfyui-preflight-v1",
        "registry_file": registry_path.name,
        "require_pin": require_pin,
        "default_template_id": selected_template,
        "workflow_count": len(workflows),
        "workflows": workflows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a ComfyUI workflow registry without submitting media."
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument(
        "--default-template-id",
        help="Reviewed image template selected for platform-created image jobs.",
    )
    parser.add_argument(
        "--allow-unpinned",
        action="store_true",
        help="Allow registries without version and SHA-256 pins. Not for production.",
    )
    args = parser.parse_args(argv)
    try:
        report = preflight_comfyui_registry(
            args.registry,
            require_pin=not args.allow_unpinned,
            default_template_id=args.default_template_id,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
