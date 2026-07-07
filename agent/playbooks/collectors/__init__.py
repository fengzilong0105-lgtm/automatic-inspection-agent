from agent.playbooks.collectors.common import collect_common
from agent.playbooks.collectors.docker import collect_docker
from agent.playbooks.collectors.gc_log import collect_gc_log
from agent.playbooks.collectors.java import collect_java
from agent.playbooks.collectors.process import collect_process

__all__ = [
    "collect_common",
    "collect_docker",
    "collect_gc_log",
    "collect_java",
    "collect_process",
]
