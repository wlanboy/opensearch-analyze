"""Human-readable formatting for byte counts, durations, and timestamps."""

from datetime import datetime


class Formatter:
    @staticmethod
    def format_bytes(n: float) -> str:
        n = float(n)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024.0:
                return f"{n:.1f}{unit}"
            n /= 1024.0
        return f"{n:.1f}PB"

    @staticmethod
    def format_ms(n: float) -> str:
        if n >= 1000:
            return f"{n / 1000:.2f}s"
        return f"{n:.0f}ms"

    @staticmethod
    def format_timestamp_ms(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%H:%M:%S")
