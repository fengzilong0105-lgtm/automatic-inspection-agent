from __future__ import annotations

import shlex


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

    detail_parts = [f"state={state or active_result.stderr or 'unknown'}"]
    if main_pid:
        detail_parts.append(f"MainPID={main_pid}")
    if sub_state:
        detail_parts.append(f"show={sub_state}")

    return {
        "running": running,
        "main_pid": main_pid,
        "state": state,
        "detail": ", ".join(detail_parts),
    }
