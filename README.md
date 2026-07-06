# SteadyOps

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**SteadyOps** 是一款面向企业内网的 Linux 服务运维 Agent，部署在 **Windows 跳板机**上，通过 SSH 远程管理 Linux 主机上的 Java、Docker、Compose 与中间件服务。它集 **自动发现、持续巡检、智能告警、AI 对话诊断** 于一体，支持桌面应用与 Web 控制台两种形态，并可对接飞书机器人。

---

## 产品简介

### 解决什么问题

在传统运维场景中，跳板机上的工程师需要反复 SSH 登录、手动查进程、翻日志、记服务清单。SteadyOps 将这些重复劳动自动化：

- **自动发现** Linux 上的混合技术栈服务（Java / Docker / Compose / systemd 中间件）
- **后台持续巡检**，检测服务健康状态与日志异常，生成告警
- **AI 对话助手**，用自然语言查状态、读日志、分析故障、执行经确认的重启与文件操作
- **统一控制台**，在 Windows 本机完成配置、监控与排障，无需在 Linux 侧安装 Agent

### 典型使用场景

| 场景 | 说明 |
|------|------|
| 跳板机日常运维 | 工程师在 Windows 上打开 SteadyOps，查看服务概览与告警 |
| 故障排查 | 通过 AI 对话查询日志、交叉验证状态、获取修复建议 |
| 服务变更 | 经用户确认后重启 systemd / Docker / Compose 服务 |
| 团队协作 | 飞书推送告警，群内 @机器人 执行只读查询指令 |

### 部署形态

| 形态 | 入口 | 适用对象 |
|------|------|----------|
| **桌面应用（推荐）** | `SteadyOps.exe` 或 `python -m agent.launcher` | 运维人员日常使用 |
| **Web 控制台** | `http://localhost:8765` 或 `python -m agent.main` | 开发调试、浏览器访问 |
| **飞书机器人** | 配置飞书应用后接入 | 告警通知与群内指令 |

### 运行环境说明

- **Agent 运行在 Windows**（10 / 11 或 Windows Server），通过 SSH 连接 Linux 目标机
- **Linux 侧无需安装 Agent**，仅需 SSH 可达及常规运维命令（`ps`、`docker`、`systemctl` 等）
- **Windows 本机不需要** Docker、MySQL、Redis 等中间件；告警与状态数据存储在本地 SQLite

---

## 安装

### 环境要求

#### Windows 本机（必需）

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11 或 Windows Server |
| Python | **3.11+**（项目自带 `installers/python-3.12.10-amd64.exe` 离线包） |
| 网络 | 能 SSH 到 Linux 目标机（默认 22 端口） |
| 磁盘 | 约 500 MB+（含虚拟环境与依赖） |

#### 大模型（对话/诊断必需，二选一）

| 方案 | 说明 |
|------|------|
| **Ollama（推荐内网）** | 默认 `http://localhost:11434`，需提前 `ollama pull` 拉取模型 |
| **OpenAI 兼容 API** | 在初始化向导中填写 `base_url`、`api_key`、`model` |

> 仅做扫描/巡检可不配大模型，但 AI 对话功能不可用。

#### Linux 目标机（被管服务器）

| 能力 | 用途 |
|------|------|
| SSH | 所有远程操作的基础 |
| `ps` / `grep` / `jps` | Java 进程发现 |
| `systemctl` | systemd 服务状态探针 |
| `ss` 或 `netstat` | 监听端口检测 |
| `docker` / `docker compose` | 容器类服务发现 |
| `curl` | HTTP 健康检查 |
| `python3` + `psutil`（可选） | 主机 CPU/内存/磁盘指标 |

若部署目录属 root（如 `/DATA01/...`），需在主机配置中勾选 **sudo su** 并提供密码。

### 方式一：源码安装（开发 / 内网部署）

**1. 安装 Python 3.11+**

```powershell
# 本地离线安装（推荐）
.\installers\python-3.12.10-amd64.exe
# 安装时务必勾选「Add python.exe to PATH」

# 静默安装（批量部署）
.\installers\python-3.12.10-amd64.exe /passive PrependPath=1 Include_test=0
```

**2. 获取代码并安装依赖**

```powershell
cd e:\project\automatic-inspection-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .
```

**3. （可选）安装 Ollama**

```powershell
# 下载安装 https://ollama.com/download
ollama pull qwen2.5
# 或 minimax-m3:cloud 等你实际使用的模型
```

### 方式二：桌面 exe（免 Python 环境）

开发者在本机构建：

```powershell
pip install -e ".[build]"
.\scripts\build.ps1
# 产物：dist\SteadyOps.exe
```

使用者双击 `SteadyOps.exe` 即可运行，无需安装 Python。大模型若选 Ollama，仍需用户自行安装 Ollama。

| 数据 | 路径（打包版） |
|------|----------------|
| 配置文件 | `%APPDATA%\SteadyOps\data\config.yaml` |
| 告警数据库 | `%APPDATA%\SteadyOps\data\agent.db` |
| 运行日志 | `%APPDATA%\SteadyOps\logs\agent.log` |

开发模式数据存放在项目目录 `data/` 下。

### 部署检查清单

- [ ] Python ≥ 3.11，`pip install -e .` 成功
- [ ] 本机能 SSH 到 Linux 目标机
- [ ] 浏览器能打开 `http://localhost:8765`（Web 模式）或桌面应用正常启动
- [ ] 初始化向导中 SSH 与 LLM 测试通过
- [ ] 「扫描服务」能发现目标机上的服务

---

## 使用

### 启动

```powershell
# 桌面应用（推荐）
.\.venv\Scripts\python.exe -m agent.launcher

# Web 控制台
.\.venv\Scripts\python.exe -m agent.main

# 指定配置与端口
.\.venv\Scripts\python.exe -m agent.main --config data\config.yaml --host 0.0.0.0 --port 8765
```

浏览器访问 **http://localhost:8765**（Web 模式）。

### 首次初始化

首次启动会进入 **初始化向导**，依次完成：

| 步骤 | 内容 |
|------|------|
| 1 | Linux SSH：IP、端口、用户名、密码或私钥；需读 root 目录时勾选 **sudo su** |
| 2 | 大模型：Ollama 或 OpenAI 兼容 API |
| 3 | （可选）飞书告警与应用凭证 |
| 4 | 扫描并注册服务 |

配置保存在 `data\config.yaml`（开发模式）或 `%APPDATA%\SteadyOps\data\config.yaml`（打包版）。日常可在 **设置** 页面修改，无需手改文件。

### 日常使用

桌面应用主界面包含：

| 页面 | 功能 |
|------|------|
| **概览** | 服务状态总览、立即巡检、服务扫描、AI 对话 |
| **告警** | 历史 Incident 列表与详情 |
| **设置** | SSH、大模型、飞书、巡检间隔等配置 |

**AI 对话示例：**

- 「road_control 最近有什么 ERROR？」
- 「列出所有服务及其状态」
- 「分析一下最近的告警」
- 「帮我重启 nginx_service」（需用户确认后执行）

**写操作安全机制：**

- 重启服务、修改/删除远程文件等写操作必须经过用户明确确认
- 重启有 15 分钟冷却限制（可配置 `autonomy.max_restart_per_15min`）
- 询问「如何重启」时只给出说明，不会触发执行

### 飞书接入

配置飞书应用后可实现：

- 告警推送到指定群
- 群内 @机器人 执行只读指令

详细步骤见 [飞书机器人接入指南](docs/feishu-bot-setup.md)。

### API 调用（可选）

```bash
# 扫描服务
curl -X POST http://localhost:8765/api/discovery/scan \
  -H "Content-Type: application/json" \
  -d "{\"host_id\":\"prod-01\"}"

# AI 对话
curl -X POST http://localhost:8765/api/chat/message \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"列出所有服务\"}"

# 立即巡检
curl -X POST http://localhost:8765/api/inspection/run
```

配置 `web.auth_token` 后，API 需携带 `Authorization: Bearer <token>`。

### 开机自启（可选）

创建 `start-agent.bat`：

```bat
@echo off
cd /d e:\project\automatic-inspection-agent
call .venv\Scripts\activate.bat
python -m agent.launcher
```

或使用 Windows **任务计划程序** 设置开机启动。

---

## 能力

### 服务发现与巡检

- **混合栈自动发现**：Java 进程、Docker 容器、Docker Compose 项目、systemd 中间件
- **持续后台巡检**：按配置间隔检测服务健康状态（默认 60 秒）
- **日志异常检测**：滑动窗口内 ERROR 日志超阈值触发告警
- **HTTP 健康检查**：支持 `health_url` 探活
- **主机指标采集**：CPU、内存、磁盘（需 Linux 侧 `python3` + `psutil`）

### AI 对话与诊断

基于 **LangChain + LangGraph ReAct** 架构，Agent 可自主决定「思考 → 调用工具 → 观察结果 → 继续推理」的循环。

**14 个内置 Tool：**

| 类别 | Tool | 说明 |
|------|------|------|
| 查询 | `list_services` | 列出已注册服务 |
| 查询 | `get_service_status` | 获取服务运行状态 |
| 查询 | `get_deployment_info` | 查看部署方式与重启命令 |
| 查询 | `get_host_metrics` | 主机 CPU/内存/磁盘 |
| 日志/文件 | `read_log` | 读取服务日志 |
| 日志/文件 | `read_remote_file` | 读取远程文件 |
| 日志/文件 | `list_config_files` | 列出服务配置文件 |
| 执行 | `run_remote_command` | 执行只读 shell 命令 |
| 写操作 | `write_remote_file` | 创建/修改远程文件（需确认） |
| 写操作 | `delete_remote_file` | 删除远程文件（需确认） |
| 巡检 | `discovery_scan` | 触发服务扫描 |
| 巡检 | `run_inspection` | 立即执行一轮巡检 |
| 告警 | `list_incidents` | 列出历史告警 |
| 告警 | `analyze_incident` | 分析指定告警 |

**其他 AI 能力：**

- 多轮对话上下文管理与自动压缩（长会话不丢关键信息）
- LangGraph Checkpointer 持久化对话状态（`thread_id` = 会话 ID）
- 长期记忆与知识库条目
- 流式输出（桌面端实时显示思考与 Tool 调用过程）
- 支持 Ollama 与 OpenAI 兼容 API，可按场景路由不同模型

### 运维操作

- **服务重启**：systemd / Docker / Compose，经用户确认后执行
- **远程文件操作**：创建、修改、删除，逐次弹出确认框
- **命令策略**：只读命令白名单 + 写操作人工门禁
- **冷却限制**：防止频繁重启（默认 15 分钟内最多 3 次）

### 通知与集成

- **飞书告警推送**：Incident 产生时通知指定群
- **飞书 @机器人**：群内只读指令查询（状态、日志等）
- **REST API**：与 Web/桌面共享同一套后端能力

---

## 架构

### 总体架构

```
┌─────────────────────────────────────────────────────────┐
│                    Windows 跳板机                        │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  PySide6     │  │  FastAPI     │  │  飞书 Bot     │  │
│  │  桌面 UI     │  │  Web 控制台   │  │  告警/指令    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬────────┘  │
│         └─────────────────┼─────────────────┘           │
│                           ▼                             │
│              ┌────────────────────────┐                 │
│              │     AgentService       │                 │
│              │     （业务门面层）       │                 │
│              └────────────┬───────────┘                 │
│         ┌─────────────────┼─────────────────┐           │
│         ▼                 ▼                 ▼           │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐    │
│  │ LangGraph   │  │ MonitorLoop │  │ Discovery    │    │
│  │ ReAct Agent │  │ 后台巡检循环  │  │ 服务发现      │    │
│  └──────┬──────┘  └──────┬──────┘  └──────┬───────┘    │
│         │                │                │             │
│         └────────────────┼────────────────┘             │
│                          ▼                              │
│              ┌────────────────────────┐                 │
│              │   Executor Registry    │                 │
│              │   SSH 远程执行引擎       │                 │
│              └────────────┬───────────┘                 │
│                           │                             │
│              ┌────────────┴───────────┐                 │
│              │  SQLite（agent.db）     │                 │
│              │  告警 / 对话 / 知识库    │                 │
│              └────────────────────────┘                 │
└───────────────────────────┬─────────────────────────────┘
                            │ SSH
                            ▼
              ┌─────────────────────────┐
              │      Linux 目标机         │
              │  Java / Docker / Compose │
              │  systemd 中间件           │
              └─────────────────────────┘
```

### AI Agent 调用链路

```
用户输入
  ↓
chat_ops（上下文预算评估 + 压缩）
  ↓
ChatAgent（LangGraph create_react_agent）
  ├─ SystemMessage：系统 Prompt + 长期记忆 + 主机/服务清单
  ├─ HumanMessage：用户问题
  └─ ReAct 循环：LLM → tool_calls → ToolNode（SSH 执行）→ ToolMessage → LLM …
  ↓
Assistant 回复 → 持久化 → 记忆提取
```

写操作（重启、文件变更）在 Graph 外设 **人工确认门禁**，Agent 提议后由 UI 弹出确认框。

### 目录结构

```
agent/
├── desktop/          # PySide6 桌面界面（默认入口）
│   ├── pages/        # 概览、告警、设置、对话页面
│   └── widgets/      # 侧边栏、聊天面板等组件
├── web/              # FastAPI Web 控制台与静态资源
├── services/         # 业务门面（AgentService、chat_ops）
├── runtime/          # 后台 asyncio 运行时（巡检循环）
├── langchain/        # LangGraph Agent、Tool 定义、上下文管理
├── discovery/        # 服务发现（Java / Docker / Compose / 中间件）
├── monitor/          # 后台巡检与告警规则
├── executor/         # SSH 远程执行、探针、命令/写操作策略
├── remediation/      # 重启与文件写操作编排
├── feishu/           # 飞书告警推送与机器人指令
├── store/            # SQLite 存储（告警、对话、知识库）
├── config_mgr/       # 配置读写与初始化向导逻辑
├── incident/         # 告警规则与 Incident 模型
├── settings.py       # 配置加载（config.yaml）
├── launcher.py       # 桌面应用入口
└── main.py           # Web 服务入口
```

### 技术栈

| 层次 | 技术 |
|------|------|
| 桌面 UI | PySide6 |
| Web 服务 | FastAPI + Uvicorn |
| AI 框架 | LangChain + LangGraph（ReAct Agent） |
| 大模型 | Ollama / OpenAI 兼容 API |
| 远程执行 | asyncssh |
| 存储 | SQLite（aiosqlite）+ YAML 配置 |
| 打包 | PyInstaller |

### 相关文档

- [飞书机器人接入](docs/feishu-bot-setup.md)
- [上下文管理机制](docs/context-management.md)

---

## 常见问题

**PowerShell 无法 `activate` 虚拟环境？**

不要激活 venv，直接用虚拟环境 Python：

```powershell
.\.venv\Scripts\python.exe -m agent.launcher
```

**配置与密钥会提交到 Git 吗？**

不会。`data/config.yaml` 和 `data/agent.db` 已在 `.gitignore` 中。克隆后参考 `config.yaml.example` 或在向导中配置。

**生产环境建议**

- 为 `web.auth_token` 设置访问令牌
- 限制 `8765` 端口仅内网访问
- SSH 优先使用密钥登录

## License

本项目采用 [MIT License](LICENSE) 开源。
