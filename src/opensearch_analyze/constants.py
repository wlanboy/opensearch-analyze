"""Shared thresholds and exit codes used across collectors/findings/report."""

HEAP_WARN_PERCENT = 85
DISK_WATERMARK_DEFAULT_LOW = 85.0
DISK_WATERMARK_DEFAULT_HIGH = 90.0
DISK_WATERMARK_DEFAULT_FLOOD = 95.0
CACHE_SAMPLE_MIN = 100
CACHE_HIT_RATIO_WARN = 50.0
QUERY_COLUMN_WRAP = 80
SLOW_QUERY_WARN_MS = 1000
CPU_WARN_PERCENT = 90
FD_WARN_PERCENT = 80
PENDING_TASKS_WARN = 10
PENDING_TASK_WAIT_WARN_MS = 30_000
# Common sizing guidance: keep shards below ~50GB for recovery/relocation speed.
LARGE_SHARD_BYTES = 50 * 1024 ** 3
# Warn when total shards reach this share of cluster.max_shards_per_node x data nodes.
SHARD_LIMIT_WARN_PERCENT = 80.0

# Index-name prefixes left out of the index table: hidden/system indices and
# the local export indices the Query Insights plugin writes.
SKIPPED_INDEX_PREFIXES = (".", "top_queries-")

# Exit codes: 0 = clean report, no findings; 1 = couldn't produce a report at
# all (connection/auth failure); 2 = report produced but findings are present
# (including partial collection failures), for monitoring/cron integration.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FINDINGS = 2
