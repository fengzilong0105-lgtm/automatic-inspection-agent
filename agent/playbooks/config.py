from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OomRiskThresholds:
    host_memory_warn: float = 85.0
    host_memory_fail: float = 92.0
    old_gen_warn: float = 85.0
    old_gen_fail: float = 92.0
    metaspace_warn: float = 90.0
    container_mem_warn: float = 85.0
    container_mem_fail: float = 95.0
    full_gc_count_warn: int = 5
    full_gc_count_fail: int = 10
    rss_xmx_warn_ratio: float = 0.90
    log_tail_lines: int = 2000
    jstat_samples: int = 3
    jstat_interval_ms: int = 5000
    gc_log_tail_lines: int = 500
    prefer_jcmd: bool = True
    allow_jstat: bool = True
    allow_dmesg: bool = True


DEFAULT_OOM_THRESHOLDS = OomRiskThresholds()

OOM_LOG_PATTERN = (
    "OOM|OutOfMemoryError|Out of memory|Java heap space|Metaspace|"
    "Direct buffer memory|GC overhead limit exceeded|"
    "Unable to create new native thread|Killed|OOMKilled"
)


@dataclass(frozen=True)
class CpuRiskThresholds:
    host_cpu_warn: float = 85.0
    host_cpu_fail: float = 95.0
    load_ratio_warn: float = 1.5
    load_ratio_fail: float = 2.0
    process_cpu_warn: float = 50.0
    process_cpu_fail: float = 80.0
    process_cpu_critical: float = 95.0
    container_cpu_warn: float = 80.0
    container_cpu_fail: float = 95.0
    throttle_ratio_warn: float = 0.10
    throttle_ratio_fail: float = 0.25
    java_gc_cpu_warn: float = 30.0
    java_gc_cpu_fail: float = 50.0
    java_threads_warn: int = 500
    java_threads_fail: int = 1000
    restart_count_warn: int = 3
    restart_count_fail: int = 10
    warmup_uptime_seconds: int = 600
    cpu_samples: int = 3
    cpu_sample_interval_seconds: int = 5
    jstat_samples: int = 3
    jstat_interval_ms: int = 5000
    log_tail_lines: int = 2000
    allow_jstack: bool = True
    jstack_on_warn: bool = True


DEFAULT_CPU_THRESHOLDS = CpuRiskThresholds()

CPU_LOG_PATTERN = (
    "Full GC|GC overhead|deadlock|Busy loop|busy loop|"
    "timeout|too many threads|unable to create new native thread|"
    "rebalance|OutOfMemoryError"
)
