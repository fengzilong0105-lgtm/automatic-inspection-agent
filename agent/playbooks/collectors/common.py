from __future__ import annotations

import re
import shlex

from agent.models import ServiceConfig
from agent.playbooks.config import DEFAULT_OOM_THRESHOLDS, OOM_LOG_PATTERN, OomRiskThresholds
from agent.playbooks.models import CheckResult, CheckStatus, CollectorState


async def collect_common(
    executor,
    service: ServiceConfig,
    state: CollectorState,
    thresholds: OomRiskThresholds = DEFAULT_OOM_THRESHOLDS,
) -> None:
    state.add_check(
        CheckResult(
            id="service_running",
            name="服务运行状态",
            status=CheckStatus.PASS if state.running else CheckStatus.FAIL,
            detail="运行中" if state.running else "未运行，无法做运行时 OOM 评估",
            source="live_probe",
        )
    )

    metrics = await executor.get_metrics()
    state.host = {
        "cpu_percent": metrics.cpu_percent,
        "memory_percent": metrics.memory_percent,
        "disk_percent": metrics.disk_percent,
        "load_avg": metrics.load_avg,
        "detail": metrics.detail,
    }

    mem = metrics.memory_percent
    if mem is None:
        host_status = CheckStatus.UNKNOWN
        host_detail = "主机内存指标不可用"
    elif mem >= thresholds.host_memory_fail:
        host_status = CheckStatus.FAIL
        host_detail = f"主机内存 {mem:.1f}%"
        state.categories.add("host_oom_killer")
    elif mem >= thresholds.host_memory_warn:
        host_status = CheckStatus.WARN
        host_detail = f"主机内存 {mem:.1f}%"
        state.categories.add("host_oom_killer")
    else:
        host_status = CheckStatus.PASS
        host_detail = f"主机内存 {mem:.1f}%"

    state.add_check(
        CheckResult(
            id="host_memory",
            name="主机内存",
            status=host_status,
            detail=host_detail,
            source="live_probe",
            metrics={"memory_percent": mem},
        )
    )
    if host_status in {CheckStatus.WARN, CheckStatus.FAIL}:
        state.evidence.append(host_detail)

    if service.log_path:
        raw = await executor.tail_log(
            service.log_path,
            lines=thresholds.log_tail_lines,
            pattern=OOM_LOG_PATTERN,
        )
        hits = len([line for line in raw.splitlines() if line.strip()])
        state.log_oom = {"hits": hits, "log_path": service.log_path}
        if hits > 0:
            status = CheckStatus.FAIL
            detail = f"最近日志中命中 OOM 相关关键字 {hits} 次"
            state.categories.add("heap")
            state.critical = True
            state.evidence.append(detail)
        else:
            status = CheckStatus.PASS
            detail = "最近日志未发现 OOM 关键字"
        state.add_check(
            CheckResult(
                id="log_oom_hits",
                name="业务日志 OOM",
                status=status,
                detail=detail,
                source="log",
                metrics={"hits": hits},
            )
        )
    else:
        state.add_check(
            CheckResult(
                id="log_oom_hits",
                name="业务日志 OOM",
                status=CheckStatus.SKIP,
                detail="未配置 log_path",
                source="log",
            )
        )

    if thresholds.allow_dmesg and state.pid:
        dmesg = await executor.run(
            "dmesg -T 2>/dev/null | grep -Ei 'out of memory|oom-kill|killed process' | tail -n 20 || true"
        )
        text = dmesg.stdout or ""
        pid_hits = len(re.findall(rf"\b{state.pid}\b", text))
        if pid_hits > 0:
            state.add_check(
                CheckResult(
                    id="dmesg_oom_killer",
                    name="内核 OOM Killer",
                    status=CheckStatus.FAIL,
                    detail=f"dmesg 中出现本进程 PID {state.pid} 的 OOM 记录",
                    source="live_probe",
                )
            )
            state.categories.add("host_oom_killer")
            state.critical = True
            state.evidence.append("dmesg 显示进程曾被 OOM Killer 关注")
        elif text.strip():
            state.add_check(
                CheckResult(
                    id="dmesg_oom_killer",
                    name="内核 OOM Killer",
                    status=CheckStatus.WARN,
                    detail="主机近期有 OOM 事件，但未明确命中本进程",
                    source="live_probe",
                )
            )
        else:
            state.add_check(
                CheckResult(
                    id="dmesg_oom_killer",
                    name="内核 OOM Killer",
                    status=CheckStatus.PASS,
                    detail="dmesg 近期无 OOM 记录",
                    source="live_probe",
                )
            )
