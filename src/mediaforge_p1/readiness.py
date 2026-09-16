from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def fetch_readiness(base_url: str, token: str = "") -> dict[str, object]:
    headers = {"Accept": "application/json"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    request = Request(
        f"{base_url.rstrip('/')}/ops/readiness",
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"readiness request failed: HTTP {exc.code} {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"readiness request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("readiness response must be a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check MediaForge production readiness."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("MEDIAFORGE_API_BASE_URL", "http://127.0.0.1:8020"),
    )
    parser.add_argument(
        "--token",
        default=os.getenv("MEDIAFORGE_READINESS_TOKEN", ""),
    )
    args = parser.parse_args()
    try:
        payload = fetch_readiness(args.base_url, args.token)
    except RuntimeError as exc:
        parser.error(str(exc))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
