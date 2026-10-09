"""Reporting-zone calendar arithmetic for canonical UTC energy storage."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from timestamp_model import ISO_TIMESTAMP, reporting_day_bounds


class EnergyClock:
    def __init__(self, reporting_timezone: str):
        self.reporting_timezone = reporting_timezone
        self.zone = ZoneInfo(reporting_timezone)
        self._bounds: dict[date, tuple[datetime, datetime]] = {}

    @staticmethod
    def instant(moment: datetime) -> datetime:
        if not isinstance(moment, datetime) or moment.utcoffset() is None:
            raise ValueError("UTC queries require an aware datetime")
        return moment.astimezone(timezone.utc)

    @classmethod
    def stamp(cls, moment: datetime) -> str:
        return cls.instant(moment).isoformat(timespec="microseconds")

    @classmethod
    def parse(cls, value: str) -> datetime:
        if not isinstance(value, str) or not ISO_TIMESTAMP.fullmatch(value):
            raise ValueError("Invalid canonical energy timestamp")
        moment = datetime.fromisoformat(value)
        if cls.stamp(moment) != value:
            raise ValueError("Energy storage requires fixed-width UTC timestamps")
        return moment.astimezone(timezone.utc)

    def local(self, moment: datetime) -> datetime:
        return self.instant(moment).astimezone(self.zone)

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        if day not in self._bounds:
            bounds = reporting_day_bounds(day, self.reporting_timezone)
            if len(self._bounds) >= 400:
                self._bounds.pop(next(iter(self._bounds)))
            self._bounds[day] = bounds
        return self._bounds[day]

    def period_start(self, moment: datetime, period: str) -> datetime:
        day = self.local(moment).date()
        if period == "week":
            day -= timedelta(days=day.weekday())
        elif period == "month":
            day = day.replace(day=1)
        elif period != "day":
            raise ValueError("Invalid calendar period")
        return self.day_bounds(day)[0]

    def complete_days(self, moment: datetime, days: int) -> tuple[datetime, datetime]:
        if type(days) is not int or days <= 0:
            raise ValueError("Complete-day count must be a positive integer")
        end_day = self.local(moment).date()
        return self.day_bounds(end_day-timedelta(days=days))[0], self.day_bounds(end_day)[0]

    def day_key(self, value: str) -> str:
        return self.local(self.parse(value)).date().isoformat()

    def month_key(self, value: str) -> str:
        return self.day_key(value)[:7]

    def year_key(self, value: str) -> str:
        return self.day_key(value)[:4]

    def minute_key(self, value: str) -> str:
        return self.parse(value).replace(second=0, microsecond=0).isoformat()

    def hour_key(self, value: str) -> str:
        moment = self.parse(value)
        start = self.day_bounds(self.local(moment).date())[0]
        elapsed_hours = int((moment-start).total_seconds() // 3600)
        return self.stamp(start+timedelta(hours=elapsed_hours))

    def buckets(self, start: datetime, end: datetime, *, hourly: bool) -> list[dict]:
        """Calendar bins intersecting [start, end), retaining actual UTC boundaries."""
        start, end = self.instant(start), self.instant(end)
        if end <= start:
            return []
        cursor, boundary = self.day_bounds(self.local(start).date())
        if hourly:
            cursor += timedelta(hours=int((start-cursor).total_seconds() // 3600))
        result = []
        while cursor < end:
            following = min(cursor+timedelta(hours=1), boundary) if hourly else boundary
            result.append({
                "key": self.stamp(cursor) if hourly else self.local(cursor).date().isoformat(),
                "label": self.local(cursor).isoformat(timespec="minutes") if hourly else
                         self.local(cursor).date().isoformat(),
                "start": cursor, "end": following,
            })
            cursor = following
            if cursor == boundary:
                _, boundary = self.day_bounds(self.local(cursor).date())
        return result

    def hour_grid(self, moment: datetime, days: int = 7) -> dict:
        start, end = self.complete_days(moment, days)
        first_day = self.local(start).date()
        bins, groups = [], []
        for index in range(days):
            day = first_day+timedelta(days=index)
            cursor, boundary = self.day_bounds(day)
            day_bins = []
            while cursor < boundary:
                following = min(cursor+timedelta(hours=1), boundary)
                local = self.local(cursor)
                day_bins.append({"key": self.stamp(cursor), "hour": local.isoformat(timespec="minutes"),
                                 "clock": local.strftime("%H:%M"), "offset": local.strftime("%z"),
                                 "minutes": (following-cursor).total_seconds()/60})
                cursor = following
            clocks = [row["clock"] for row in day_bins]
            for row in day_bins:
                repeated = clocks.count(row["clock"]) > 1
                row["tick"] = (f"{row['clock']} {row['offset']}" if repeated else
                               row["clock"][:2] if row["clock"] in {"00:00", "06:00", "12:00", "18:00"}
                               else "")
            bins.extend(day_bins)
            groups.append({"label": day.strftime("%a %m/%d"), "columns": len(day_bins)})
        return {"bins": bins, "day_columns": groups,
                "start": self.stamp(start), "end": self.stamp(end)}
