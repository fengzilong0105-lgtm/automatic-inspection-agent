from __future__ import annotations

import shlex

from agent.models import ServiceConfig
from agent.playbooks.config import CPU_LOG_PATTERN, DEFAULT_CPU_THRESHOLDS, CpuRiskThresholds
from agent.playbooks.models import CheckResult, CheckStatus, CpuCollectorState
from agent.playbooks.parsers.cpu_probe import (
    parse_load_ratio,
    parse_pidstat_cpu,
    parse_ps_cpu_samples,
    parse_ps_top_rank,
    summarize_cpu_samples,
)


async def _sample_process_cpu(executor, pid: int, thresholds: CpuRiskThresholds) -> dict:
    interval = thresholds.cpu_sample_interval_seconds
    samples = thresholds.cpu_samples
    quoted_pid = shlex.quote(str(pid))
    cmd = (
        f"if command -v pidstat >/dev/null 2>&1; then "
        f"pidstat -p {quoted_pid} 1 {samples}; "
        f"else for i in $(seq 1 {samples}); do "
        f"ps -p {quoted_pid} -o %cpu= 2>/dev/null; sleep {interval}; done; fi"
    )
    timeout = max(30, interval * samples + 10)
    result = await executor.run(cmd, timeout=timeout)
    values = parse_pidstat_cpu(result.stdout)
    if not values:
        values = parse_ps_cpu_samples(result.stdout)
    summary = summarize_cpu_samples(values)
    summary["sample_window_seconds"] = interval * max(samples - 1, 1)
    summary["method"] = "pidstat" if "pidstat" in (result.stdout or "") else "ps"
    return summary


async def collect_cpu_common(
    executor,
    service: ServiceConfig,
    state: CpuCollectorState,
    thresholds: CpuRiskThresholds = DEFAULT_CPU_THRESHOLDS,
) -> None:
    state.add_check(
        CheckResult(
            id="service_running",
            name="服务运行状态",
            status=CheckStatus.PASS if state.running else CheckStatus.FAIL,
            detail="运行中" if state.running else "未运行",
            source="live_probe",
        )
    )

    cores_result = await executor.run("nproc 2>/dev/null || getconf _NPROCESSORS_ONLN")
    cpu_cores = None
    try:
        cpu_cores = int((cores_result.stdout or "").strip().split()[0])
    except (ValueError, IndexError):
        state.limitations.append("无法读取 CPU 核数")

    metrics = await executor.get_metrics()
    load_ratio = parse_load_ratio(metrics.load_avg, cpu_cores)
    state.host = {
        "cpu_percent": metrics.cpu_percent,
        "load_avg": metrics.load_avg,
        "cpu_cores": cpu_cores,
        "load_ratio": load_ratio,
    }

    host_cpu = metrics.cpu_percent
    if host_cpu is None:
        host_cpu_status = CheckStatus.UNKNOWN
        host_cpu_detail = "主机 CPU 指标不可用"
    elif host_cpu >= thresholds.host_cpu_fail:
        host_cpu_status = CheckStatus.FAIL
        host_cpu_detail = f"主机 CPU {host_cpu:.1f}%"
        state.categories.add("host_saturated")
        state.critical = True
    elif host_cpu >= thresholds.host_cpu_warn:
        host_cpu_status = CheckStatus.WARN
        host_cpu_detail = f"主机 CPU {host_cpu:.1f}%"
        state.categories.add("host_saturated")
    else:
        host_cpu_status = CheckStatus.PASS
        host_cpu_detail = f"主机 CPU {host_cpu:.1f}%"
    state.add_check(
        CheckResult(
            id="host_cpu",
            name="主机 CPU",
            status=host_cpu_status,
            detail=host_cpu_detail,
            source="live_probe",
            metrics={"cpu_percent": host_cpu},
        )
    )

    if load_ratio is None:
        load_status = CheckStatus.UNKNOWN
        load_detail = "load/核数 不可用"
    elif load_ratio >= thresholds.load_ratio_fail:
        load_status = CheckStatus.FAIL
        load_detail = f"load/核数={load_ratio:.2f}（{metrics.load_avg} / {cpu_cores}核）"
        state.categories.add("host_saturated")
    elif load_ratio >= thresholds.load_ratio_warn:
        load_status = CheckStatus.WARN
        load_detail = f"load/核数={load_ratio:.2f}"
        state.categories.add("host_saturated")
    else:
        load_status = CheckStatus.PASS
        load_detail = f"load/核数={load_ratio:.2f}"
    state.add_check(
        CheckResult(
            id="host_load_ratio",
            name="主机负载饱和度",
            status=load_status,
            detail=load_detail,
            source="live_probe",
            metrics={"load_ratio": load_ratio},
        )
    )
    if load_status in {CheckStatus.WARN, CheckStatus.FAIL}:
        state.evidence.append(load_detail)

    if state.running and state.pid:
        cpu_summary = await _sample_process_cpu(executor, state.pid, thresholds)
        state.process_cpu = cpu_summary
        cpu_avg = float(cpu_summary.get("cpu_avg") or 0)
        cpu_max = float(cpu_summary.get("cpu_max") or 0)

        if state.uptime_seconds is not None and state.uptime_seconds < thresholds.warmup_uptime_seconds:
            if cpu_avg >= thresholds.process_cpu_warn:
                proc_status = CheckStatus.WARN
                proc_detail = (
                    f"启动仅 {state.uptime_seconds}s，CPU 采样 avg={cpu_avg}% max={cpu_max}%（可能为预热尖峰）"
                )
                state.limitations.append("服务启动未满 warmup 窗口，CPU 结论可能偏低")
            else:
                proc_status = CheckStatus.PASS
                proc_detail = f"CPU 采样 avg={cpu_avg}% max={cpu_max}%"
        elif cpu_max >= thresholds.process_cpu_critical or cpu_avg >= thresholds.process_cpu_fail:
            proc_status = CheckStatus.FAIL
            proc_detail = f"CPU 采样 avg={cpu_avg}% max={cpu_max}%（{cpu_summary.get('count')}次）"
            state.categories.add("process_hot")
            state.critical = True
        elif cpu_avg >= thresholds.process_cpu_warn:
            proc_status = CheckStatus.WARN
            proc_detail = f"CPU 采样 avg={cpu_avg}% max={cpu_max}%"
            state.categories.add("process_hot")
        else:
            proc_status = CheckStatus.PASS
            proc_detail = f"CPU 采样 avg={cpu_avg}% max={cpu_max}%"

        state.add_check(
            CheckResult(
                id="process_cpu_sample",
                name="进程 CPU 采样",
                status=proc_status,
                detail=proc_detail,
                source="live_probe",
                metrics=cpu_summary,
            )
        )
        if proc_status in {CheckStatus.WARN, CheckStatus.FAIL}:
            state.evidence.append(proc_detail)
            state.next_commands.append(f"pidstat -p {state.pid} 1 5")

        rank_result = await executor.run("ps aux --sort=-%cpu | head -n 8")
        rank_info = parse_ps_top_rank(rank_result.stdout, state.pid)
        state.process_cpu.update(rank_info)
        if rank_info.get("rank"):
            rank = int(rank_info["rank"])
            if rank_info.get("is_top1"):
                rank_status = CheckStatus.PASS if proc_status != CheckStatus.PASS else CheckStatus.PASS
                rank_detail = f"本进程 CPU 排名 #{rank}（主机最高）"
            elif rank <= 3 and proc_status != CheckStatus.PASS:
                rank_status = CheckStatus.WARN
                rank_detail = f"本进程 CPU 排名 #{rank}"
            else:
                rank_status = CheckStatus.PASS
                rank_detail = f"本进程 CPU 排名 #{rank}，非主要耗 CPU 方"
                if load_status in {CheckStatus.WARN, CheckStatus.FAIL}:
                    state.evidence.append("主机负载高，但本服务不是 Top CPU 进程")
            state.add_check(
                CheckResult(
                    id="process_cpu_rank",
                    name="进程 CPU 排名",
                    status=rank_status,
                    detail=rank_detail,
                    source="live_probe",
                    metrics={"rank": rank, "is_top1": rank_info.get("is_top1")},
                )
            )
    else:
        state.add_check(
            CheckResult(
                id="process_cpu_sample",
                name="进程 CPU 采样",
                status=CheckStatus.SKIP,
                detail="服务未运行或无 PID",
                source="live_probe",
            )
        )

    await _collect_restart(executor, service, state, thresholds)
    await _collect_cpu_logs(executor, service, state, thresholds)


async def _collect_restart(
    executor,
    service: ServiceConfig,
    state: CpuCollectorState,
    thresholds: CpuRiskThresholds,
) -> None:
    restarts = 0
    detail = "未知"
    if state.systemd_unit:
        unit = shlex.quote(state.systemd_unit)
        result = await executor.run(f"systemctl show {unit} -p NRestarts --value 2>/dev/null")
        try:
            restarts = int((result.stdout or "0").strip())
            detail = f"systemd NRestarts={restarts}"
        except ValueError:
            detail = "systemd 重启次数不可用"
    elif state.docker.get("restarts") is not None:
        restarts = int(state.docker["restarts"])
        detail = f"docker RestartCount={restarts}"

    state.restart = {"count": restarts, "detail": detail}
    if restarts >= thresholds.restart_count_fail:
        status = CheckStatus.FAIL
        state.categories.add("restart_storm")
        state.critical = True
    elif restarts >= thresholds.restart_count_warn:
        status = CheckStatus.WARN
        state.categories.add("restart_storm")
    else:
        status = CheckStatus.PASS
    state.add_check(
        CheckResult(
            id="restart_stability",
            name="重启稳定性",
            status=status,
            detail=detail,
            source="live_probe",
            metrics={"restarts": restarts},
        )
    )
    if status in {CheckStatus.WARN, CheckStatus.FAIL}:
        state.evidence.append(detail)


async def _collect_cpu_logs(
    executor,
    service: ServiceConfig,
    state: CpuCollectorState,
    thresholds: CpuRiskThresholds,
) -> None:
    if not service.log_path:
        state.add_check(
            CheckResult(
                id="log_cpu_clues",
                name="日志 CPU 线索",
                status=CheckStatus.SKIP,
                detail="未配置 log_path",
                source="log",
            )
        )
        return
    raw = await executor.tail_log(
        service.log_path,
        lines=thresholds.log_tail_lines,
        pattern=CPU_LOG_PATTERN,
    )
    hits = len([line for line in raw.splitlines() if line.strip()])
    if hits > 0:
        status = CheckStatus.WARN
        detail = f"日志命中 CPU/GC/线程 相关关键字 {hits} 次"
        state.evidence.append(detail)
    else:
        status = CheckStatus.PASS
        detail = "日志未发现明显 CPU 相关异常关键字"
    state.add_check(
        CheckResult(
            id="log_cpu_clues",
            name="日志 CPU 线索",
            status=status,
            detail=detail,
            source="log",
            metrics={"hits": hits},
        )
    )
