# SteadyOps

Windows 跳板机部署的 **SteadyOps** 运维桌面应用。通过 SSH 管理 Linux 上的 Java / Docker / Compose / 中间件服务。

## Windows 部署环境要求

本项目**运行在 Windows 跳板机上**，通过 SSH 连接 Linux 目标机，**不需要在 Windows 上安装 Docker、MySQL、Redis 等中间件**。

### 必需（Windows 本机）

| 项目 | 要求 | 说明 |
|------|------|------|
| 操作系统 | Windows 10 / 11 或 Windows Server | 作为运维跳板机使用 |
| Python | **3.11+**（项目自带 **3.12.10** 安装包） | 见下方 `installers/`，网速慢可直接本地安装，无需再下载 |
| 网络 | 能 SSH 到 Linux 目标机 | 默认端口 22，防火墙需放行 |
| 磁盘 | 约 500MB+（含虚拟环境与依赖） | 配置、SQLite 告警库在 `data/` 目录 |

### 大模型（二选一，对话/诊断必需）

| 方案 | 部署位置 | 说明 |
|------|----------|------|
| **Ollama（推荐内网）** | 可与 Agent 同机或局域网其他机器 | 默认地址 `http://localhost:11434`；需提前 `ollama pull` 拉取模型（如 `minimax-m3:cloud`、 `qwen2.5` 等） |
| **OpenAI 兼容 API** | 公网或内网 API 网关 | 在 Web 向导中填写 `base_url`、`api_key`、`model` |

> 对话、故障分析依赖 LLM；仅做扫描/巡检可不配模型，但控制台对话功能不可用。

### 可选组件

| 组件 | 用途 |
|------|------|
| 飞书应用 | 告警推送 + **群内 @机器人 只读指令**（见 [飞书机器人接入](docs/feishu-bot-setup.md)） |
| SSH 私钥文件 | 推荐密钥登录；也支持密码登录 |
| Bearer Token | 配置 `web.auth_token` 后 API 需带 `Authorization: Bearer <token>` |

### 本机不需要

- Docker Desktop、WSL（非必须）
- MySQL / PostgreSQL / Redis（告警与状态使用本地 **SQLite**：`data/agent.db`）
- Nginx / IIS（内置 **Uvicorn** 直接提供 Web，默认 `http://0.0.0.0:8765`）

### Linux 目标机要求（被管服务器）

Agent 本身装在 Windows，但 Linux 侧需满足以下能力，巡检/扫描才完整：

| 能力 | 用途 | 缺失时影响 |
|------|------|------------|
| **SSH** | 所有远程操作 | 无法使用 |
| `ps` / `grep` | Java 进程发现 | Java 服务检测受限 |
| `jps`（JDK 自带） | Java 主类识别 | 部分 Java 服务可能漏扫 |
| `systemctl` | systemd 服务状态 | 中间件 systemd 探针失效 |
| `ss` 或 `netstat` | 监听端口检测 | 端口匹配不准确 |
| `docker` / `docker compose` | 容器类服务 | Docker/Compose 服务无法发现 |
| `curl` | HTTP 健康检查 | `health_url` 不可用 |
| `python3` + `psutil`（可选） | 主机 CPU/内存/磁盘指标 | 指标查询降级，不影响核心巡检 |

**权限说明：**

- 普通用户：可执行 `ps`、`jps`、读有权限的日志与配置
- 若部署目录属 root（如 `/DATA01/...`）：在主机配置中勾选 **sudo su** 并提供 `sudo_password`，否则读目录、`ls`、读配置可能失败

**网络：**

- Windows 跳板机 → Linux：`22/tcp`（或自定义 SSH 端口）
- Windows 跳板机 → Ollama：`11434/tcp`（若本机或内网部署）
- Windows 跳板机 → 公网 LLM API：按实际 API 地址放行 HTTPS

### 依赖安装（Python 包）

由 `pip install -e .` 自动安装，主要包括：

- **Web**：FastAPI、Uvicorn
- **SSH**：asyncssh
- **AI**：LangChain、LangGraph、langchain-openai、langchain-ollama
- **存储**：aiosqlite、PyYAML

## 能力（MVP）

- 混合栈自动发现（Java、Docker、Compose、中间件）
- 持续巡检 + 服务状态检测 + 报错告警
- LangChain 对话：查状态、查日志、扫描、分析建议
- 对话确认后重启服务（systemd / docker / compose）
- Web 控制台 + 飞书告警占位

## 新电脑从零部署（完整步骤）

适用于一台**全新的 Windows 电脑**，按顺序操作即可。

### 第 1 步：安装 Python 3.11+

项目已自带 Windows 64 位安装包（适合内网或网速慢的环境）：

```
installers/python-3.12.10-amd64.exe
```

**方式 A：本地安装（推荐，无需联网下载 Python）**

1. 进入项目目录，双击运行 `installers\python-3.12.10-amd64.exe`  
   或在 PowerShell 中执行：

```powershell
cd e:\project\automatic-inspection-agent
.\installers\python-3.12.10-amd64.exe
```

2. 安装界面**务必勾选底部** **Add python.exe to PATH**，再点 **Install Now**。

3. **静默安装（可选，适合批量部署）**：

```powershell
.\installers\python-3.12.10-amd64.exe /passive PrependPath=1 Include_test=0
```

> `/passive` 会显示进度条、自动完成；`PrependPath=1` 等价于勾选「Add to PATH」。

**方式 B：在线下载（未带安装包时）**

1. 打开 [https://www.python.org/downloads/windows/](https://www.python.org/downloads/windows/)，下载 **Python 3.11+** 的 Windows installer（64-bit）。
2. 同样勾选 **Add python.exe to PATH** 后安装。

**验证安装**

4. **重新打开** PowerShell 或 cmd（安装后需新开窗口才能识别 PATH），执行：

```powershell
python --version
# 应显示 Python 3.12.10（或你安装的版本）

pip --version
# 应能正常显示 pip 版本
```

若提示「找不到 python」：

- 重新安装并勾选 **Add to PATH**，或
- 手动将 `C:\Users\<用户名>\AppData\Local\Programs\Python\Python312\` 及 `...\Scripts\` 加入系统环境变量 PATH。

### 第 2 步：获取项目代码

**方式 A：Git 克隆（推荐）**

```powershell
cd e:\project
git clone <你的仓库地址> automatic-inspection-agent
cd automatic-inspection-agent
```

**方式 B：拷贝文件夹**

将整份 `automatic-inspection-agent` 目录复制到新电脑（如 `e:\project\automatic-inspection-agent`），**建议连同 `installers\python-3.12.10-amd64.exe` 一起拷贝**，新机器无需再下载 Python。

```powershell
cd e:\project\automatic-inspection-agent
```

> 若从旧机器迁移，可一并复制 `data\config.yaml` 和 `data\agent.db`，免重新配置；注意其中含 SSH 密码等敏感信息，勿提交到 Git。

### 第 3 步：创建虚拟环境并安装依赖

```powershell
python -m venv .venv

# 方式 A（推荐）：不激活 venv，直接用其 Python（不受 PowerShell 执行策略影响）
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .

# 方式 B：先激活 venv 再安装（若 PowerShell 报「禁止运行脚本」，见下方「常见问题」）
# .\.venv\Scripts\activate
# pip install -U pip
# pip install -e .
```

安装约需几分钟，完成后无报错即可。

### 第 4 步：（可选）安装 Ollama 大模型

若使用本机 Ollama 做对话（不用公网 API）：

1. 下载安装 [Ollama for Windows](https://ollama.com/download)
2. 安装后执行：

```powershell
ollama pull minimax-m3:cloud
# 或你实际使用的模型名
```

3. 确认服务在运行：`http://localhost:11434`

### 第 5 步：启动 Agent

```powershell
cd e:\project\automatic-inspection-agent

# 推荐：不激活 venv，直接运行
.\.venv\Scripts\python.exe -m agent.main
```

看到类似 `Uvicorn running on http://0.0.0.0:8765` 即启动成功。

浏览器访问：**http://localhost:8765**

### 第 6 步：Web 初始化向导

首次打开会进入向导，依次完成：

| 步骤 | 内容 |
|------|------|
| 1 | Linux SSH：IP、端口、用户名、密码或私钥；需读 root 目录时勾选 **sudo su** |
| 2 | 大模型：Ollama（`http://localhost:11434` + 模型名）或 OpenAI 兼容 API |
| 3 | （可选）飞书告警 |
| 4 | 扫描并注册服务 |

配置保存在 `data\config.yaml`。

### 第 7 步：开机自启（可选）

可用 **任务计划程序** 或写一个 `start-agent.bat`：

```bat
@echo off
cd /d e:\project\automatic-inspection-agent
call .venv\Scripts\activate.bat
python -m agent.main
```

### 部署检查清单

- [ ] 已用 `installers\python-3.12.10-amd64.exe` 或在线方式安装 Python，且勾选了 **Add to PATH**
- [ ] `python --version` ≥ 3.11
- [ ] `pip install -e .` 成功
- [ ] 本机能 `ping` / SSH 到 Linux 目标机（可用 PuTTY 先测）
- [ ] 浏览器能打开 `http://localhost:8765`
- [ ] Web 向导中 SSH 测试通过
- [ ] （若用对话）LLM 测试通过
- [ ] 「扫描服务」能发现目标机上的 Java/中间件

## 快速开始（Windows）

```powershell
cd e:\project\automatic-inspection-agent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .

# 启动 Web 控制台
.\.venv\Scripts\python.exe -m agent.main

# 或启动桌面版（开发调试）
.\.venv\Scripts\python.exe -m agent.launcher
```

浏览器打开 **http://localhost:8765**（Web 模式），首次访问会自动进入 **Web 初始化向导**：

1. 填写 Linux SSH 信息（IP、用户、私钥路径或密码）并测试连接  
   - 需要读 root 目录时勾选 **sudo su**
2. 配置大模型（OpenAI 兼容 API 或 Ollama）并测试
3. （可选）配置飞书告警
4. 扫描并注册服务 → 进入控制台

所有配置保存到 `data/config.yaml`，日常可在控制台右上角 **「设置」** 中修改，无需手改文件。

### 使用 Ollama 的简要步骤

```powershell
# 1. 安装 Ollama for Windows: https://ollama.com/download
# 2. 拉取模型（示例）
ollama pull minimax-m3:cloud

# 3. 在 Web 向导中选择 provider=ollama，base_url=http://localhost:11434，填写模型名
```

### 指定配置文件或端口

```powershell
python -m agent.main --config data\config.yaml --host 0.0.0.0 --port 8765
```

## 打包为可分享的 .exe（桌面应用）

可将 Agent 打成 **单个 Windows 桌面程序**，发给他人双击运行，**无需安装 Python**。  
所有操作在 **软件窗口内** 完成（初始化向导、巡检、告警、AI 对话、设置），**不打开浏览器**。

大模型仍支持 **Ollama 与 OpenAI 兼容 API 并存**；若用本机 Ollama，用户需自行安装 Ollama。

### 开发者：在本机构建

```powershell
cd e:\project\automatic-inspection-agent
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[build]"

# 一键构建（约 5–15 分钟）
.\scripts\build.ps1

# 调试版（保留控制台窗口）
.\scripts\build.ps1 -Console
```

产物：`dist\SteadyOps.exe`

### 开发者：本地运行桌面版

```powershell
# 推荐：不激活 venv
.\.venv\Scripts\python.exe -m agent.launcher
```

旧版 Web 控制台（仅开发调试）：`.\.venv\Scripts\python.exe -m agent.launcher --web`

### 使用者：安装与配置

1. 双击 `SteadyOps.exe`
2. 首次运行进入 **初始化向导**：SSH → 大模型 → 飞书（可选）→ 扫描服务
3. 完成后进入主界面：概览 / 告警 / AI 对话 / 设置

再次双击时，若已在运行，会提示「应用已在运行中」。

### 打包版数据存放位置

| 内容 | 路径 |
|------|------|
| 配置文件 | `%APPDATA%\SteadyOps\data\config.yaml` |
| 告警数据库 | `%APPDATA%\SteadyOps\data\agent.db` |
| 运行日志 | `%APPDATA%\SteadyOps\logs\agent.log` |

> 开发模式（`python -m agent.launcher`）在项目目录运行时也使用 `data/`。

### 打包版说明

- **不需要 Python、Qt 等环境**（已打进 exe）
- **Ollama 需用户自行安装**（若选本地大模型）
- 体积约 **80–200 MB**（含 PySide6 + LangChain）
- 极少数精简系统若无法启动，可安装 [VC++ 2015–2022 x64 运行库](https://learn.microsoft.com/zh-cn/cpp/windows/latest-supported-vc-redist)


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

- `agent/desktop/` — PySide6 桌面界面（默认入口）
- `agent/services/` — 业务门面（SSH、巡检、对话等）
- `agent/runtime/` — 后台 asyncio 运行时（巡检循环）
- `agent/executor/ssh.py` — SSH 远程执行
- `agent/discovery/` — 服务发现
- `agent/monitor/` — 后台巡检
- `agent/langchain/` — LangChain 对话与诊断
- `agent/web/` — 旧版 Web 控制台（`--web` 调试用）

## 发布到 GitHub（纯净版说明）

仓库**不会也不应**包含你的现场配置与密钥：

| 路径 | 是否提交 Git | 说明 |
|------|----------------|------|
| `data/config.yaml` | **否**（`.gitignore`） | 主机 IP、SSH 密码、服务列表等，仅在本机 `data/` 生成 |
| `data/agent.db` | **否** | 本地告警 SQLite |
| `config.yaml.example` | **是** | 示例模板，无真实密码 |
| `installers/python-3.12.10-amd64.exe` | **是** | 离线 Python 安装包 |

克隆仓库后首次启动会进入 **Web 初始化向导**，在页面里配置 SSH 与 LLM 即可；也可参考示例：

```powershell
copy config.yaml.example data\config.yaml
# 编辑 data\config.yaml 填入真实信息（勿提交到 Git）
```

**注意：** 若你曾在旧版本中将 `data/config.yaml` 或含真实密码的文件提交过 Git，请在 Linux 服务器上**更换 SSH 密码**，并考虑用 `git filter-repo` 清理历史后再推送公开仓库。

## 注意

- Linux 侧**无需安装本 Agent**，仅需 SSH 可达及上表所列命令
- 重启有 15 分钟冷却限制（可配置）
- 飞书接入需配置应用凭证并将机器人拉入告警群
- 对话上下文默认保存在进程内存，**重启 Agent 后丢失**；可点控制台「清空对话」重置
- 生产环境建议为 `web.auth_token` 设置访问令牌，并限制 `8765` 端口仅内网访问

## 常见问题

### PowerShell 提示「禁止运行脚本」，无法 `activate` 虚拟环境

Windows 默认 PowerShell **执行策略**可能阻止运行 `.venv\Scripts\Activate.ps1`，例如：

```text
无法加载文件 ...\Activate.ps1，因为在此系统上禁止运行脚本
```

**推荐做法（无需改系统策略）**：不要 `activate`，直接用虚拟环境里的 Python：

```powershell
cd e:\project\automatic-inspection-agent

# 安装依赖（首次）
.\.venv\Scripts\python.exe -m pip install -e .

# 调试桌面版
.\.venv\Scripts\python.exe -m agent.launcher

# 调试 Web 版
.\.venv\Scripts\python.exe -m agent.main
```

**若仍想使用 `activate`**，任选其一：

```powershell
# 仅当前 PowerShell 窗口临时放行（关闭窗口后失效，较安全）
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\activate
python -m agent.launcher
```

```powershell
# 改用 cmd（不受 PowerShell 执行策略影响）
cmd
cd /d e:\project\automatic-inspection-agent
.venv\Scripts\activate.bat
python -m agent.launcher
```

```powershell
# 当前用户永久放宽（需管理员权限时选「是」；仅在你信任的开发机上使用）
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

> 说明：`activate` 只是把 `python` / `pip` 指到 `.venv`；用 `.\.venv\Scripts\python.exe` 效果相同，且更省事。
