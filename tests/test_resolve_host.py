from agent.settings import Settings


def test_resolve_host_id_by_partial_match(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
setup_completed: true
hosts:
  - id: 公司229服务器
    name: prod
    ssh:
      host: 10.102.1.229
      port: 22
      user: root
  - id: 纳黔现场
    name: nq
    ssh:
      host: 10.144.20.61
      port: 22
      user: wanji
services: []
""",
        encoding="utf-8",
    )
    settings = Settings(config_path=config_path)
    assert settings.resolve_host_id("229") == "公司229服务器"
    assert settings.resolve_host_id("10.144.20.61") == "纳黔现场"
    assert settings.resolve_host_id("公司229服务器") == "公司229服务器"
