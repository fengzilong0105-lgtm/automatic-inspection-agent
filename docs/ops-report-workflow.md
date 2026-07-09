# SteadyOps 巡检-问题分析-报告生成-工单归档方案

> 版本：v1.0  
> 日期：2026-07-09  
> 状态：设计稿，待实施  
> 工单形态：**A — 飞书多维表格（Bitable）**

## 1. 背景与目标

### 1.1 业务诉求

运维人员在 **AI 对话** 或 **巡检/检查** 中发现问题后，希望形成一套闭环：

```
巡检 → 问题识别 → LLM 梳理报告 → 人工确认 → 飞书文档归档 → 飞书工单（多维表格）
```

报告需包含：

| 字段 | 说明 |
|------|------|
| 问题名称 | 简明标题 |
| 问题描述 | 现象、影响 |
| 具体文档 | 完整分析报告（飞书文档链接） |
| 发起人 | 桌面运维姓名 / 飞书用户 |

### 1.2 与现有系统的关系

SteadyOps 已具备巡检、告警、AI 分析、飞书群通知等能力，但缺少「结构化报告 + 文档归档 + 工单台账」。

| 环节 | 现状 | 本方案补齐 |
|------|------|------------|
| 巡检 | `MonitorLoop`、`run_inspection`、playbook | 作为问题来源 |
| 问题发现 | `Incident` 入库 | 关联 `ProblemCase` |
| 问题分析 | `analyze_incident`、AI 对话 + 工具 | 取证 + LLM 梳报告 |
| 报告生成 | playbook 有 JSON 报告，无统一模型 | `ProblemCase` + Markdown |
| 飞书通知 | `send_feishu_text` 群消息 | 保留，发布后发链接 |
| 飞书归档 | 无 | **飞书文档 API** |
| 工单 | 无 | **飞书多维表格 Bitable（方案 A）** |

---

## 2. 总体架构

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ 自动巡检      │  │ AI 对话发现   │  │ 告警页手动    │
│ Incident     │  │ Chat + Tools │  │ 选 Incident  │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         ▼
              ┌─────────────────────┐
              │ EvidenceCollector   │  取证（工具/日志/playbook）
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │ ReportComposer      │  LLM 结构化报告（draft）
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │ ProblemCase (本地)   │  草稿 / 审核 / 已发布
              └──────────┬──────────┘
                         ▼ 人工确认
              ┌─────────────────────┐
              │ FeishuPublisher     │
              │  · 飞书文档归档      │
              │  · Bitable 工单行    │
              │  · 群消息通知        │
              └─────────────────────┘
```

### 2.1 设计原则

1. **先结构化，再生成文档** — LLM 填充 `ProblemCase`，不直接拼飞书 API 载荷  
2. **发布前必须人工确认** — 根因、路径、命令需运维审核  
3. **证据与结论分离** — 证据来自工具/巡检；结论标注【已核实/待核实】  
4. **发起人可追溯** — 桌面配置运维姓名；飞书 bot 用 `user_id`  
5. **Incident 双向关联** — `incident_id ↔ case_id`，便于闭环  
6. **复用 playbook** — OOM/CPU 等报告作为证据，避免重复分析  

---

## 3. 核心数据模型

### 3.1 ProblemCase（问题案例）

本地中枢对象，存于 `data/agent.db`（或独立 `data/ops_cases.db`）。

```python
class ProblemCaseSource(str, Enum):
    INSPECTION = "inspection"
    INCIDENT = "incident"
    CHAT = "chat"
    MANUAL = "manual"


class ProblemCaseStatus(str, Enum):
    DRAFT = "draft"              # 草稿
    REVIEWING = "reviewing"      # 待确认
    PUBLISHED = "published"      # 已发布飞书文档
    TICKET_CREATED = "ticket_created"  # 已建 Bitable 工单
    CLOSED = "closed"


class ProblemCase(BaseModel):
    id: str
    title: str                      # 问题名称
    description: str                # 问题描述（现象）
    severity: str                     # P0 / P1 / P2
    service_id: str
    host_id: str
    initiator: str                    # 发起人

    source: ProblemCaseSource
    source_ref: str                   # incident_id / conversation_id / playbook_run_id

    evidence: dict                    # 结构化证据（状态、日志、playbook 结果）
    analysis: str                     # 根因分析
    impact: str                       # 影响范围
    recommendations: list[str]        # 处置建议
    report_markdown: str              # 完整报告正文

    status: ProblemCaseStatus = ProblemCaseStatus.DRAFT

    incident_id: str | None = None    # 关联告警

    feishu_doc_token: str | None = None
    feishu_doc_url: str | None = None
    feishu_bitable_record_id: str | None = None  # 多维表格行 ID

    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
```

### 3.2 EvidenceBundle（取证包）

```python
class EvidenceBundle(BaseModel):
    service_id: str
    host_id: str
    collected_at: datetime

    incident: dict | None = None          # Incident 快照
    service_status: dict | None = None
    deployment_info: dict | None = None
    log_tail: str | None = None
    playbook_reports: list[dict] = []     # OOM/CPU 等
    chat_excerpt: str | None = None       # 对话摘要
    tool_outputs: list[dict] = []         # 工具调用记录
```

---

## 4. 四步流水线

### 4.1 Step 1：取证（Evidence Collector）

**模块**：`agent/ops/evidence_collector.py`

按来源拉取事实，禁止 LLM 编造：

| 来源 | 收集内容 |
|------|----------|
| `Incident` | title、summary、log_snippet、severity |
| Playbook | `assess_oom_risk`、`assess_cpu_risk` 等 |
| 服务状态 | `get_service_status`、`get_deployment_info` |
| 对话 | 最近 N 轮 + 工具结果（`conversation_id`） |
| 可选 | `read_log` 尾部、关键配置片段 |

```python
async def collect_from_incident(incident_id: str) -> EvidenceBundle: ...
async def collect_from_chat(conversation_id: str, hint: str | None) -> EvidenceBundle: ...
async def collect_from_service(service_id: str) -> EvidenceBundle: ...
```

### 4.2 Step 2：LLM 梳理（Report Composer）

**模块**：`agent/ops/report_composer.py`

- 输入：`EvidenceBundle` + 报告模板 + `initiator`
- 输出：`ProblemCase`（`status=draft`）
- 使用 Pydantic `with_structured_output` 约束 JSON
- Prompt 要求：结论标注【已核实/待核实】，与现有对话规范一致

**报告 Markdown 模板**：

```markdown
# {问题名称}

| 项目 | 内容 |
|------|------|
| 发起人 | {initiator} |
| 服务 | {service_id} |
| 主机 | {host_id} |
| 严重级别 | {severity} |
| 发现时间 | {created_at} |
| 来源 | 巡检 / AI对话 / 手动 |

## 1. 问题描述

{description}

## 2. 影响范围

{impact}

## 3. 证据与现象

{evidence_summary}

## 4. 根因分析

{analysis}

## 5. 处置建议

{recommendations}

## 6. 后续跟踪

- [ ] 待指派处理人
- [ ] 待验证恢复
```

### 4.3 Step 3：人工确认（Review Gate）

**必须人工确认后再发布飞书。**

| 入口 | 操作 |
|------|------|
| 桌面端「问题报告」页 | 列表 → 预览 Markdown → 编辑 → 发布 |
| Web 控制台 | 同上 |
| AI 对话 | 生成草稿后提示「请到问题报告页确认」 |

**API**：

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/ops/cases` | 从 incident/chat/service 创建草稿 |
| GET | `/api/ops/cases` | 列表 |
| GET | `/api/ops/cases/{id}` | 详情 |
| PUT | `/api/ops/cases/{id}` | 人工修改 |
| POST | `/api/ops/cases/{id}/publish` | 发布飞书文档 + Bitable 工单 |
| POST | `/api/ops/cases/{id}/close` | 关闭案例 |

### 4.4 Step 4：飞书归档（Publisher）

**模块**：

- `agent/feishu/doc_client.py` — 飞书文档
- `agent/feishu/bitable_client.py` — 多维表格工单
- `agent/feishu/publisher.py` — 统一发布门面

**发布流程（一次确认，三步写入）**：

```
1. 创建飞书文档（docx），写入 report_markdown 章节
2. 在 Bitable 新增一行工单记录（含文档链接）
3. 向运维群发送通知消息（含文档链接 + 工单编号）
4. 更新 ProblemCase.status = ticket_created，回写 URL 与 record_id
```

---

## 5. 工单方案 A：飞书多维表格（Bitable）

### 5.1 选型说明

| 方案 | 说明 | 选择 |
|------|------|------|
| **A 多维表格** | Bitable 一行一条工单，可筛选/指派 | ✅ 本期 |
| B 飞书文档 + 群通知 | 仅文档无台账 | 文档作为附件，不单做工单 |
| C 禅道/飞书审批 | 正式流程审批 | 后续扩展 |

### 5.2 Bitable 表结构（建议）

在飞书创建多维表格「SteadyOps 运维工单」，字段：

| 字段名 | 类型 | 来源 |
|--------|------|------|
| 工单编号 | 自动编号 | Bitable 自带 |
| 问题名称 | 文本 | `ProblemCase.title` |
| 问题描述 | 多行文本 | `ProblemCase.description` |
| 严重级别 | 单选 | P0 / P1 / P2 |
| 服务 | 文本 | `service_id` |
| 主机 | 文本 | `host_id` |
| 发起人 | 文本 | `initiator` |
| 来源 | 单选 | 巡检 / AI对话 / 手动 |
| 状态 | 单选 | 待处理 / 处理中 / 已关闭 |
| 报告链接 | 超链接 | `feishu_doc_url` |
| SteadyOps Case ID | 文本 | `ProblemCase.id` |
| Incident ID | 文本 | 可选，关联告警 |
| 创建时间 | 日期 | `created_at` |
| 发布时间 | 日期 | `published_at` |

### 5.3 所需飞书权限

应用需开通（飞书开放平台）：

| 权限 | 用途 |
|------|------|
| `docx:document` | 创建/编辑飞书文档 |
| `bitable:app` | 读写多维表格 |
| `im:message` | 群通知（已有） |

配置项（`config.yaml`）：

```yaml
ops_report:
  auto_draft_on_incident: true       # P0/P1 自动起草（不自动发布）
  auto_publish: false                # 必须人工确认
  initiator_default: "运维值班"

  feishu:
    archive_folder_token: ""         # 飞书云文档归档文件夹 token
    bitable_app_token: ""            # 多维表格 app_token
    bitable_table_id: ""             # 数据表 table_id
    notify_chat_id: ""               # 发布通知群（可复用 alert_chat_id）
```

---

## 6. 三种触发场景

### 6.1 场景 A：巡检自动发现

```
MonitorLoop 产生 Incident (P0/P1)
        ↓
[若 auto_draft_on_incident=true]
EvidenceCollector(incident_id)
        ↓
ReportComposer → ProblemCase(draft)
        ↓
飞书群通知：「新问题草稿已生成，请在 SteadyOps 确认发布」
        ↓
运维确认 → publish → 文档 + Bitable + 群链接
```

### 6.2 场景 B：AI 对话中发现

```
运维：road_control 一直 OOM，整理成报告
        ↓
AI 调用工具取证（status、log、assess_oom_risk）
        ↓
AI 调用 create_problem_report(conversation_id=...)
        ↓
返回：「报告草稿已生成，请到【问题报告】页确认」
        ↓
人工确认 → 发布
```

**LangChain 新工具**：

| 工具名 | 作用 |
|--------|------|
| `create_problem_report` | 从当前对话/服务创建草稿 |
| `list_problem_reports` | 列出近期案例（可选） |

### 6.3 场景 C：告警页手动

```
告警记录页 → 选中 Incident →「生成报告」
        ↓
Evidence + LLM → ProblemCase(draft)
        ↓
跳转问题报告页预览 → 确认发布
```

---

## 7. 状态机

```
                    ┌─────────┐
         创建 ────→ │  draft  │
                    └────┬────┘
                         │ 人工打开审核
                         ▼
                    ┌─────────┐
                    │reviewing│
                    └────┬────┘
                         │ 点击「发布」
                         ▼
                    ┌─────────┐
                    │published│  飞书文档已创建
                    └────┬────┘
                         │ Bitable 行已写入
                         ▼
               ┌──────────────────┐
               │ ticket_created   │
               └────────┬─────────┘
                        │ 问题已解决
                        ▼
                    ┌─────────┐
                    │ closed  │
                    └─────────┘
```

| 状态 | 含义 |
|------|------|
| `draft` | LLM 已生成，待人工查看 |
| `reviewing` | 人工已打开，可编辑 |
| `published` | 飞书文档已创建 |
| `ticket_created` | Bitable 工单行已创建 |
| `closed` | 闭环结束，可回写 Incident 为 resolved |

---

## 8. 模块与文件规划

```
agent/
  ops/
    models.py                 # ProblemCase, EvidenceBundle, enums
    evidence_collector.py     # 取证
    report_composer.py        # LLM 梳报告
    case_store.py             # SQLite CRUD
    orchestrator.py           # 状态机：create / publish / close
  feishu/
    client.py                 # 已有：token、发消息
    doc_client.py             # 新建：飞书文档 create + 写 blocks
    bitable_client.py         # 新建：多维表格增删改查
    publisher.py              # 新建：publish_case(case_id)
  langchain/
    tools.py                  # + create_problem_report
  desktop/
    pages/cases_page.py       # 问题报告列表 + 预览 + 发布
  web/
    routes.py                 # /api/ops/cases/*
    static/                   # Web 问题报告页（可选）
```

### 8.1 数据库表（SQLite）

```sql
CREATE TABLE problem_cases (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL,
    service_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    initiator TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ref TEXT,
    evidence TEXT,                -- JSON
    analysis TEXT,
    impact TEXT,
    recommendations TEXT,         -- JSON array
    report_markdown TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    incident_id TEXT,
    feishu_doc_token TEXT,
    feishu_doc_url TEXT,
    feishu_bitable_record_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT
);
```

---

## 9. 分阶段实施计划

| 阶段 | 内容 | 预估 | 交付 |
|------|------|------|------|
| **M1** | `ProblemCase` 模型 + `case_store` + 从 Incident 生成草稿 + 桌面预览页 | 3 天 | 本地报告闭环 |
| **M2** | 飞书文档 API：`doc_client` 创建文档并写入报告 | 2 天 | 文档归档 |
| **M3** | Bitable 工单：`bitable_client` + `publisher` + 群通知 | 2 天 | 工单台账 |
| **M4** | AI 工具 `create_problem_report` + 告警页「生成报告」+ 巡检自动起草 | 2 天 | 三入口打通 |
| **M5** | 工单状态回写、Incident 联动关闭、负责人字段 | 2 天 | 完整闭环 |

**建议实施顺序**：M1 → M2 → M3 → M4 → M5

### 9.1 M1 验收标准

- [ ] 从 Incident 一键生成 `ProblemCase` 草稿
- [ ] 桌面端可预览、编辑报告 Markdown
- [ ] 草稿保存到本地 SQLite，可列表查看

### 9.2 M2 验收标准

- [ ] 点击「发布」后在飞书创建文档
- [ ] 文档内容与本地 `report_markdown` 一致
- [ ] `feishu_doc_url` 回写到 `ProblemCase`

### 9.3 M3 验收标准

- [ ] 发布时 Bitable 新增一行，字段完整
- [ ] 群消息含文档链接
- [ ] `feishu_bitable_record_id` 回写成功

### 9.4 M4 验收标准

- [ ] AI 对话可触发「生成报告草稿」
- [ ] P0/P1 Incident 可自动起草（不自动发布）
- [ ] 告警页有「生成报告」按钮

---

## 10. 飞书侧准备清单（实施前）

运维管理员需提前完成：

1. 在飞书开放平台为应用开通 `docx:document`、`bitable:app` 权限并发布版本  
2. 创建云空间文件夹「SteadyOps 报告归档」，获取 `folder_token`  
3. 创建多维表格「SteadyOps 运维工单」，按 §5.2 建字段，获取 `app_token`、`table_id`  
4. 将应用机器人拉入通知群  
5. 在 `config.yaml` 填写 `ops_report.feishu.*` 配置项  

---

## 11. 与现有模块映射

| 现有模块 | 本方案中的角色 |
|----------|----------------|
| `MonitorLoop` / `run_inspection` | 巡检入口，产生 Incident |
| `Incident` + `analyze_incident` | 问题分析输入 |
| `playbooks/build_report` | 取证包中的结构化证据 |
| `ChatAgent` + `tools.py` | 对话发现 + `create_problem_report` |
| `FeishuNotifier` | 发布后的群通知 |
| `agent/feishu/client.py` | token 基础，扩展 doc/bitable |

---

## 12. 风险与约束

| 风险 | 应对 |
|------|------|
| LLM 编造根因 | 证据包与结论分离；标注【待核实】；人工确认门 |
| 飞书 API 限流 | 发布失败重试；本地保留 draft 可再次发布 |
| 文档格式丢失 | Markdown → 飞书 block 需转换层；M2 先做纯文本/标题块 |
| 发起人缺失 | `initiator_default` + 设置页可配置；飞书 bot 映射 user_id |
| 重复建单 | 同一 `incident_id` 默认只允许一个 open case |

---

## 13. 后续扩展（非本期）

- 对接禅道 Bug（zentao MCP）作为 L3 工单  
- 飞书审批流  
- 自动指派负责人（按服务 owner 矩阵）  
- 报告模板按问题类型分化（OOM / 磁盘 / 网络）  

---

## 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-07-09 | 初稿；工单采用飞书多维表格方案 A |
