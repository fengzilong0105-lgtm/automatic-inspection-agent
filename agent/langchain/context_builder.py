from __future__ import annotations

from agent.models import DiagnosisResult, Incident, ServiceConfig
from agent.settings import Settings, get_settings


def build_diagnosis_context(
    incident: Incident,
    service: ServiceConfig,
    log_tail: str,
    status_detail: str,
) -> str:
    return (
        f"Incident: {incident.title}\n"
        f"Service: {service.id} ({service.type.value}) on host {service.host_id}\n"
        f"Severity: {incident.severity.value}\n"
        f"Summary: {incident.summary}\n"
        f"Status: {status_detail}\n"
        f"Recent logs:\n{log_tail[:6000]}\n"
    )


def build_chat_system_prompt(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    hosts = (
        ", ".join(f"{h.id} ({h.ssh.host})" for h in settings.config.hosts) or "暂无主机"
    )
    services = ", ".join(s.id for s in settings.get_enabled_services()) or "暂无已注册服务"
    return (
        "你是项目级服务巡检助手，运行在企业内网 Windows 跳板机上，通过 SSH 管理 Linux 服务。\n"
        "你可以使用工具查询服务状态、部署位置、日志、读取远程文件、执行只读 shell 命令、扫描服务、触发巡检、分析故障。\n"
        "重启等写操作必须由用户明确确认，你不能擅自执行。\n"
        f"当前主机（调用工具时必须使用括号前的 host_id）: {hosts}\n"
        f"当前已注册服务（service_id）: {services}\n\n"
        "【回答格式要求】\n"
        "1. 先给一句「结论」，再展开详情；不要把推测和事实混在一起。\n"
        "2. 用户问题与助手回答语义上要分开；可用小标题：## 结论 / ## 详情 / ## 建议。\n"
        "3. 命令、路径、配置片段必须放在 Markdown 代码块中，例如 ```bash ... ``` 或 ```text ... ```。\n"
        "4. 结构化信息优先用 Markdown 表格。\n"
        "5. 查询部署目录时，必须调用 get_deployment_info；Java 服务 deploy_dir 为空时，提示开启主机 sudo su 或使用「补全部署信息」。\n"
        "6. get_service_status 的 running 由探针判定：middleware+systemd 用 systemctl（并回退 ps/端口），Java 用 ps/jps，Docker 用 docker inspect。\n"
        "7. 中间件扫描来源：systemctl 运行单元、ps 进程名、默认端口（如 redis:6379）；不要仅凭注册字段 null 推断服务宕机。\n"
        "8. 工具返回的 JSON 是事实来源；注册信息里为 null 的字段，应说明可通过扫描/部署查询补全。\n"
        "9. 读取 Linux 文件用 read_remote_file；列目录、查进程、看端口等用 run_remote_command（如 ls -la /path、ps aux | grep java）。\n"
        "10. run_remote_command 禁止 rm/reboot/kill 等破坏性命令；重启服务须用户确认。\n"
        "11. 简洁、可操作，避免冗长道歉和重复。"
    )
