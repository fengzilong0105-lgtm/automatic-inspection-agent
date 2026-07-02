from agent.settings import Settings


def test_is_setup_needed_when_empty(tmp_path):
    settings = Settings(config_path=tmp_path / "config.yaml")
    assert settings.is_setup_needed() is True


def test_is_setup_needed_when_configured(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
setup_completed: true
hosts:
  - id: prod-01
    name: test
    ssh:
      host: 10.0.0.1
      port: 22
      user: deploy
      key_file: C:/keys/id.pem
services: []
""",
        encoding="utf-8",
    )
    settings = Settings(config_path=config_path)
    assert settings.is_setup_needed() is False
