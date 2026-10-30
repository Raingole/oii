"""分享链接解析模块。

支持三种输入格式：
1. 分享短链: https://v.douyin.com/xxx → 302重定向后提取aweme_id
2. 视频长链: https://www.douyin.com/video/7613614740174081320 → 直接正则提取
3. 纯数字ID: 7613614740174081320 → 直接返回
"""

import logging
import re
from urllib.parse import urljoin, urlsplit

import httpx

from ..token_manager import DOUYIN_FIXED_USER_AGENT

logger = logging.getLogger("douyinmcp.video.resolver")

# aweme_id 提取正则：/video/数字、/note/数字、aweme_id=数字
_AWEME_ID_PATTERN = re.compile(r"(?:/video/|/note/|aweme_id=)(\d+)")
_PURE_NUMBER_PATTERN = re.compile(r"^\d{15,25}$")  # 15~25位纯数字
_ALLOWED_HOSTS = ("douyin.com", "iesdouyin.com")


def _validate_share_url(url: str) -> None:
    """Reject non-Douyin hosts and URL features unsafe for a server-side fetch."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or not any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_HOSTS)
    ):
        raise ValueError("仅支持 HTTPS 抖音分享链接")


def _extract_aweme_id(url_or_id: str) -> str | None:
    """从URL或字符串中提取 aweme_id。"""
    # 纯数字直接返回
    if _PURE_NUMBER_PATTERN.match(url_or_id.strip()):
        return url_or_id.strip()

    match = _AWEME_ID_PATTERN.search(url_or_id)
    return match.group(1) if match else None


async def resolve_share_url(share_url: str) -> str:
    """解析分享链接，提取 aweme_id。

    Args:
        share_url: 抖音链接（短链/长链/纯ID均可）

    Returns:
        aweme_id 字符串

    Raises:
        ValueError: 无法从URL中解析出aweme_id
    """
    share_url = share_url.strip()

    # 1. 先尝试从原始输入直接提取（长链接或纯ID）
    direct_id = _extract_aweme_id(share_url)
    if direct_id:
        logger.info(f"直接提取: aweme_id={direct_id}")
        return direct_id

    # 2. 短链接：跟踪302重定向
    headers = {"User-Agent": DOUYIN_FIXED_USER_AGENT}

    _validate_share_url(share_url)
    final_url = share_url
    async with httpx.AsyncClient(follow_redirects=False, timeout=15) as client:
        for _ in range(6):
            _validate_share_url(final_url)
            response = await client.get(final_url, headers=headers)
            if response.status_code not in (301, 302, 303, 307, 308):
                response.raise_for_status()
                break
            location = response.headers.get("location")
            if not location:
                raise ValueError("抖音分享链接重定向缺少目标")
            final_url = urljoin(final_url, location)
        else:
            raise ValueError("抖音分享链接重定向次数过多")

    logger.info("抖音分享链接重定向解析完成")

    # 3. 从最终URL提取
    redirected_id = _extract_aweme_id(final_url)
    if redirected_id:
        logger.info(f"重定向后提取: aweme_id={redirected_id}")
        return redirected_id

    # 4. 无法提取 — 判断是否为过期链接
    if final_url.rstrip("/") in ("https://www.douyin.com", "https://m.douyin.com"):
        raise ValueError(
            "分享链接可能已过期（被重定向到首页）。"
            "请获取新的分享链接或直接提供视频ID。"
        )

    raise ValueError("无法解析抖音视频编号；请提供有效的抖音短链、作品链接或纯数字编号")
