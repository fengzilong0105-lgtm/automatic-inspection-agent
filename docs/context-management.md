# SteadyOps 上下文管理实施方案

> 版本：v1.0  
> 日期：2026-07-04  
> 状态：设计稿，待实施

## 1. 背景与目标

### 1.1 现状问题

当前 SteadyOps 的 AI 对话存在以下限制：

| 问题 | 原因 |
|------|------|
| 重启后对话丢失 | `ChatAgent` 使用 `MemorySaver()`，状态仅存进程内存 |
| 无法新建独立对话 | 桌面端固定 `SESSION_ID = "desktop-default"`，Web 端固定 `"web-default"` |
| 无上下文用量展示 | 未做 token 估算与统计 |
| 超长对话可能报错 | 历史全量累积，无压缩策略 |
| 跨对话知识不共享 | 无长期记忆机制；服务路径、用户偏好随会话消失 |
| 改配置会清空对话 | `chat_graph.py` 在配置指纹变化时重置 `MemorySaver` |

### 1.2 建设目标

实现完整的上下文管理体系，覆盖：

1. **多对话隔离** — 可开启新一轮对话，各对话上下文独立
2. **持久化** — 重启软件后可查看历史对话并继续聊
3. **用量统计** — 每个对话显示 `used / limit (percent%)`
4. **工具压缩** — 防止单轮工具返回撑满上下文
5. **长期记忆** — 服务路径、用户偏好等跨对话共享
6. **滚动摘要 + 分级策略** — 单对话聊很长时自动压缩，少报错

### 1.3 实施路线

```
P0  持久化 + 用量统计
 ↓
P1  工具压缩 + 长期记忆
 ↓
P2  滚动摘要 + 分级策略
```

---

## 2. 核心概念

### 2.1 术语定义

| 概念 | 说明 |
|------|------|
| **Conversation（对话）** | 用户可见的一个聊天会话，有标题、创建时间 |
| **thread_id** | LangGraph checkpointer 使用的线程 ID，与 Conversation 1:1 |
| **Turn（轮）** | 用户发一条消息 → AI 完整回复（含工具调用） |
| **会话累计用量** | 单个对话内从第 1 轮到当前的 token 总和 |
| **单轮用量** | 仅当前这一轮（含本轮工具结果）的 token |
| **长期记忆** | 跨对话共享的事实/偏好，单独存储，不占对话历史 |

### 2.2 隔离 vs 累计

```
隔离的是「对话」与「对话」之间
累计的是「同一对话」里「轮」与「轮」之间
```

- **对话 A** 和 **对话 B** 的历史互不可见，各自独立累计用量
- **长期记忆** 可跨对话共享（P1 实现后）
- **累计满** 指某一个对话内部累计接近窗口上限，不是所有对话加总

### 2.3 上下文组成（每轮发给模型）

```
┌──────────────────────────────────────────────────┐
│ ① 系统提示（角色、规则、主机/服务列表）              │
├──────────────────────────────────────────────────┤
│ ② 长期记忆注入（P1）                              │
├──────────────────────────────────────────────────┤
│ ③ 本对话早期摘要（P2）                            │
├──────────────────────────────────────────────────┤
│ ④ 最近 N 轮完整对话（含工具调用与返回）              │
├──────────────────────────────────────────────────┤
│ ⑤ 本轮新消息                                      │
└──────────────────────────────────────────────────┘
                    ↓
            模型上下文窗口（如 512K）
```

---

## 3. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         UI 层                                    │
│  对话列表 │ 新建对话 │ 消息展示 │ 用量条 xK/512K │ 压缩提示      │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    ChatService（新模块）                          │
│  创建/切换对话 │ 发消息 │ 用量估算 │ 触发压缩策略 │ 提取长期记忆    │
└─────┬──────────────┬──────────────┬──────────────┬────────────┘
      │              │              │              │
      ▼              ▼              ▼              ▼
┌──────────┐  ┌────────────┐  ┌──────────┐  ┌──────────────┐
│ChatStore │  │Checkpointer│  │TokenMeter│  │KnowledgeStore│
│消息/UI历史│  │LangGraph状态│  │用量统计  │  │长期记忆      │
└──────────┘  └────────────┘  └──────────┘  └──────────────┘
      │              │              │              │
      └──────────────┴──────────────┴──────────────┘
                             │
                    data/chat.db（SQLite）
                    data/chat_checkpoints.db（LangGraph）
```

### 3.1 与现有模块关系

| 现有模块 | 改造方式 |
|----------|----------|
| `agent/langchain/chat_graph.py` | 核心改造：checkpointer、预算评估、压缩触发 |
| `agent/langchain/context_builder.py` | 注入长期记忆 |
| `agent/langchain/tools.py` | 接入工具结果压缩 |
| `agent/services/agent_service.py` | 新增对话管理 API 门面 |
| `agent/desktop/widgets/chat_panel.py` | 多对话 UI + 用量条 |
| `agent/web/routes.py` + `app.js` | Web 端同步 |
| `agent/store/incidents.py` | 不动；告警仍用 `agent.db` |

---

## 4. 数据模型

新建 `data/chat.db`（与 `agent.db` 分离）。

### 4.1 表：`conversations`

```sql
CREATE TABLE conversations (
    id            TEXT PRIMARY KEY,      -- UUID，也是 thread_id
    title         TEXT NOT NULL,         -- 默认取首条用户消息前 30 字
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    token_count   INTEGER DEFAULT 0,     -- 当前估算累计 token
    context_limit INTEGER DEFAULT 524288,-- 创建时快照模型窗口
    summary       TEXT,                  -- P2：滚动摘要
    status        TEXT DEFAULT 'active'  -- active / archived
);
```

### 4.2 表：`chat_messages`

```sql
CREATE TABLE chat_messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,         -- user / assistant / system / tool
    content         TEXT NOT NULL,         -- 展示内容（压缩后）
    raw_content     TEXT,                  -- 原始内容（可选）
    tool_name       TEXT,
    token_estimate  INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
```

### 4.3 表：`knowledge_entries`（P1）

```sql
CREATE TABLE knowledge_entries (
    id              TEXT PRIMARY KEY,
    category        TEXT NOT NULL,   -- preference / service_fact / ops_note
    key             TEXT NOT NULL,   -- 如 road_control.log_path
    value           TEXT NOT NULL,
    source_conv_id  TEXT,            -- 来源对话
    confidence      REAL DEFAULT 1.0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(category, key)
);
```

### 4.4 LangGraph Checkpointer

- 文件：`data/chat_checkpoints.db`
- 实现：`AsyncSqliteSaver`（依赖 `langgraph-checkpoint-sqlite`，已在 `pyproject.toml` 中声明）
- `thread_id` = `conversations.id`

---

## 5. P0：持久化 + 用量统计

**目标**：多对话隔离、重启可恢复、每个对话显示用量。本阶段不做压缩。

### 5.1 P0-1：接入 AsyncSqliteSaver

**干什么**：将 `MemorySaver` 替换为 SQLite 持久化 checkpointer。

**新建文件**：`agent/langchain/checkpointer.py`

**改动文件**：`agent/langchain/chat_graph.py`

**步骤**：

1. 创建单例 `AsyncSqliteSaver`，连接 `data/chat_checkpoints.db`
2. `ChatAgent.__init__` 使用 `AsyncSqliteSaver` 替代 `MemorySaver`
3. 移除「配置变更时 `self._memory = MemorySaver()` 清空全部历史」逻辑；改为只重建 graph，保留 checkpointer 连接
4. 应用启动时初始化，关闭时释放连接

**验收**：同一 `thread_id` 进程重启后，LangGraph 可恢复对话状态。

---

### 5.2 P0-2：新建 ChatStore

**干什么**：管理对话和消息的 CRUD，供 UI 展示。

**新建文件**：`agent/store/chat.py`

**核心方法**：

| 方法 | 作用 |
|------|------|
| `create_conversation(title?)` | 新建对话，返回 id |
| `list_conversations()` | 列表，按 `updated_at` 倒序 |
| `get_conversation(id)` | 获取元数据 |
| `delete_conversation(id)` | 删除对话、消息，并清理 checkpointer thread |
| `append_message(conv_id, role, content, ...)` | 写入一条消息 |
| `list_messages(conv_id)` | 加载历史供 UI 渲染 |
| `update_token_count(conv_id, count)` | 更新累计用量 |

**验收**：重启后 UI 可列出历史对话并展示消息。

---

### 5.3 P0-3：新建 TokenMeter 与 ContextLimits

**干什么**：估算 token 用量；获取模型上下文窗口上限。

**新建文件**：

- `agent/langchain/token_meter.py`
- `agent/langchain/context_limits.py`

**Token 估算策略**（初期轻量方案）：

```python
def estimate_tokens(text: str) -> int:
    # 中文为主：len(text) * 0.6
    # 英文为主：len(text) / 4
    # 后续可换 tiktoken
```

**上下文上限获取**：

| Provider | 方式 |
|----------|------|
| Ollama | 调 `POST /api/show`，读 `context_length`；结果缓存 |
| OpenAI 兼容 | 内置映射表（如 `gpt-4o-mini` → 128000） |

**统计时机**：

| 时机 | 统计内容 |
|------|----------|
| 用户发消息前 | system + 历史 + 本轮输入 |
| AI 回复后 | 回复 + 工具结果，累加到 `token_count` |
| 切换对话时 | 从 DB 读取已有 `token_count` |

**验收**：每个对话有 `token_count / context_limit` 数值。

---

### 5.4 P0-4：改造 ChatAgent 发消息流程

**干什么**：发消息时写 DB、更新用量。

**改动文件**：

- `agent/langchain/chat_graph.py`
- `agent/services/agent_service.py`

**每轮流程**：

```
1. 收到 (conversation_id, user_text)
2. ChatStore.append_message(conv_id, "user", user_text)
3. TokenMeter 估算当前累计用量
4. 调用 LangGraph（thread_id = conversation_id）
5. 流式/完整获取 AI 回复
6. ChatStore.append_message(conv_id, "assistant", reply)
7. 若有工具调用 → append_message(role="tool", tool_name=...)
8. 更新 conversation.token_count
9. 返回 { reply, usage: { used, limit, percent } }
```

---

### 5.5 P0-5：多对话 API + UI

**干什么**：支持新建/切换/删除对话；展示历史与用量。

**新增 API**：

| API | 方法 | 作用 |
|-----|------|------|
| `/api/chat/conversations` | GET | 对话列表 |
| `/api/chat/conversations` | POST | 新建对话 |
| `/api/chat/conversations/{id}/messages` | GET | 消息历史 |
| `/api/chat/conversations/{id}` | DELETE | 删除对话 |
| `/api/chat/conversations/{id}/usage` | GET | 用量详情 |

**桌面端改动**：`agent/desktop/widgets/chat_panel.py`

- 对话列表（左侧或顶部下拉）
- 「新建对话」按钮
- 用量显示：`42K / 512K (8%)`
- 启动时加载最近对话并渲染历史

**Web 端改动**：`agent/web/static/app.js`、`index.html`

### 5.6 P0 验收标准

- [ ] 可新建 3 个以上独立对话，互不影响
- [ ] 重启后对话列表、消息、继续聊均正常
- [ ] 每个对话显示 `used / limit (percent%)`
- [ ] 删除对话后，对应 checkpointer thread 一并清除

---

## 6. P1：工具压缩 + 长期记忆

**目标**：控制单轮上下文膨胀；跨对话记住服务路径和用户偏好。

### 6.1 P1-1：工具结果压缩器

**干什么**：工具返回写入历史前进行瘦身。

**新建文件**：`agent/langchain/tool_compress.py`

**按工具类型的压缩策略**：

| 工具 | 压缩策略 |
|------|----------|
| `read_log` | 保留 ERROR/WARN/Exception 行 + 最后 80 行；附总行数 |
| `read_remote_file` | 超 8KB 保留头 40 行 + 尾 40 行；标注已截断 |
| `run_remote_command` | stdout/stderr 各限 4000 字符 |
| `get_deployment_info` | JSON 超 4KB 时去掉冗余字段 |
| `analyze_incident` | 保留结论 + 建议；日志片段限 2000 字 |

**接入点**：`agent/langchain/tools.py`，每个工具 `return` 前调用 `compress_tool_output()`。

**存储策略**：

```
chat_messages.content     = 压缩后（给 UI 和模型）
chat_messages.raw_content   = 原始结果（可选，配置开关）
```

**配置项**（`config.yaml`）：

```yaml
chat:
  tool_compression:
    enabled: true
    keep_raw: false
    log_tail_lines: 80
    log_error_scan: true
```

**解决的问题**：单轮读取大日志/大文件导致上下文撑满。

---

### 6.2 P1-2：长期记忆 KnowledgeStore

**干什么**：跨对话共享稳定事实，不依赖完整对话历史。

**新建文件**：

- `agent/store/knowledge.py`
- `agent/langchain/memory_extractor.py`

**记忆分类**：

| category | 示例 |
|----------|------|
| `preference` | `answer_style = 简短，先给结论` |
| `service_fact` | `road_control.log_path = /DATA01/logs/road_control.log` |
| `service_fact` | `road_control.startup = systemd:road-control.service` |
| `ops_note` | `prod-01 读 /DATA01 需 sudo su` |

**写入时机**：

**A. 自动提取（每轮结束后）**

从本轮对话提取：

- 用户明确表达的偏好
- 新确认的服务路径 / 启动方式
- 与 `get_deployment_info` 实测一致的事实

**B. 用户确认（更稳）**

AI 回复标记 `【可记住】...`，UI 显示「记住这条」，用户确认后写入。

---

### 6.3 P1-3：注入长期记忆到 System Prompt

**干什么**：每轮将知识库摘要注入上下文。

**改动文件**：`agent/langchain/context_builder.py`

```python
def build_chat_system_prompt(settings, knowledge: list[KnowledgeEntry]):
    base = "...现有规则..."
    if knowledge:
        base += "\n\n【已知事实与偏好（跨对话共享）】\n"
        for entry in knowledge:
            base += f"- [{entry.category}] {entry.key}: {entry.value}\n"
    return base
```

**加载规则**：

- `preference`：全部加载
- `service_fact`：优先当前活跃服务 + 最近更新 20 条
- 总量上限约 2000 token，超出按 `updated_at` 裁最旧

---

### 6.4 P1-4：长期记忆管理 UI

**干什么**：用户可查看、编辑、删除记忆。

**设置页新增「AI 记忆」面板**：

- 列表：分类、键、值、来源对话、更新时间
- 操作：编辑、删除、手动添加
- 开关：自动提取记忆 on/off

**新增 API**：

| API | 方法 | 作用 |
|-----|------|------|
| `/api/chat/knowledge` | GET | 列表 |
| `/api/chat/knowledge` | POST | 手动添加 |
| `/api/chat/knowledge/{id}` | PUT | 编辑 |
| `/api/chat/knowledge/{id}` | DELETE | 删除 |

### 6.5 P1 验收标准

- [ ] `read_log` 返回大日志时，压缩后单轮不撑满窗口
- [ ] 对话中学到的服务路径，新建对话后 AI 仍知道
- [ ] 设置页可查看、编辑、删除记忆条目
- [ ] 自动提取与用户确认两种写入方式均可用

---

## 7. P2：滚动摘要 + 分级策略

**目标**：单对话聊很长时仍能继续；接近上限时自动压缩，减少报错。

### 7.1 P2-1：Token 预算管理器

**干什么**：发消息前评估用量，决定压缩动作。

**新建文件**：`agent/langchain/context_budget.py`

```python
@dataclass
class BudgetReport:
    used: int
    limit: int
    percent: float
    overflow_reason: str | None  # None / "current_turn" / "accumulated"
    actions: list[str]           # 将要执行的压缩动作
```

**用量计算公式**：

```
total = system_prompt_tokens
      + knowledge_tokens
      + conversation.summary_tokens
      + recent_messages_tokens
      + new_input_tokens
      + estimated_tool_reserve     # 预留一轮工具返回，如 8K
```

---

### 7.2 P2-2：分级策略

**干什么**：按用量百分比触发不同压缩动作。

**新建文件**：`agent/langchain/context_policy.py`

| 档位 | 阈值 | 动作 |
|------|------|------|
| **Green** | < 60% | 不压缩 |
| **Yellow** | 60%～80% | 压缩历史中的旧 tool 消息（保留最近 5 轮完整） |
| **Orange** | 80%～90% | 对早期对话做滚动摘要 + 继续压缩旧 tool |
| **Red** | 90%～100% | 摘要 + 仅保留最近 5 轮 + UI 提示「建议新建对话」 |
| **Blocked** | ≥ 100% | 按溢出原因分两支处理（见下） |

#### 单轮满（`overflow_reason = current_turn`）

问题出在当前轮工具返回过大，旧历史不是主因。

```
1. 对当前轮工具结果激进压缩（只保留 ERROR 行等）
2. 仍超限 → 拒绝发送，提示：
   「本次查询范围过大，请指定服务名/时间段/关键字」
3. 不触发滚动摘要
```

#### 累计满（`overflow_reason = accumulated`）

问题是同一对话内多轮累积过长。

```
1. 执行 Yellow 动作（压缩旧 tool）
2. 仍超 → Orange 滚动摘要
3. 仍超 → Red 缩窗（只留最近 5 轮）
4. 仍超 → 提示新建对话（重要信息已在长期记忆中）
```

---

### 7.3 P2-3：滚动摘要器

**干什么**：将早期对话压缩为短摘要，释放 token。

**新建文件**：`agent/langchain/conversation_summarizer.py`

**触发条件**：Orange 及以上，或每 30 轮自动触发。

**流程**：

```
输入：
  - 当前 conversation.summary（可能已有旧摘要）
  - 需要摘要的早期消息（第 1 轮 ～ 第 N-10 轮）

LLM 摘要要求（500 字以内）：
  1. 用户偏好
  2. 服务路径/配置
  3. 已确认故障结论
  4. 未解决问题
  不要编造。

输出：
  - 更新 conversations.summary
  - 已摘要消息标记 archived 或从 checkpointer 删除
```

**压缩后发给模型的上下文**：

```
system prompt
+ 长期记忆
+ 【本对话早期摘要】conversation.summary
+ 最近 10 轮完整消息
+ 本轮新消息
```

---

### 7.4 P2-4：压缩执行器

**干什么**：统一执行各档位的压缩动作。

**新建文件**：`agent/langchain/context_compactor.py`

| 方法 | 作用 |
|------|------|
| `compress_old_tool_messages(conv_id, keep_recent=5)` | 压缩历史 tool 消息 |
| `apply_rolling_summary(conv_id)` | 调用 summarizer |
| `shrink_recent_window(conv_id, keep=5)` | Red 档缩窗 |
| `aggressive_compress_current_tool(output)` | 单轮激进压缩 |

**集成到 ChatAgent**：

```python
async def handle_message(conversation_id, text):
    budget = evaluate_budget(...)
    if budget.actions:
        await context_compactor.execute(budget.actions, conversation_id)
    # 调用 LangGraph
    ...
    # 回复后：extract_memory + update_token_count + 返回 usage
```

---

### 7.5 P2-5：UI 用量与压缩反馈

**用量条示例**：

```
[████░░░░░░░░░░░░░░░░] 42K / 512K (8%)   🟢
[████████████░░░░░░░░] 310K / 512K (61%)  🟡 已压缩历史工具结果
[██████████████████░░] 470K / 512K (92%)  🔴 建议新建对话
```

**系统消息**（压缩时插入 `chat_messages`）：

- 「上下文 Usage 61%，已自动压缩较早的工具返回。」
- 「上下文 Usage 92%，建议新建对话；重要信息已写入 AI 记忆。」

**用量 API 返回示例**：

```json
{
  "used": 470000,
  "limit": 524288,
  "percent": 89.6,
  "level": "orange",
  "summary_tokens": 800,
  "recent_turns": 10,
  "last_compaction": "2026-07-04T18:30:00",
  "actions_applied": ["compress_old_tools", "rolling_summary"]
}
```

### 7.6 P2 验收标准

- [ ] 同一对话 50+ 轮后仍可继续（有摘要/压缩）
- [ ] 60/80/90% 各档位触发正确动作
- [ ] 单轮超大时走 `current_turn` 分支，不误触发滚动摘要
- [ ] UI 显示档位颜色与压缩提示

---

## 8. 压缩策略速查

### 8.1 各策略解决什么问题

| 策略 | 单轮满 | 累计满 | 跨对话记服务路径/偏好 |
|------|--------|--------|------------------------|
| 工具压缩（P1） | ✅ 主力 | ✅ 也压历史旧 tool | ❌ |
| 长期记忆（P1） | ❌ 不救场 | ⚪ 间接（敢删历史） | ✅ 主力 |
| 滚动摘要（P2） | ❌ 无效 | ✅ 主力 | ⚪ 配合长期记忆 |
| 分级策略（P2） | ✅ 调度 | ✅ 调度 | ❌ |

### 8.2 策略选择逻辑

```
发消息前估算 token
        ↓
  单轮就 > 100%？
    ├─ 是 → 工具激进压缩 → 仍超 → 拒绝并提示缩小范围
    └─ 否 → 检查累计用量
              ├─ < 60%  → 不压缩
              ├─ 60～80% → 压缩旧 tool
              ├─ 80～90% → 滚动摘要
              └─ > 90%  → 缩窗 + 提示新建对话
```

---

## 9. 配置扩展

在 `data/config.yaml` 中新增：

```yaml
chat:
  context_limit: null          # null = 自动从模型读取
  tool_compression:
    enabled: true
    keep_raw: false
    log_tail_lines: 80
    log_error_scan: true
  memory:
    auto_extract: true
    max_inject_tokens: 2000
  policy:
    yellow_threshold: 0.6
    orange_threshold: 0.8
    red_threshold: 0.9
    keep_recent_turns: 10
    summary_trigger_turns: 30
```

---

## 10. 文件改动总览

| 阶段 | 新建文件 | 修改文件 |
|------|----------|----------|
| **P0** | `store/chat.py`, `langchain/checkpointer.py`, `langchain/token_meter.py`, `langchain/context_limits.py` | `chat_graph.py`, `agent_service.py`, `routes.py`, `chat_panel.py`, `app.js` |
| **P1** | `langchain/tool_compress.py`, `store/knowledge.py`, `langchain/memory_extractor.py` | `tools.py`, `context_builder.py`, `settings_page.py`, `models.py` |
| **P2** | `langchain/context_budget.py`, `langchain/context_policy.py`, `langchain/conversation_summarizer.py`, `langchain/context_compactor.py` | `chat_graph.py`, `chat_panel.py`, `models.py` |

---

## 11. 工期估算

| 步骤 | 内容 | 预估 |
|------|------|------|
| P0-1 | AsyncSqliteSaver | 0.5 天 |
| P0-2 | ChatStore 表 + CRUD | 1 天 |
| P0-3 | TokenMeter + context_limits | 0.5 天 |
| P0-4 | ChatAgent 接入 Store | 1 天 |
| P0-5 | 多对话 UI + API | 1.5 天 |
| **P0 小计** | | **~4.5 天** |
| P1-1 | 工具压缩 | 1 天 |
| P1-2 | KnowledgeStore + 提取 | 1.5 天 |
| P1-3 | 注入 system prompt | 0.5 天 |
| P1-4 | 记忆管理 UI | 1 天 |
| **P1 小计** | | **~4 天** |
| P2-1 | Budget 评估 | 0.5 天 |
| P2-2 | 分级策略 | 1 天 |
| P2-3 | 滚动摘要 | 1 天 |
| P2-4 | Compactor 集成 | 1 天 |
| P2-5 | 用量 UI + 提示 | 0.5 天 |
| **P2 小计** | | **~4 天** |

**合计约 12～13 天**（含测试与联调）。

---

## 12. 附录

### 12.1 当前模型参考

本机 Ollama 模型 `minimax-m3:cloud`：

```
context length: 524288（约 512K）
```

查询命令：

```powershell
ollama show minimax-m3:cloud
# 云端模型不要用 --modelfile（无本地 Modelfile）
```

### 12.2 数据文件位置

| 内容 | 开发模式 | 打包版 |
|------|----------|--------|
| 业务配置 | `data/config.yaml` | `%APPDATA%\SteadyOps\data\config.yaml` |
| 告警库 | `data/agent.db` | 同上目录 |
| 对话库（新） | `data/chat.db` | 同上目录 |
| LangGraph 检查点（新） | `data/chat_checkpoints.db` | 同上目录 |

### 12.3 实施建议

建议从 **P0-1（AsyncSqliteSaver）+ P0-2（ChatStore 表结构）** 开始，这是后续所有能力的基础。P0 完成并验收后再进入 P1，避免同时改动过多模块。

---

## 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-07-04 | 初稿 |
