from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def load_payload(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Telemetry file must contain one JSON object")
    return payload


def post_telemetry(
    *,
    api_url: str,
    api_key: str,
    payload: dict[str, object],
    timeout_seconds: float,
) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/api/v1/telemetry",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "User-Agent": "highground-edge-client/1.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API returned HTTP {error.code}: {detail}") from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send real or recorded HighGround sensor telemetry to the decision API."
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(__file__).parent / "examples" / "rising-water.json",
        help="Telemetry JSON file",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("HIGHGROUND_API_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("HIGHGROUND_API_KEY", "development-only-change-me"),
    )
    parser.add_argument("--repeat", type=int, default=1, help="Number of samples to send")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between samples")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")
    parser.add_argument(
        "--rise-per-sample",
        type=float,
        default=0.0,
        help="Optional water-level increase in cm after each sample",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeat < 1:
        raise ValueError("--repeat must be >= 1")
    source = load_payload(args.file)

    for index in range(args.repeat):
        payload = deepcopy(source)
        payload["message_id"] = f"msg_edge_{uuid4().hex}"
        payload["captured_at"] = datetime.now(timezone.utc).isoformat()
        if args.rise_per_sample:
            environment = payload.get("environment")
            if not isinstance(environment, dict):
                raise ValueError("environment must be an object")
            increase = index * args.rise_per_sample
            environment["water_level_cm"] = float(environment["water_level_cm"]) + increase
            environment["secondary_water_level_cm"] = (
                float(environment["secondary_water_level_cm"]) + increase
            )

        result = post_telemetry(
            api_url=args.api_url,
            api_key=args.api_key,
            payload=payload,
            timeout_seconds=args.timeout,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if index + 1 < args.repeat:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"edge_client error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
