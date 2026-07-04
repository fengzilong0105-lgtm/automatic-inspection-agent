# 飞书机器人只读指令接入指南

SteadyOps 支持通过飞书 **长连接** 接收群消息，在群内 @机器人 发送运维指令（只读：查状态、读日志、巡检等）。

## 前置条件

- SteadyOps 已配置 LLM 与 SSH（与桌面/Web 对话相同）
- 跳板机可访问公网（主动连接 `open.feishu.cn`，无需公网 IP）
- 已安装依赖：`pip install -e .`（含 `lark-oapi`）

## 第一步：飞书开放平台配置

登录 [飞书开放平台](https://open.feishu.cn/app) → 创建或打开**企业自建应用**。

### 1. 凭证

在 **凭证与基础信息** 复制：

- App ID
- App Secret

### 2. 启用机器人

**应用能力 → 机器人 → 启用**

### 3. 权限

**权限管理** 中申请（名称以控制台为准）：

| 权限 | 用途 |
|------|------|
| 获取与发送单聊、群组消息 (`im:message`) | 收发消息 |
| 接收群聊中 @ 机器人 消息 | 指令触发 |

申请后 **创建版本并发布**（企业内发布即可）。

### 4. 事件订阅（长连接）

**开发配置 → 事件与回调 → 事件配置**：

1. 订阅方式选择：**使用长连接接收事件**
2. 添加事件：**接收消息 `im.message.receive_v1`**

> **重要**：保存长连接订阅前，必须先启动 SteadyOps（见下文），否则可能保存失败。

### 5. 机器人进群

将机器人拉入运维群，并获取群 **Chat ID**（`oc_` 开头）。

- 若与告警同一群，可复用 `alert_chat_id`
- 或在开放平台/API 工具中查询

## 第二步：SteadyOps 配置

在 **设置** 页面（桌面端右上角「设置」，或 Web 控制台「设置」）填写飞书相关项：

| 设置项 | 说明 |
|--------|------|
| 启用飞书告警 | 告警推送开关（与指令独立，可只开指令） |
| App ID / App Secret | 开放平台凭证（告警与指令共用） |
| 告警 Chat ID | 告警推送目标群 |
| **启用飞书 @机器人 指令** | 开启群内只读指令（**无需勾选告警**） |
| 指令群 Chat ID | 留空则与告警群相同 |
| 仅 @机器人 时响应 | 建议保持开启 |

保存后 SteadyOps 会自动尝试重连飞书长连接。

也可直接编辑 `data/config.yaml`（与界面等价）：

```yaml
feishu:
  enabled: true
  app_id: "cli_xxxxxxxx"
  app_secret: "xxxxxxxxxxxxxxxx"
  alert_chat_id: "oc_xxxxxxxx"
  bot:
    command_enabled: true
    command_chat_id: ""
    require_at_mention: true
```

## 第三步：启动并保存飞书订阅

```powershell
cd e:\project\automatic-inspection-agent
.\.venv\Scripts\python.exe -m agent.launcher
```

日志中应出现：

```text
Feishu bot long connection starting (app_id=cli_xxx)
```

此时回到飞书开放平台，**保存**「使用长连接接收事件」和 `im.message.receive_v1` 订阅。

## 第四步：验证

在配置的群内发送：

```text
@SteadyOps机器人 road_control 状态怎么样？
```

机器人应先回复「收到，正在处理…」，再回复 Agent 查询结果。

发送 `帮助` 或 `@机器人 帮助` 可查看指令说明。

## 只读限制

飞书机器人 **不支持**：

- 重启服务
- 写文件 / 删文件
- 其他需用户确认的写操作

以上请使用桌面端或 Web 控制台。

## 对话上下文

每个「群 + 用户」独立会话，ID 形如 `feishu:{chat_id}:{user_id}`，可在桌面端对话列表中查看（若同步展示）。

## 故障排查

| 现象 | 处理 |
|------|------|
| 日志 `lark-oapi not installed` | 执行 `pip install lark-oapi` 或 `pip install -e .` |
| 长连接订阅保存失败 | 先启动 SteadyOps，再保存订阅 |
| @机器人 无反应 | 检查 `command_enabled`、`command_chat_id`、机器人是否在群内、应用是否已发布 |
| 发消息权限错误 | 确认 `im:message` 权限已开通并发布版本 |
| 只收到告警、指令不工作 | 确认 `bot.command_enabled: true` |

## 相关代码

- `agent/feishu/runner.py` — 长连接
- `agent/feishu/bot_service.py` — 指令路由与只读校验
- `agent/feishu/message_parser.py` — 消息解析
