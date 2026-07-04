from __future__ import annotations

import re
import shlex

_UNIT_FROM_CGROUP = re.compile(r"([a-zA-Z0-9@._-]+\.service)(?:/|$)")
_UNIT_FROM_STATUS = re.compile(r"●\s+(\S+\.service)")


async def probe_systemd_unit(executor, unit: str) -> dict:
    """Check systemd unit active state and main PID."""
    unit_q = shlex.quote(unit)
    active_result = await executor.run(f"systemctl is-active {unit_q} 2>/dev/null || true")
    state = active_result.stdout.strip()
    running = state == "active"

    pid_result = await executor.run(f"systemctl show {unit_q} -p MainPID --value 2>/dev/null || true")
    pid_raw = pid_result.stdout.strip()
    main_pid = int(pid_raw) if pid_raw.isdigit() and int(pid_raw) > 0 else None

    sub_result = await executor.run(
        f"systemctl show {unit_q} -p ActiveState,SubState --value 2>/dev/null || true"
    )
    sub_state = ",".join(part for part in sub_result.stdout.splitlines() if part.strip())

    unit_file_result = await executor.run(
        f"systemctl show {unit_q} -p FragmentPath --value 2>/dev/null || true"
    )
    unit_file = unit_file_result.stdout.strip() or None

    detail_parts = [f"state={state or active_result.stderr or 'unknown'}"]
    if main_pid:
        detail_parts.append(f"MainPID={main_pid}")
    if sub_state:
        detail_parts.append(f"show={sub_state}")
    if unit_file:
        detail_parts.append(f"unit_file={unit_file}")

    return {
        "running": running,
        "main_pid": main_pid,
        "state": state,
        "unit_file": unit_file,
        "detail": ", ".join(detail_parts),
    }


def _normalize_unit_name(unit: str) -> str:
    unit = unit.strip()
    if not unit:
        return unit
    return unit if unit.endswith(".service") else f"{unit}.service"


def _service_unit_candidates(service_id: str, registered_unit: str | None = None) -> list[str]:
    candidates: list[str] = []
    if registered_unit:
        candidates.append(_normalize_unit_name(registered_unit))
    for raw in (
        service_id,
        service_id.replace("-", "_"),
        service_id.replace("_", "-"),
    ):
        unit = _normalize_unit_name(raw)
        if unit not in candidates:
            candidates.append(unit)
    return candidates


def _extract_unit_from_cgroup(text: str) -> str | None:
    for line in text.splitlines():
        match = _UNIT_FROM_CGROUP.search(line)
        if match:
            unit = match.group(1)
            if unit.endswith(".service") and unit not in {"-.service"}:
                return unit
    return None


async def detect_systemd_unit_from_pid(executor, pid: int) -> str | None:
    """Resolve systemd unit name from a running process PID."""
    if pid <= 0:
        return None

    cgroup_result = await executor.run(f"cat /proc/{pid}/cgroup 2>/dev/null || true")
    unit = _extract_unit_from_cgroup(cgroup_result.stdout)
    if unit:
        return unit

    status_result = await executor.run(
        f"systemctl status {pid} --no-pager --lines=0 2>/dev/null || true"
    )
    match = _UNIT_FROM_STATUS.search(status_result.stdout)
    if match:
        return match.group(1)

    return None


async def probe_systemd_for_service(
    executor,
    service_id: str,
    *,
    pid: int | None = None,
    registered_unit: str | None = None,
) -> dict:
    """
    Cross-check whether a service is managed by systemd on the host.

    Returns structured facts with verification level for the chat agent.
    """
    detected_from_pid = await detect_systemd_unit_from_pid(executor, pid) if pid else None
    candidates = _service_unit_candidates(service_id, registered_unit)
    if detected_from_pid and detected_from_pid not in candidates:
        candidates.insert(0, detected_from_pid)

    checked_units: list[str] = []
    active_unit: str | None = None
    unit_probe: dict | None = None
    unit_exists = False

    for unit in candidates:
        checked_units.append(unit)
        probe = await probe_systemd_unit(executor, unit)
        exists_result = await executor.run(
            f"systemctl cat {shlex.quote(unit)} >/dev/null 2>&1 && echo yes || true"
        )
        exists = exists_result.stdout.strip() == "yes"
        if exists:
            unit_exists = True
        if probe["running"]:
            active_unit = unit
            unit_probe = probe
            break
        if exists and unit_probe is None:
            unit_probe = probe

    main_pid_match = bool(
        pid and unit_probe and unit_probe.get("main_pid") and unit_probe["main_pid"] == pid
    )
    managed_by_systemd = False
    if active_unit and unit_probe:
        if detected_from_pid == active_unit:
            managed_by_systemd = True
        elif main_pid_match:
            managed_by_systemd = True
        elif pid is None and unit_probe.get("running"):
            managed_by_systemd = True

    verification = "unverified"
    if managed_by_systemd:
        verification = "verified_systemd"
    elif active_unit or detected_from_pid:
        verification = "partial"
    elif unit_exists:
        verification = "unit_file_only"
    else:
        verification = "not_found"

    notes: list[str] = []
    if registered_unit is None:
        notes.append("注册信息未记录 systemd_unit，已通过主机探测交叉验证。")
    elif registered_unit and not active_unit and not unit_exists:
        notes.append("注册信息中的 systemd_unit 在主机上未找到或未运行。")
    if detected_from_pid and detected_from_pid != active_unit:
        notes.append(f"PID cgroup 指向 {detected_from_pid}。")

    return {
        "managed_by_systemd": managed_by_systemd,
        "active_unit": active_unit,
        "detected_from_pid": detected_from_pid,
        "registered_unit": registered_unit,
        "checked_units": checked_units,
        "unit_probe": unit_probe,
        "unit_file_exists": unit_exists,
        "main_pid_match": main_pid_match,
        "verification": verification,
        "source": "live_probe",
        "notes": notes,
    }
