import asyncio

from agent.executor.ssh import SSHRemoteExecutor
from agent.models import HostConfig, ServiceConfig, ServiceType, SSHConfig


class FakeExecutor:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.host = HostConfig(id="h1", name="h1", ssh=SSHConfig(host="1.1.1.1", user="root"))

    async def run(self, cmd: str, timeout: int = 60):
        from agent.models import CommandResult

        for key, value in self.responses.items():
            if key in cmd:
                return CommandResult(stdout=value, stderr="", exit_code=0)
        return CommandResult(stdout="", stderr="", exit_code=1)


def test_middleware_systemd_status_active():
    executor = FakeExecutor(
        {
            "systemctl is-active redis.service": "active",
            "systemctl show redis.service -p MainPID": "1234",
            "systemctl show redis.service -p ActiveState": "active\nrunning",
        }
    )

    class Wrapper:
        host = executor.host

        async def run(self, cmd: str, timeout: int = 60):
            return await executor.run(cmd, timeout)

    wrapper = Wrapper()
    service = ServiceConfig(
        id="redis",
        host_id="h1",
        name="redis",
        type=ServiceType.MIDDLEWARE,
        systemd_unit="redis.service",
    )

    async def _run():
        from agent.executor.systemd_probe import probe_systemd_unit

        probe = await probe_systemd_unit(wrapper, service.systemd_unit)
        assert probe["running"] is True
        assert probe["main_pid"] == 1234

    asyncio.run(_run())


def test_middleware_process_fallback_detects_port():
    executor = FakeExecutor(
        {
            "ps -eo pid,cmd": "",
            "ss -tln": "LISTEN 0 128 *:6379 *:*",
        }
    )

    class Wrapper:
        host = executor.host

        async def run(self, cmd: str, timeout: int = 60):
            return await executor.run(cmd, timeout)

    wrapper = Wrapper()

    async def _run():
        from agent.executor.middleware_probe import probe_middleware_process

        probe = await probe_middleware_process(wrapper, "redis")
        assert probe["running"] is True
        assert probe["port_listening"] is True

    asyncio.run(_run())
