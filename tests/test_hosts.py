from agent.config_mgr.hosts import delete_host, set_active_host, upsert_host
from agent.models import HostConfig, ServiceConfig, ServiceType, SSHConfig
from agent.settings import Settings, reset_settings


def _host(host_id: str, ip: str) -> HostConfig:
    return HostConfig(
        id=host_id,
        name=f"Host {host_id}",
        ssh=SSHConfig(host=ip, port=22, user="root", password="secret"),
    )


def test_upsert_adds_and_updates_host(tmp_path):
    reset_settings()
    config_path = tmp_path / "config.yaml"
    settings = Settings(config_path=config_path)

    import agent.settings as settings_mod

    settings_mod._settings = settings

    upsert_host(_host("h1", "10.0.0.1"))
    assert len(settings.config.hosts) == 1
    assert settings.config.active_host_id == "h1"

    updated = _host("h1", "10.0.0.2")
    updated.name = "Updated"
    upsert_host(updated)
    assert settings.config.hosts[0].ssh.host == "10.0.0.2"
    assert settings.config.hosts[0].name == "Updated"


def test_delete_host_blocks_when_services_bound(tmp_path):
    reset_settings()
    config_path = tmp_path / "config.yaml"
    settings = Settings(config_path=config_path)

    import agent.settings as settings_mod

    settings_mod._settings = settings

    upsert_host(_host("h1", "10.0.0.1"))
    config = settings.config.model_copy(
        update={
            "services": [
                ServiceConfig(
                    id="svc1",
                    host_id="h1",
                    name="app",
                    type=ServiceType.JAVA,
                )
            ]
        }
    )
    settings.save(config)

    try:
        delete_host("h1")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "svc1" in str(exc)


def test_set_active_host_switches_service(tmp_path):
    reset_settings()
    config_path = tmp_path / "config.yaml"
    settings = Settings(config_path=config_path)

    import agent.settings as settings_mod

    settings_mod._settings = settings

    upsert_host(_host("h1", "10.0.0.1"))
    upsert_host(_host("h2", "10.0.0.2"))
    config = settings.config.model_copy(
        update={
            "services": [
                ServiceConfig(id="svc1", host_id="h1", name="a", type=ServiceType.JAVA),
                ServiceConfig(id="svc2", host_id="h2", name="b", type=ServiceType.JAVA),
            ],
            "active_service_id": "svc1",
        }
    )
    settings.save(config)

    set_active_host("h2")
    assert settings.config.active_host_id == "h2"
    assert settings.config.active_service_id == "svc2"
