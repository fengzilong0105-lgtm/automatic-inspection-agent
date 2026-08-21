# 飞书机器人只读指令接入指南

SteadyOps 支持通过飞书 **长连接** 接收群消息，在群内 @机器人 发送运维指令（只读：查状态、读日志、巡检等）。

## 配置总览（按此顺序做）

```text
① 准备环境
② 开放平台：建应用 → 拿凭证 → 开机器人
③ 开放平台：申请权限 → 创建版本并发布
④ 开放平台：把机器人拉进群 → 记下 Chat ID
⑤ SteadyOps：填写 App ID / Secret / Chat ID，开启「@机器人指令」
⑥ 启动 SteadyOps（建立长连接）
⑦ 开放平台：保存「长连接」+ 订阅 im.message.receive_v1
⑧ 群内 @机器人 验证对话
```

> **顺序要点**：权限必须先发布；长连接订阅必须在 SteadyOps 已启动后再保存，否则开放平台可能保存失败。

---

## ① 准备环境

- SteadyOps 已配置好 LLM 与 SSH（与桌面/Web 对话相同）
- 运行机可访问公网（主动连 `open.feishu.cn`，**不需要**公网 IP / 回调地址）
- 已安装依赖：`pip install -e .`（含 `lark-oapi`）

---

## ② 开放平台：创建应用并启用机器人

1. 登录 [飞书开放平台](https://open.feishu.cn/app)
2. 创建或打开 **企业自建应用**
3. **凭证与基础信息** → 复制并保存：
   - **App ID**（形如 `cli_xxx`）
   - **App Secret**
4. **应用能力 → 机器人 → 启用**

此时先不要急着配事件订阅（放到 ⑦）。

---

## ③ 开放平台：申请权限并发布

路径：**开发配置 → 权限管理 → API 权限**（**应用身份**）。用搜索框搜权限码开通即可。

**必须开这 2 项：**

| 权限码 | 用途 |
|--------|------|
| `im:message.group_at_msg:readonly` | 接收群内 `@机器人` 消息 |
| `im:message` 或 `im:message:send_as_bot` | 往群里回复消息 |

开通后立刻：**创建版本 → 保存并申请发布**，确认状态为 **已发布**（只勾选不发布不生效）。

---

## ④ 开放平台：机器人进群并拿到 Chat ID

1. 在目标运维群中 **添加机器人 / 添加应用**（选刚建的应用）
2. 获取该群 **Chat ID**（`oc_` 开头）

获取方式任选：

- 与告警共用同一群时，可直接复用已有 `alert_chat_id`
- 飞书开放平台 / API 调试工具查询群列表
- 群设置或企业内部常用的 Chat ID 查询方式

后面 SteadyOps 配置里要用到这个 ID。

---

## ⑤ SteadyOps：填写飞书对话配置

在 **设置**（桌面端右上角「设置」，或 Web 控制台「设置」）填写：

| 顺序 | 设置项 | 怎么填 |
|------|--------|--------|
| 1 | App ID / App Secret | 填 ② 里复制的凭证 |
| 2 | 告警 Chat ID | 填 ④ 的 `oc_xxx`（若只开指令、不开告警，也可只填「指令群 Chat ID」） |
| 3 | **启用飞书 @机器人 指令** | **必须勾选**（与「启用飞书告警」独立，可只开指令） |
| 4 | 指令群 Chat ID | 填指令所在群；**留空则与告警群相同** |
| 5 | 仅 @机器人 时响应 | 建议保持开启 |

点 **保存**。SteadyOps 会尝试按新配置重连飞书长连接。

等价配置（`data/config.yaml`）：

```yaml
feishu:
  enabled: true                   # 告警开关；只开指令时也可 false
  app_id: "cli_xxxxxxxx"
  app_secret: "xxxxxxxxxxxxxxxx"
  alert_chat_id: "oc_xxxxxxxx"    # 告警群；指令群留空时也会用它
  bot:
    command_enabled: true         # 必须 true
    command_chat_id: ""           # 留空 = 用 alert_chat_id
    require_at_mention: true      # 建议 true
```

---

## ⑥ 启动 SteadyOps（先连上长连接）

```powershell
cd e:\project\automatic-inspection-agent
.\.venv\Scripts\python.exe -m agent.launcher
```

日志中应出现：

```text
Feishu bot long connection starting (app_id=cli_xxx)
```

若看不到这行：检查 `command_enabled`、App ID/Secret、Chat ID 是否齐全。

---

## ⑦ 开放平台：保存事件订阅（必须在 ⑥ 之后）

确认 SteadyOps 已在跑、长连接已启动后，回到飞书开放平台：

**开发配置 → 事件与回调 → 事件配置**

1. 订阅方式选择：**使用长连接接收事件** → **保存**
2. **添加事件** → **接收消息** `im.message.receive_v1` → 保存

> 若此时保存失败：多半是 SteadyOps 未启动或长连接未起来，回到 ⑥ 再试。

---

## ⑧ 验证群内对话

在已配置的群里发送：

```text
@SteadyOps机器人 road_control 状态怎么样？
```

预期：

1. 机器人先回「收到，正在处理…」
2. 再回 Agent 查询结果

也可发 `帮助` 或 `@机器人 帮助` 查看指令说明。

---

## 只读限制

飞书机器人 **不支持**：

- 重启服务
- 写文件 / 删文件
- 其他需用户确认的写操作

以上请用桌面端或 Web 控制台。

## 对话上下文

每个「群 + 用户」独立会话，ID 形如 `feishu:{chat_id}:{user_id}`，可在桌面端对话列表中查看（若同步展示）。

## 故障排查

| 现象 | 处理 |
|------|------|
| 日志 `lark-oapi not installed` | `pip install lark-oapi` 或 `pip install -e .` |
| 长连接订阅保存失败 | **先**启动 SteadyOps（⑥），**再**保存订阅（⑦） |
| @机器人 无反应 | 检查：权限已发布；机器人在群内；`command_enabled`；Chat ID；已订阅 `im.message.receive_v1`；是否开通 `im:message.group_at_msg:readonly` |
| 发消息权限错误 | 确认已开通 `im:message` 或 `im:message:send_as_bot`，且版本已发布 |
| 只收到告警、指令不工作 | 确认 `bot.command_enabled: true` |
| 别的群 @ 了没反应 | `command_chat_id` / `alert_chat_id` 只认配置的那一个群 |

## 相关代码

- `agent/feishu/runner.py` — 长连接
- `agent/feishu/bot_service.py` — 指令路由与只读校验
- `agent/feishu/message_parser.py` — 消息解析
