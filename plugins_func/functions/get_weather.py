"""和风天气实时天气插件，使用当前 API 的 JSON 接口。"""

from typing import TYPE_CHECKING, Any

import httpx

from config.logger import setup_logging
from core.utils.cache.manager import CacheType, cache_manager
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定地点的实时天气。未指定地点时查询默认位置。",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "城市名、地点名，或经度,纬度，例如重庆、北京、106.79,29.72。可选。"},
                "lang": {"type": "string", "description": "语言代码，例如 zh_CN、en_US。默认 zh_CN。"},
            },
            "required": ["lang"],
        },
    },
}

HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "User-Agent": "xiaozhi-server-qweather/1.0",
}


def _language(lang: str) -> str:
    return (lang or "zh_CN").replace("-", "_").split("_")[0]


def _coordinates(value: Any) -> tuple[float, float] | None:
    """解析和风天气的经度,纬度坐标，返回 (纬度, 经度)。"""
    if not isinstance(value, str):
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return None
    try:
        longitude, latitude = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        return None
    return latitude, longitude


def _headers(api_key: str) -> dict[str, str]:
    # 当前 API 文档推荐的 API KEY 请求头，不再把 key 拼进 URL。
    return {**HEADERS, "X-QW-Api-Key": api_key}


async def _get_json(client, url: str, api_key: str, params: dict[str, Any] | None = None):
    try:
        response = await client.get(url, params=params, headers=_headers(api_key))
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.bind(tag=TAG).error("和风天气请求失败：%s", exc)
        return None
    if response.status_code >= 400 or data.get("code") != "200":
        logger.bind(tag=TAG).error(
            f"和风天气接口返回错误：HTTP {response.status_code}，"
            f"code={data.get('code')}，detail={data.get('detail', '')}"
        )
        return None
    return data


async def _lookup_location(client, api_host: str, api_key: str, location: str, lang: str):
    data = await _get_json(
        client, f"https://{api_host}/geo/v2/city/lookup", api_key,
        {"location": location, "number": 1, "lang": _language(lang)},
    )
    locations = data.get("location", []) if data else []
    return locations[0] if locations else None


def _number(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "未知"


def _weather_report(location_name: str, weather: dict[str, Any]) -> str:
    condition = weather.get("condition") or {}
    temperature = weather.get("temperature") or {}
    feels_like = weather.get("feelsLike") or {}
    wind = weather.get("wind") or {}
    direction = wind.get("direction") or {}
    speed = wind.get("speed") or {}
    amount = (weather.get("precipitation") or {}).get("amount") or {}
    try:
        humidity = f"{float(weather.get('humidity')) * 100:.0f}%"
    except (TypeError, ValueError):
        humidity = "未知"

    lines = [
        f"查询位置：{location_name}",
        f"当前天气：{condition.get('text', '未知')}",
        f"温度：{_number(temperature.get('value'))}{temperature.get('unit', '°C')}",
        f"体感：{_number(feels_like.get('value'))}{feels_like.get('unit', '°C')}",
        f"相对湿度：{humidity}",
        f"风向：{direction.get('degree', '未知')}°，风速：{_number(speed.get('value'))}{speed.get('unit', 'm/s')}",
        f"降水量：{_number(amount.get('value'))}{amount.get('unit', 'mm')}",
    ]
    if weather.get("visibility"):
        visibility = weather["visibility"]
        lines.append(f"能见度：{_number(visibility.get('value'), 0)}{visibility.get('unit', 'm')}")
    if weather.get("uvIndex") is not None:
        lines.append(f"紫外线指数：{weather['uvIndex']}")
    return "\n".join(lines)


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def get_weather(conn: "ConnectionHandler", location: str = None, lang: str = "zh_CN"):
    weather_config = conn.config.get("plugins", {}).get("get_weather", {})
    api_host = str(weather_config.get("api_host", "")).strip().removeprefix("https://").rstrip("/")
    api_key = str(weather_config.get("api_key", "")).strip()
    default_location = str(weather_config.get("default_location", "106.79,29.72"))
    default_name = str(weather_config.get("default_location_name", "默认位置"))
    fixed_location = bool(weather_config.get("fixed_location", False))
    if not api_host or not api_key:
        return ActionResponse(Action.REQLLM, None, "和风天气 api_host 或 api_key 未配置")

    requested = bool(location and location.strip())
    query_location = location.strip() if requested else default_location
    coordinates = _coordinates(query_location)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
        if coordinates:
            latitude, longitude = coordinates
            location_name = default_name if not requested else query_location
        else:
            if fixed_location and not requested:
                query_location = default_location
                coordinates = _coordinates(query_location)
            if coordinates:
                latitude, longitude = coordinates
                location_name = default_name
            else:
                city = await _lookup_location(client, api_host, api_key, query_location, lang)
                if not city:
                    return ActionResponse(Action.REQLLM, f"未找到地点：{query_location}", None)
                try:
                    latitude, longitude = float(city["lat"]), float(city["lon"])
                except (KeyError, TypeError, ValueError):
                    return ActionResponse(Action.REQLLM, "和风天气返回的地点坐标无效", None)
                location_name = city.get("name") or query_location

        cache_key = f"qweather_current_{latitude:.2f}_{longitude:.2f}_{_language(lang)}"
        cached = cache_manager.get(CacheType.WEATHER, cache_key)
        if cached:
            return ActionResponse(Action.REQLLM, cached, None)
        weather = await _get_json(
            client,
            f"https://{api_host}/weather/v1/current/{latitude:.2f}/{longitude:.2f}",
            api_key,
            {"localTime": "true", "lang": _language(lang)},
        )

    if not weather or not weather.get("condition"):
        return ActionResponse(Action.REQLLM, None, "和风天气未返回有效的实时天气数据")
    report = _weather_report(location_name, weather)
    cache_manager.set(CacheType.WEATHER, cache_key, report)
    return ActionResponse(Action.REQLLM, report, None)
