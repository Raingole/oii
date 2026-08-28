#!/usr/bin/env python3
"""Send a one-shot TTS notification through the server cloud push API."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Test server-to-ESP TTS push")
    parser.add_argument(
        "--url",
        default=os.environ.get(
            "XIAOZHI_PUSH_URL", "http://36.212.7.43:8005/api/cloud/push"
        ),
    )
    parser.add_argument(
        "--token", default=os.environ.get("XIAOZHI_CLOUD_TOKEN"),
        help="cloud push token, or set XIAOZHI_CLOUD_TOKEN",
    )
    parser.add_argument("--text", default="我真的放出来了")
    args = parser.parse_args()

    if not args.token:
        parser.error("missing --token or XIAOZHI_CLOUD_TOKEN")

    body = json.dumps({"type": "speak", "text": args.text}, ensure_ascii=False).encode()
    request = Request(
        args.url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Cloud-Token": args.token},
    )
    try:
        with urlopen(request, timeout=20) as response:
            print(response.read().decode("utf-8"))
        return 0
    except HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}", file=sys.stderr)
    except URLError as exc:
        print(f"request failed: {exc.reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
