from datetime import date, datetime, time, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


def get_business_timezone() -> ZoneInfo:
    return ZoneInfo(settings.BUSINESS_TIME_ZONE)


def format_business_datetime(value: datetime, *, include_timezone: bool = True) -> str:
    business_timezone = get_business_timezone()
    normalized = value if timezone.is_aware(value) else value.replace(tzinfo=dt_timezone.utc)
    local_value = normalized.astimezone(business_timezone)
    if include_timezone:
        return local_value.strftime('%b %d, %Y %I:%M %p %Z')
    return local_value.strftime('%b %d, %Y %I:%M %p')


def get_business_day_bounds(value: date) -> tuple[datetime, datetime]:
    business_timezone = get_business_timezone()
    start = datetime.combine(value, time.min, tzinfo=business_timezone)
    end = datetime.combine(value, time.max, tzinfo=business_timezone)
    return start.astimezone(dt_timezone.utc), end.astimezone(dt_timezone.utc)


def parse_business_datetime_filter_value(raw_value: str, *, end_of_day: bool) -> str | datetime:
    parsed_date = parse_date(raw_value)
    if parsed_date is not None and len(raw_value) == 10:
        day_start, day_end = get_business_day_bounds(parsed_date)
        return day_end if end_of_day else day_start

    parsed_datetime = parse_datetime(raw_value)
    if parsed_datetime is not None:
        business_timezone = get_business_timezone()
        normalized = (
            parsed_datetime.replace(tzinfo=business_timezone)
            if timezone.is_naive(parsed_datetime)
            else parsed_datetime
        )
        return normalized.astimezone(dt_timezone.utc)

    if parsed_date is None:
        return raw_value

    day_start, day_end = get_business_day_bounds(parsed_date)
    return day_end if end_of_day else day_start