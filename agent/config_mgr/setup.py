from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent.config_mgr.hosts import build_host_config
from agent.models import (
    FeishuBotConfig,
    FeishuConfig,
    HostConfig,
    LLMDefaultConfig,
    OpsReportConfig,
    OpsReportFeishuConfig,
    SSHConfig,
)
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


class FeishuBotSetupPayload(BaseModel):
    command_enabled: bool = False
    command_chat_id: str = ""
    require_at_mention: bool = True


class FeishuSetupPayload(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str | None = None
    alert_chat_id: str = ""
    bot: FeishuBotSetupPayload = Field(default_factory=FeishuBotSetupPayload)


class OpsReportFeishuSetupPayload(BaseModel):
    archive_folder_token: str = ""
    tenant_subdomain: str = ""
    bitable_app_token: str = ""
    bitable_table_id: str = ""
    notify_chat_id: str = ""


class OpsReportSetupPayload(BaseModel):
    auto_draft_on_incident: bool = False
    auto_publish: bool = False
    initiator_default: str = "运维值班"
    feishu: OpsReportFeishuSetupPayload = Field(default_factory=OpsReportFeishuSetupPayload)


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
    ssh_changed = existing_host is None or existing_host.ssh != host_config.ssh

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
            bot=FeishuBotConfig(
                command_enabled=payload.feishu.bot.command_enabled,
                command_chat_id=payload.feishu.bot.command_chat_id,
                require_at_mention=payload.feishu.bot.require_at_mention,
            ),
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

    if ssh_changed:
        from agent.config_mgr.hosts import _reset_ssh_pool

        _reset_ssh_pool()

    if payload.feishu is not None:
        from agent.feishu.runner import schedule_feishu_bot_restart

        schedule_feishu_bot_restart()


def apply_llm_feishu_payload(
    llm: LLMSetupPayload,
    feishu: FeishuSetupPayload | None,
    ops_report: OpsReportSetupPayload | None = None,
) -> None:
    """Update LLM / Feishu / ops report settings without touching SSH connections."""
    settings = get_settings()
    config = settings.config

    llm_payload = llm
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
    if feishu:
        app_secret = feishu.app_secret
        if not app_secret or app_secret == UNCHANGED_SECRET:
            app_secret = config.feishu.app_secret
        feishu_config = FeishuConfig(
            enabled=feishu.enabled,
            app_id=feishu.app_id,
            app_secret=app_secret or "",
            alert_chat_id=feishu.alert_chat_id,
            bot=FeishuBotConfig(
                command_enabled=feishu.bot.command_enabled,
                command_chat_id=feishu.bot.command_chat_id,
                require_at_mention=feishu.bot.require_at_mention,
            ),
        )

    ops_report_config = config.ops_report
    if ops_report:
        ops_report_config = OpsReportConfig(
            auto_draft_on_incident=ops_report.auto_draft_on_incident,
            auto_publish=ops_report.auto_publish,
            initiator_default=ops_report.initiator_default.strip() or "运维值班",
            feishu=OpsReportFeishuConfig(
                archive_folder_token=ops_report.feishu.archive_folder_token.strip(),
                tenant_subdomain=ops_report.feishu.tenant_subdomain.strip(),
                bitable_app_token=ops_report.feishu.bitable_app_token.strip(),
                bitable_table_id=ops_report.feishu.bitable_table_id.strip(),
                notify_chat_id=ops_report.feishu.notify_chat_id.strip(),
            ),
        )

    updated = config.model_copy(
        update={
            "llm": llm_config,
            "feishu": feishu_config,
            "ops_report": ops_report_config,
        }
    )
    settings.save(updated)

    if feishu is not None:
        from agent.feishu.runner import schedule_feishu_bot_restart

        schedule_feishu_bot_restart()


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

    from agent.brand import PRODUCT_NAME
    from agent.feishu.client import FeishuAPIError, send_feishu_text

    try:
        await send_feishu_text(
            app_id=feishu.app_id,
            app_secret=app_secret,
            chat_id=feishu.alert_chat_id,
            text=f"【{PRODUCT_NAME}】这是一条测试消息，飞书机器人配置正常，群告警可用。",
        )
        return {
            "success": True,
            "message": f"测试消息已发送到群 {feishu.alert_chat_id}，请在飞书群中查看。",
        }
    except FeishuAPIError as exc:
        return {"success": False, "message": str(exc)}
    except Exception as exc:
        return {"success": False, "message": f"请求失败: {exc}"}
