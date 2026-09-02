"""Shared thresholds and exit codes used across collectors/findings/report."""

HEAP_WARN_PERCENT = 85
DISK_WATERMARK_DEFAULT_LOW = 85.0
DISK_WATERMARK_DEFAULT_HIGH = 90.0
DISK_WATERMARK_DEFAULT_FLOOD = 95.0
CACHE_SAMPLE_MIN = 100
CACHE_HIT_RATIO_WARN = 50.0
QUERY_COLUMN_WRAP = 80
SLOW_QUERY_WARN_MS = 1000

# Exit codes: 0 = clean report, no findings; 1 = couldn't produce a report at
# all (connection/auth failure); 2 = report produced but findings are present
# (including partial collection failures), for monitoring/cron integration.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FINDINGS = 2
