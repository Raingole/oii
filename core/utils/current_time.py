"""
时间工具模块
提供统一的时间获取功能
"""

import cnlunar
from datetime import datetime, timedelta, timezone

CHINA_TIMEZONE = timezone(timedelta(hours=8))

WEEKDAY_MAP = {
    "Monday": "星期一",
    "Tuesday": "星期二", 
    "Wednesday": "星期三",
    "Thursday": "星期四",
    "Friday": "星期五",
    "Saturday": "星期六",
    "Sunday": "星期日",
}


def local_now(moment: datetime | None = None) -> datetime:
    """返回中国本地时间；无时区输入按中国墙上时间处理。"""
    if moment is None:
        return datetime.now(CHINA_TIMEZONE)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=CHINA_TIMEZONE)
    return moment.astimezone(CHINA_TIMEZONE)


def get_time_period(moment: datetime | None = None) -> str:
    """把 24 小时制时刻转换成明确的时段，避免 17:xx 被当成凌晨 5:xx。"""
    hour = local_now(moment).hour
    if hour < 6:
        return "凌晨"
    if hour < 9:
        return "早上"
    if hour < 12:
        return "上午"
    if hour < 13:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


def format_current_time(moment: datetime | None = None) -> str:
    """给模型的时间文本附带时段和 24 小时制标记。"""
    now = local_now(moment)
    return f"{now:%H:%M}（{get_time_period(now)}，24 小时制）"


def get_current_time() -> str:
    """
    获取当前时间字符串 (格式: HH:MM)
    """
    return local_now().strftime("%H:%M")


def get_current_date() -> str:
    """
    获取今天日期字符串 (格式: YYYY-MM-DD)
    """
    return local_now().strftime("%Y-%m-%d")


def get_current_weekday() -> str:
    """
    获取今天星期几
    """
    now = local_now()
    return WEEKDAY_MAP[now.strftime("%A")]


def get_current_lunar_date() -> str:
    """
    获取农历日期字符串
    """
    try:
        now = local_now().replace(tzinfo=None)
        today_lunar = cnlunar.Lunar(now, godType="8char")
        return "%s年%s%s" % (
            today_lunar.lunarYearCn,
            today_lunar.lunarMonthCn[:-1],
            today_lunar.lunarDayCn,
        )
    except Exception:
        return "农历获取失败"


def get_current_time_info() -> tuple:
    """
    获取当前时间信息
    返回: (当前时间字符串, 今天日期, 今天星期, 农历日期)
    """
    current_time = format_current_time()
    today_date = get_current_date()
    today_weekday = get_current_weekday()
    lunar_date = get_current_lunar_date()
    
    return current_time, today_date, today_weekday, lunar_date
