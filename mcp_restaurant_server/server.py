"""WebSocket MCP service for IP geolocation and nearby restaurant search."""

import json
import ipaddress
import os
import random
import sys
import asyncio
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import websockets
import yaml


HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "8766"))
MCP_TOKEN = os.getenv("MCP_TOKEN", "")
IPINFO_TOKEN = os.getenv("IPINFO_TOKEN", "")
AMAP_URL = "https://restapi.amap.com/v3/place/around"


def load_restaurant_config() -> dict:
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", ".config.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file) or {}
        return config.get("mcp", {}).get("restaurant", {})
    except (OSError, yaml.YAMLError):
        return {}


RESTAURANT_CONFIG = load_restaurant_config()
AMAP_KEY = RESTAURANT_CONFIG.get("amap_key", "")
FIXED_LOCATION = RESTAURANT_CONFIG.get("location", {})

TOOL = {
    "name": "find_nearby_restaurants",
    "description": "根据当前公网IP定位位置，并查询附近的餐馆。IP定位通常只能精确到城市或区域。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "餐馆类型或关键词，例如火锅、粤菜、咖啡店。默认是餐馆。",
            },
            "radius": {
                "type": "integer",
                "description": "搜索半径，单位米，范围100到50000，默认3000。",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回多少家，范围1到20，默认10。",
            },
            "ip": {
                "type": "string",
                "description": "可选的公网IP，留空则自动检测当前服务出口IP，便于测试。",
            },
            "latitude": {
                "type": "number",
                "description": "可选的精确纬度；与longitude同时提供时优先使用。",
            },
            "longitude": {
                "type": "number",
                "description": "可选的精确经度；与latitude同时提供时优先使用。",
            },
        },
        "required": [],
    },
}

MEAL_TOOL = {
    "name": "recommend_meal",
    "description": "当用户询问今天早上、早餐、中午、午餐、晚上或晚餐吃什么时，结合当前位置附近餐馆随机推荐一家。不要在普通聊天中主动调用。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "meal_period": {
                "type": "string",
                "enum": ["早餐", "午餐", "晚餐"],
                "description": "用餐时段：早餐、午餐或晚餐。",
            },
            "keyword": {
                "type": "string",
                "description": "可选的菜系或餐馆类型，例如火锅、粤菜、面馆。",
            },
            "radius": {
                "type": "integer",
                "description": "搜索半径，单位米，默认3000。",
            },
            "ip": {
                "type": "string",
                "description": "可选的公网IP，留空则自动检测当前服务出口IP。",
            },
            "latitude": {
                "type": "number",
                "description": "可选的精确纬度；与longitude同时提供时优先使用。",
            },
            "longitude": {
                "type": "number",
                "description": "可选的精确经度；与latitude同时提供时优先使用。",
            },
        },
        "required": ["meal_period"],
    },
}


async def fetch_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> dict:
    try:
        response = await client.get(url, **kwargs)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"请求服务超时: {url}") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"请求服务失败: {url} ({exc})") from exc
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("定位服务返回格式错误")
    return data


async def locate_ip(client: httpx.AsyncClient, ip: str = "") -> dict:
    try:
        if ip and ipaddress.ip_address(ip).is_private:
            ip = ""
    except ValueError:
        ip = ""
    if not ip:
        data = await fetch_json(client, "https://api.ipify.org", params={"format": "json"})
        ip = data.get("ip", "")
    if not ip:
        raise RuntimeError("无法获取当前公网IP")

    params = {"token": IPINFO_TOKEN} if IPINFO_TOKEN else {}
    try:
        location = await fetch_json(client, f"https://ipinfo.io/{ip}/json", params=params)
        coordinates = location.get("loc", "").split(",")
        if len(coordinates) != 2:
            raise RuntimeError("IP定位服务未返回有效经纬度")
    except Exception:
        location = await fetch_json(client, f"https://ipapi.co/{ip}/json/")
        coordinates = [location.get("latitude"), location.get("longitude")]
        if not all(value is not None for value in coordinates):
            raise RuntimeError("备用IP定位服务未返回有效经纬度")

    return {
        "ip": ip,
        "latitude": float(coordinates[0]),
        "longitude": float(coordinates[1]),
        "city": location.get("city", ""),
        "region": location.get("region", ""),
        "country": location.get("country", ""),
    }


def get_configured_location() -> dict | None:
    try:
        latitude = float(FIXED_LOCATION.get("latitude"))
        longitude = float(FIXED_LOCATION.get("longitude"))
    except (AttributeError, TypeError, ValueError):
        return None
    return {
        "ip": "",
        "latitude": latitude,
        "longitude": longitude,
        "city": FIXED_LOCATION.get("city", ""),
        "region": FIXED_LOCATION.get("region", ""),
        "country": FIXED_LOCATION.get("country", ""),
        "source": "config",
    }


async def resolve_location(client: httpx.AsyncClient, arguments: dict) -> dict:
    try:
        latitude = float(arguments.get("latitude"))
        longitude = float(arguments.get("longitude"))
        return {"ip": "", "latitude": latitude, "longitude": longitude, "source": "tool"}
    except (TypeError, ValueError):
        configured = get_configured_location()
        return configured or await locate_ip(client, str(arguments.get("ip") or "").strip())


async def find_restaurants(arguments: dict) -> str:
    if not AMAP_KEY:
        raise RuntimeError("MCP服务未配置 AMAP_KEY")

    requested_keyword = str(arguments.get("keyword") or "").strip()[:50]
    keyword = requested_keyword or "餐馆"
    radius = max(100, min(int(arguments.get("radius", 3000)), 50000))
    limit = max(1, min(int(arguments.get("limit", 10)), 20))

    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        location = await resolve_location(client, arguments)
        params = {
            "key": AMAP_KEY,
            "location": f"{location['longitude']},{location['latitude']}",
            "keywords": requested_keyword,
            "types": "" if requested_keyword else "050000",
            "radius": radius,
            "sortrule": "distance",
            "offset": limit,
            "page": 1,
            "extensions": "all",
            "output": "json",
        }
        data = await fetch_json(client, AMAP_URL, params=params)

    if data.get("status") != "1":
        raise RuntimeError(f"高德地图查询失败：{data.get('info', '未知错误')}")

    restaurants = []
    for poi in data.get("pois", [])[:limit]:
        restaurants.append(
            {
                "name": poi.get("name", ""),
                "type": poi.get("type", ""),
                "address": poi.get("address", ""),
                "tel": poi.get("tel", ""),
                "distance_m": poi.get("distance", ""),
                "rating": poi.get("biz_ext", {}).get("rating", ""),
            }
        )

    result = {
        "location": location,
        "keyword": keyword,
        "radius_m": radius,
        "count": len(restaurants),
        "restaurants": restaurants,
        "notice": "公网IP定位可能存在较大误差，结果仅供参考。",
    }
    return json.dumps(result, ensure_ascii=False)


async def recommend_meal(arguments: dict) -> str:
    period = str(arguments.get("meal_period") or "").strip()
    if period not in {"早餐", "午餐", "晚餐"}:
        raise RuntimeError("meal_period 必须是 早餐、午餐 或 晚餐")

    search_arguments = {
        "keyword": str(arguments.get("keyword") or "餐馆").strip()[:50],
        "radius": arguments.get("radius", 3000),
        "limit": 20,
        "ip": arguments.get("ip", ""),
        "latitude": arguments.get("latitude"),
        "longitude": arguments.get("longitude"),
    }
    search_result = json.loads(await find_restaurants(search_arguments))
    restaurants = search_result.get("restaurants", [])
    if not restaurants:
        return json.dumps({
            "meal_period": period,
            "message": "附近没有找到符合条件的餐馆，请扩大搜索范围或更换关键词。",
            "location": search_result.get("location", {}),
        }, ensure_ascii=False)

    recommendation = random.SystemRandom().choice(restaurants)
    return json.dumps({
        "meal_period": period,
        "recommendation": recommendation,
        "location": search_result.get("location", {}),
        "notice": "这是从附近搜索结果中随机推荐的一家，公网IP定位可能存在较大误差。",
    }, ensure_ascii=False)


async def send_error(websocket, request_id, code: int, message: str) -> None:
    await websocket.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            },
            ensure_ascii=False,
        )
    )


async def handle(websocket):
    query = parse_qs(urlparse(websocket.request.path).query)
    if MCP_TOKEN and query.get("token", [""])[0] != MCP_TOKEN:
        await websocket.close(code=1008, reason="invalid token")
        return

    async for raw_message in websocket:
        try:
            request = json.loads(raw_message)
            method = request.get("method")
            request_id = request.get("id")

            if method == "initialize":
                await websocket.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {"tools": {}},
                                "serverInfo": {
                                    "name": "ip-restaurant-mcp",
                                    "version": "1.0.0",
                                },
                            },
                        }
                    )
                )
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                await websocket.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [TOOL, MEAL_TOOL]}},
                        ensure_ascii=False,
                    )
                )
            elif method == "tools/call":
                name = request.get("params", {}).get("name")
                if name not in {TOOL["name"], MEAL_TOOL["name"]}:
                    await send_error(websocket, request_id, -32601, f"未知工具: {name}")
                    continue
                try:
                    arguments = request.get("params", {}).get("arguments", {})
                    if name == MEAL_TOOL["name"]:
                        text = await recommend_meal(arguments)
                    else:
                        text = await find_restaurants(arguments)
                    result = {"content": [{"type": "text", "text": text}]}
                    await websocket.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False))
                except Exception as exc:
                    await send_error(websocket, request_id, -32000, str(exc) or exc.__class__.__name__)
            elif request_id is not None:
                await send_error(websocket, request_id, -32601, f"不支持的方法: {method}")
        except (json.JSONDecodeError, TypeError) as exc:
            await send_error(websocket, None, -32700, f"无效请求: {exc}")


async def main() -> None:
    if not AMAP_KEY:
        print("警告：未设置 AMAP_KEY，工具调用时会失败", file=sys.stderr)
    print(f"IP餐馆 MCP 服务监听 ws://{HOST}:{PORT}/mcp/")
    async with websockets.serve(handle, HOST, PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
