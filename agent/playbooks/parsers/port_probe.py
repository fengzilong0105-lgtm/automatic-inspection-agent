from __future__ import annotations

import re


def parse_ss_listening_ports(ss_output: str) -> set[int]:
    ports: set[int] = set()
    for line in ss_output.splitlines():
        line = line.strip()
        if not line:
            continue
        for token in line.split():
            if ":" not in token:
                continue
            port_part = token.rsplit(":", 1)[-1]
            if port_part.isdigit():
                ports.add(int(port_part))
    return ports


def jar_name_from_path(jar_path: str | None) -> str | None:
    if not jar_path:
        return None
    name = jar_path.strip().split("/")[-1]
    return name or None


def cmdline_matches_jar(cmdline: str, jar_path: str | None) -> bool | None:
    jar_name = jar_name_from_path(jar_path)
    if not jar_name:
        return None
    return jar_name.lower() in (cmdline or "").lower()
