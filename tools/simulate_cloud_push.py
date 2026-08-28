"""模拟云端向 ESP 下发语音消息。

用法：
    python tools/simulate_cloud_push.py --text "云端测试播报"
    python tools/simulate_cloud_push.py --url http://127.0.0.1:8005/api/notify --text "测试"
"""

import argparse
import json
import urllib.request
import urllib.error


def main() -> int:
    parser = argparse.ArgumentParser(description="模拟云端主动下发 ESP TTS")
    parser.add_argument(
        "--url",
        default="http://36.212.7.43:8005/api/notify",
        help="服务端通知接口地址",
    )
    parser.add_argument("--text", default="这是一条云端主动下发的测试语音")
    args = parser.parse_args()

    body = json.dumps({"text": args.text}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        args.url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            print(response.status, response.read().decode("utf-8"))
        return 0
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode("utf-8", errors="replace"))
        return 1
    except urllib.error.URLError as exc:
        print(f"连接服务端失败: {exc.reason}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
