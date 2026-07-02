from agent.settings import Settings


def test_resolve_host_id_by_partial_match(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
setup_completed: true
hosts:
  - id: prod-east
    name: prod-east
    ssh:
      host: 192.168.10.229
      port: 22
      user: root
  - id: prod-west
    name: prod-west
    ssh:
      host: 192.168.20.61
      port: 22
      user: deploy
services: []
""",
        encoding="utf-8",
    )
    settings = Settings(config_path=config_path)
    assert settings.resolve_host_id("229") == "prod-east"
    assert settings.resolve_host_id("192.168.20.61") == "prod-west"
    assert settings.resolve_host_id("prod-east") == "prod-east"
