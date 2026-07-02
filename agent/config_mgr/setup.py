from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent.config_mgr.hosts import build_host_config
from agent.models import FeishuConfig, HostConfig, LLMDefaultConfig, SSHConfig
from agent.settings import UNCHANGED_SECRET, get_settings


class SSHSetupPayload(BaseModel):
    host: str
    port: int = 22
    user: str
    key_file: str | None = None
    password: str | None = None
    use_sudo_su: bool = False
    sudo_password: str | None = None


class HostSetupPayload(BaseModel):
    id: str = "prod-01"
    name: str = "生产服务器"
    ssh: SSHSetupPayload


class LLMSetupPayload(BaseModel):
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int = 4096
    ollama_base_url: str = "http://localhost:11434"


class FeishuSetupPayload(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str | None = None
    alert_chat_id: str = ""


class SetupSavePayload(BaseModel):
    host: HostSetupPayload
    llm: LLMSetupPayload
    feishu: FeishuSetupPayload | None = None
    complete: bool = False


class InlineSSHTestPayload(BaseModel):
    host: SSHSetupPayload


def apply_setup_payload(payload: SetupSavePayload) -> None:
    settings = get_settings()
    config = settings.config

    existing_host = next((h for h in config.hosts if h.id == payload.host.id), None)
    host_config = build_host_config(payload.host, existing_host)

    hosts = list(config.hosts)
    replaced = False
    for index, item in enumerate(hosts):
        if item.id == host_config.id:
            hosts[index] = host_config
            replaced = True
            break
    if not replaced:
        hosts.append(host_config)

    llm_payload = payload.llm
    existing_llm = config.llm.default
    api_key = llm_payload.api_key
    if not api_key or api_key == UNCHANGED_SECRET:
        api_key = existing_llm.api_key

    llm_default = LLMDefaultConfig(
        provider=llm_payload.provider,  # type: ignore[arg-type]
        base_url=llm_payload.base_url,
        api_key=api_key or "",
        model=llm_payload.model,
        temperature=llm_payload.temperature,
        max_tokens=llm_payload.max_tokens,
    )
    llm_config = config.llm.model_copy(
        update={
            "default": llm_default,
            "ollama_base_url": llm_payload.ollama_base_url,
        }
    )

    feishu_config = config.feishu
    if payload.feishu:
        app_secret = payload.feishu.app_secret
        if not app_secret or app_secret == UNCHANGED_SECRET:
            app_secret = config.feishu.app_secret
        feishu_config = FeishuConfig(
            enabled=payload.feishu.enabled,
            app_id=payload.feishu.app_id,
            app_secret=app_secret or "",
            alert_chat_id=payload.feishu.alert_chat_id,
        )

    updated = config.model_copy(
        update={
            "hosts": hosts,
            "active_host_id": config.active_host_id or host_config.id,
            "llm": llm_config,
            "feishu": feishu_config,
            "setup_completed": payload.complete or config.setup_completed,
        }
    )
    settings.save(updated)

    from agent.config_mgr.hosts import _reset_ssh_pool

    _reset_ssh_pool()


async def test_ssh_config(ssh: SSHSetupPayload, existing: HostConfig | None = None) -> dict[str, Any]:
    from agent.executor.ssh import SSHRemoteExecutor

    password = ssh.password
    if password == UNCHANGED_SECRET:
        password = existing.ssh.password if existing else None
        if password is None:
            settings = get_settings()
            matched = next((h for h in settings.config.hosts if h.ssh.host == ssh.host), None)
            password = matched.ssh.password if matched else None

    sudo_password = ssh.sudo_password
    if sudo_password == UNCHANGED_SECRET:
        sudo_password = existing.ssh.sudo_password if existing else None

    host = HostConfig(
        id="test",
        name="test",
        ssh=SSHConfig(
            host=ssh.host,
            port=ssh.port,
            user=ssh.user,
            key_file=ssh.key_file,
            password=password,
            use_sudo_su=ssh.use_sudo_su,
            sudo_password=sudo_password,
        ),
    )
    executor = SSHRemoteExecutor(host)
    try:
        result = await executor.test_connection()
        return {
            "success": result.exit_code == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }
    finally:
        await executor.close()


async def test_llm_config(llm: LLMSetupPayload) -> dict[str, Any]:
    settings = get_settings()
    existing = settings.config.llm.default
    api_key = llm.api_key
    if not api_key or api_key == UNCHANGED_SECRET:
        api_key = existing.api_key

    from agent.langchain.llm_factory import create_chat_model

    try:
        model = create_chat_model(
            provider=llm.provider,
            model=llm.model,
            base_url=llm.base_url,
            api_key=api_key or "",
            ollama_base_url=llm.ollama_base_url,
            temperature=llm.temperature,
            max_tokens=llm.max_tokens,
        )
        response = await model.ainvoke("回复 OK 两个字母即可。")
        content = getattr(response, "content", str(response))
        return {"success": True, "response": str(content)[:200]}
    except Exception as exc:
        return {"success": False, "response": str(exc)}


async def test_feishu_config(feishu: FeishuSetupPayload) -> dict[str, Any]:
    settings = get_settings()
    app_secret = feishu.app_secret
    if not app_secret or app_secret == UNCHANGED_SECRET:
        app_secret = settings.config.feishu.app_secret

    if not feishu.app_id:
        return {"success": False, "message": "请填写 App ID"}
    if not app_secret:
        return {"success": False, "message": "请填写 App Secret"}
    if not feishu.alert_chat_id:
        return {"success": False, "message": "请填写告警 Chat ID（群 oc_ 开头）"}

    from agent.feishu.client import FeishuAPIError, send_feishu_text

    try:
        await send_feishu_text(
            app_id=feishu.app_id,
            app_secret=app_secret,
            chat_id=feishu.alert_chat_id,
            text="【服务巡检 Agent】这是一条测试消息，飞书机器人配置正常，群告警可用。",
        )
        return {
            "success": True,
            "message": f"测试消息已发送到群 {feishu.alert_chat_id}，请在飞书群中查看。",
        }
    except FeishuAPIError as exc:
        return {"success": False, "message": str(exc)}
    except Exception as exc:
        return {"success": False, "message": f"请求失败: {exc}"}
