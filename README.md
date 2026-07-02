# Automatic Inspection Agent

Windows 跳板机部署的项目级服务巡检 AI Agent。通过 SSH 管理 Linux 上的 Java / Docker / Compose / 中间件服务。

## 能力（MVP）

- 混合栈自动发现（Java、Docker、Compose、中间件）
- 持续巡检 + 服务状态检测 + 报错告警
- LangChain 对话：查状态、查日志、扫描、分析建议
- 对话确认后重启服务（systemd / docker / compose）
- Web 控制台 + 飞书告警占位

## 快速开始

```powershell
cd e:\project\automatic-inspection-agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .

python -m agent.main
```

浏览器打开 **http://localhost:8765**，首次访问会自动进入 **Web 初始化向导**：

1. 填写 Linux SSH 信息（IP、用户、私钥路径）并测试连接
2. 配置大模型（OpenAI 兼容 API 或 Ollama）并测试
3. （可选）配置飞书告警
4. 扫描并注册服务 → 进入控制台

所有配置保存到 `data/config.yaml`，日常可在控制台右上角 **「设置」** 中修改，无需手改文件。

## 配置说明

- `hosts[]`：Linux 目标机 SSH 信息
- `services[]`：可通过 Web「扫描服务」自动注册
- `llm.default`：OpenAI 兼容 API 或 Ollama
- `web.auth_token`：可选 Bearer Token

## API 示例

```bash
# 扫描
curl -X POST http://localhost:8765/api/discovery/scan -H "Content-Type: application/json" -d "{\"host_id\":\"prod-01\"}"

# 对话
curl -X POST http://localhost:8765/api/chat/message -H "Content-Type: application/json" -d "{\"message\":\"列出所有服务\"}"

# 立即巡检
curl -X POST http://localhost:8765/api/inspection/run
```

## 架构

- `agent/executor/ssh.py` — SSH 远程执行
- `agent/discovery/` — 服务发现
- `agent/monitor/` — 后台巡检
- `agent/langchain/` — LangChain 对话与诊断
- `agent/remediation/orchestrator.py` — 写操作（重启）

## 注意

- Linux 侧无需安装 Agent，仅需 SSH 权限
- 重启有 15 分钟冷却限制（可配置）
- 飞书完整接入需配置 webhook 或应用凭证
