# SteadyOps 桌面端交付形态改造方案

> 日期：2026-08-21  
> 背景：当前以「单文件裸 exe」分发，带来 UI 观感差、运行不稳定、无法在线更新等问题。  
> 结论：值得做成「可安装软件」，但不必立刻换 Electron；优先 **onedir 目录包 + Inno Setup 安装程序 + 自研在线更新（方案 B）+ UI 渐进升级**。

## 目录

- [现状](#现状)
- [结论](#结论)
- [三个问题分别怎么解](#三个问题分别怎么解)
- [在线更新：选定方案 B（自研）](#在线更新选定方案-b自研)
- [最终安装包与安装后形态](#最终安装包与安装后形态)
- [分阶段路线图](#分阶段路线图)
- [实施步骤（按此顺序执行）](#实施步骤按此顺序执行)
- [不建议的做法](#不建议的做法)
- [相关代码与文件](#相关代码与文件)

---

## 现状

| 维度 | 当前做法 |
|------|----------|
| UI | PySide6 原生控件 + Fusion/QSS（`agent/desktop/`） |
| 打包 | PyInstaller **onedir** `dist/SteadyOps/`（`build/inspection-agent.spec` + `scripts/build.ps1`）；安装包脚本 `installers/SteadyOps.iss`（`-Installer`） |
| 分发 | 拷贝 / 双击运行，无安装目录、无卸载项 |
| 用户数据 | `%APPDATA%\SteadyOps\`（与 exe 位置分离，利于后续升级） |
| 更新 | 方案 B 自研（`agent/updater/`）；设置页配置 `update.feed_url`；见 `releases/README.md` |
| 安装器 | `installers/SteadyOps.iss`（Inno Setup）；`.\scripts\build.ps1 -Installer` 产出 Setup |
| 次要 UI | 另有 FastAPI Web 控制台（`agent/web/`），打包时默认排除 |

单文件 + UPX + 胖依赖（LangChain 等）是不稳定与体验差的主要交付层原因；与「要不要重写前端」是两件可分开解决的事。

---

## 结论

**三个问题本质是同一套交付形态带来的，可以一起治。**

更稳妥的路径：

```text
onedir 目录包
  → Inno Setup 安装程序（默认装到用户目录，少 UAC）
  → 自动更新：方案 B 自研（version.json → 下 Setup → 静默安装 → 重启）
  → UI 渐进升级（先打磨 Qt，再考虑内嵌已有 Web）
```

比继续发裸 exe 强很多，也比重写一整套桌面端（Electron 等）便宜。

**在线更新已拍板：选方案 B（自研轻量更新），不采用 WinSparkle。** 理由见下文对照表。

---

## 三个问题分别怎么解

| 问题 | 根因（现状） | 推荐做法 |
|------|--------------|----------|
| **exe 不稳定** | 单文件每次解压到临时目录；UPX；冷启动慢；杀软易误报；GUI 线程阻塞（见缺陷审查 P3-3 / P3-4） | 改 **onedir**；关闭 UPX；程序常驻安装目录；同步修复桌面异步阻塞 |
| **无法在线更新** | 只有手动换 exe，无版本通道 | 固定安装目录 + **方案 B 自研更新**（查 `version.json` → 下 Setup → 校验 → `/SILENT` → 重启）；**只更新程序目录，不动 AppData** |
| **UI 丑** | 原生 Qt + 简单 QSS，信息密度高、视觉层级弱 | **短期**统一主题/图标/间距；**中期**用已有 `agent/web` 内嵌（QWebEngineView / pywebview）；**长期**再评估 Tauri/Electron（成本最高） |

### 问题展开

#### 1. exe 不稳定

- 单文件模式（`onefile`）启动时解包到 `%TEMP%\_MEI*`，杀软扫描、磁盘抖动、偶发文件占用都会放大故障面。
- UPX 压缩进一步增加误报概率。
- 业务侧另有 GUI 线程同步等待异步任务等问题，会表现为「卡死 / 无响应」，与打包问题叠加。

**目标形态**：`dist/SteadyOps/` 目录结构（exe + `_internal/` 依赖），由 Inno Setup 写入固定安装目录（见下文）。

#### 2. 无法在线更新

- 今日分发模型是「换一个 exe」，无签名版本通道、无静默升级。
- 用户数据已在 `%APPDATA%\SteadyOps\`，适合「覆盖安装程序、保留配置与库」。

**目标形态（方案 B）**：

1. 发布产物：`SteadyOps-Setup-x.y.z.exe`（Inno Setup）+ `version.json`
2. 客户端启动或定时检查版本
3. 有新版本则下载 Setup → SHA256 校验 → `/SILENT` 升级 → 重启应用

#### 3. UI 丑

不必为了好看先换框架。可选路线：

| 路线 | 做法 | 适合 |
|------|------|------|
| A. 继续 PySide6 | 统一设计规范、字体/图标、减少表单堆砌 | 改动小、要快出 |
| B. 内嵌 Web（中期推荐） | 复用 `agent/web`，Qt/WebView 包一层 | UI 更好做，后端仍是 Python |
| C. Tauri / Electron 重写前端 | 前端全新，Python 作本地 sidecar | 长期产品化，成本最高 |

对 SteadyOps 这类运维工具，UI 路线里的 **B（内嵌 Web）通常比 C 更划算**。（注意：此处「UI 路线 B」与「更新方案 B」不是同一件事。）

---

## 在线更新：选定方案 B（自研）

### 方案对照

| 维度 | A WinSparkle | **B 自研（已选定）** |
|------|--------------|----------------------|
| 和 PySide6 / Python 集成 | 需桥接原生 DLL，打包与调试更绕 | 纯 Python，与现有代码同栈 |
| 和 Inno Setup | 能用，但需维护 appcast 格式 | 直接下载 Setup，`/SILENT` 最自然 |
| 内网 / 私有化 | 可以，仍要维护 XML Feed | 一个 `version.json` 即可，运维好懂 |
| 定制（强制更新、公告、灰度） | 能力有限，改库成本高 | 按需加字段与策略 |
| 前期工作量 | 「看起来省事」，集成期往往不省 | MVP 步骤清晰、可控 |
| 后期扩展 | 容易顶到天花板 | 易演进（签名校验、渠道、差分等） |
| 权限 / UAC | 同样要面对 | 同样要面对（默认用户目录可减轻） |

**选定 B 的原因（摘要）**：交付物已是 Setup；技术栈一致；私有化只需静态 JSON；后期强制升级/公告/灰度都更好加。WinSparkle 更适合纯 C++/Qt、公网标准 appcast、很少定制的产品——与 SteadyOps 不完全匹配。

### 方案 B 演进步骤

1. **MVP**：启动检查 `version.json` → 提示有新版本 → 下载 Setup → 校验 SHA256 → 静默安装 → 重启  
2. **产品化**：设置页「检查更新」、更新说明、稍后提醒、失败重试  
3. **加固**：HTTPS、安装包代码签名、可选「最低强制版本」  
4. **再往后**：按需差分包、灰度渠道（稳定 / 测试）——仍在同一套自研框架上扩展  

### 在线更新所需条件（摘要）

| 条件 | 说明 |
|------|------|
| 可安装形态 | 固定安装目录 + AppData 数据分离（阶段 1） |
| 发布通道 | 用户机能访问的地址，托管 Setup + `version.json`（内网或公网均可） |
| 版本号一致 | 安装包、客户端、`version.json`、建议与 `pyproject.toml` / CHANGELOG 对齐 |
| 客户端更新逻辑 | 方案 B：查版本 → 下载 → 校验 → 静默装 → 重启 |
| 建议后补 | 代码签名、HTTPS、强制升级策略 |

---

## 最终安装包与安装后形态

### 发给用户 / 给自动更新用的是什么

对外主产物通常是 **一个安装包文件**：

```text
SteadyOps-Setup-0.3.0.exe
```

由 Inno Setup 打包 PyInstaller **onedir** 目录（不是现在的单文件绿 exe）。

发布服务器（内网 Nginx / OSS / 文件服务器）示意：

```text
releases/
  SteadyOps-Setup-0.3.0.exe
  SteadyOps-Setup-0.3.1.exe
  version.json
```

`version.json` 示例：

```json
{
  "version": "0.3.1",
  "url": "https://your-cdn.example/releases/SteadyOps-Setup-0.3.1.exe",
  "sha256": "abc123...",
  "notes": "修复告警时间显示；优化启动稳定性"
}
```

- **首次安装**：用户手动下载并运行 Setup。  
- **后续升级**：客户端按 `version.json` 自动下载新 Setup 并静默安装。

### 安装向导（首次）用户看到什么

1. 欢迎 / 许可（可精简）  
2. 选择安装目录（可默认）  
3. 是否创建桌面图标  
4. 安装进度  
5. 完成 → 可勾选立即启动  

自动更新时一般不展示完整向导：提示 → 下载 → `/SILENT` → 重启应用。

### 安装完成后的目录结构

#### 推荐默认：当前用户目录（少 UAC，与方案 B 更搭）

```text
%LOCALAPPDATA%\Programs\SteadyOps\
  SteadyOps.exe              ← 主程序
  Uninstall.exe              ← Inno 生成
  _internal\                 ← onedir 依赖（dll / pyd / 资源）
```

开始菜单：

```text
开始菜单 → SteadyOps → SteadyOps
                 └─ 卸载 SteadyOps
```

可选桌面快捷方式：`桌面\SteadyOps.lnk` → 上述 `SteadyOps.exe`。

#### 用户数据（与程序分离，升级不覆盖）

```text
%APPDATA%\SteadyOps\
  config.yaml
  *.db
  logs\
  ...
```

#### 可选变体：企业要求装系统目录

```text
C:\Program Files\SteadyOps\
  SteadyOps.exe
  _internal\
  Uninstall.exe
```

此时安装/更新往往需要管理员权限；逻辑相同，仅路径与 UAC 不同。

### 与「现在绿 exe」对比

| | 现在 | 选定 B 之后 |
|--|------|-------------|
| 用户拿到的 | `SteadyOps.exe`（单文件） | `SteadyOps-Setup-x.y.z.exe` |
| 装完 | 无安装，解压到临时目录跑 | 固定目录 + 开始菜单 + 可卸载 |
| 依赖 | 藏在临时 `_MEI*` | 固定在 `_internal\` |
| 配置 | 已在 AppData | 仍在 AppData（不变） |
| 更新 | 人工换 exe | 下新 Setup，覆盖程序目录后重启 |

### 一次自动更新时发生什么

假设已装 `0.3.0`，`version.json` 指向 `0.3.1`：

1. 客户端发现新版本 → 下载 `SteadyOps-Setup-0.3.1.exe`（可先放临时目录）  
2. 校验 SHA256 → 以静默参数运行安装包  
3. 覆盖 `%LOCALAPPDATA%\Programs\SteadyOps\`（或 Program Files 变体）  
4. 重启 `SteadyOps.exe`  
5. `%APPDATA%\SteadyOps\` **不动** → 主机、SSH、飞书等配置与告警数据保留  

用户体感：「软件更新完了，设置还在。」

---

## 分阶段路线图

### 阶段 1：做成正经安装软件（优先，性价比最高）

1. PyInstaller 改为 **`onedir`**（去掉 `onefile`），关闭 UPX  
2. 构建脚本产出目录包，而非单个 exe  
3. 用 **Inno Setup** 打出 `SteadyOps-Setup-x.y.z.exe`  
   - **默认**安装到 `%LOCALAPPDATA%\Programs\SteadyOps\`  
   - 可选变体：`C:\Program Files\SteadyOps\`  
   - 开始菜单 / 可选桌面快捷方式 / 卸载项  
   - 升级时覆盖程序文件，**保留** `%APPDATA%\SteadyOps\`  
4. 发布流程：打版本号 → `.\scripts\build.ps1 -Installer`（自动生成 `dist/version.json` 含 sha256）→ 上传 Setup + version.json 到下载目录

**预期收益**：安装/卸载体验正常；稳定性明显改善；为方案 B 自动更新打底。

### 阶段 2：在线更新（方案 B）

实现自研更新器：

- 启动或定时请求 `version.json`  
- 比较本地版本与远端版本  
- 下载 Setup → SHA256 校验 → `/SILENT` 安装 → 重启  

约束：

- 更新目标仅为安装目录  
- 配置、SQLite、日志留在 AppData  
- 版本号与 `pyproject.toml` / CHANGELOG 对齐  
- **打包时**由 `scripts/build.ps1 -Installer` 同步写出 `dist/version.json`（默认下载根：`http://106.120.201.126:14828/down/steadyOps`）  

### 阶段 3：UI 渐进升级

1. 先修已知桌面卡顿/崩溃类问题（缺陷审查 P3）  
2. 再打磨 QSS 与信息架构  
3. 评估是否将主界面切到内嵌 Web；Qt 可保留托盘、开机启动、系统集成  

### 建议落地顺序（一句话）

```text
onedir + Inno 安装包 + 修 GUI 卡死
  → 方案 B：version.json + 静默 Setup 更新
  → Web UI 内嵌或继续打磨 Qt
```

---

## 实施步骤（按此顺序执行）

总原则：**先能装、再能更、最后再碰 UI**；中间不要并行开三条线。

```text
阶段 1 安装形态  →  阶段 2 自研更新  →  阶段 3 UI
     ↑                    ↑
  没有固定安装目录，更新做不稳
```

### 第一步：阶段 1 打底（优先做完）

1. **改打包**
   - `build/inspection-agent.spec`：`onefile` → `onedir`，关闭 UPX
   - `scripts/build.ps1`：产出 `dist/SteadyOps/`（exe + `_internal`）
2. **写 Inno Setup 脚本**
   - 默认装到 `%LOCALAPPDATA%\Programs\SteadyOps\`
   - 开始菜单、可选桌面图标、卸载项
   - 明确：**不碰** `%APPDATA%\SteadyOps\`
3. **打出版本化安装包**
   - 产物名：`SteadyOps-Setup-x.y.z.exe`
   - 版本与 `pyproject.toml` 对齐
4. **本机验收**
   - 安装 → 启动 → 配置仍在 → 卸载行为符合约定
   - 再装一遍覆盖升级，确认 AppData 不被清空
5. **（建议穿插）修已知 GUI 卡死**
   - 缺陷审查 P3-3 / P3-4，减少「装上了但仍觉得不稳」

**阶段 1 完成标志**：不再发绿 exe，只发 Setup；能装、能卸、能覆盖升级。

### 第二步：阶段 2 方案 B 更新

1. **搭发布通道**
   - 内网 / OSS 放置：`SteadyOps-Setup-*.exe` + `version.json`
2. **客户端读本地版本**
   - 从打包资源或安装信息读出当前版本
3. **实现更新器 MVP**
   - 启动检查 → 比版本 → 下载 → SHA256 → `/SILENT` → 重启
   - 失败有提示，不静默吞掉
4. **联调**
   - 装旧版 → 改 `version.json` 指向新 Setup → 确认自动升级且配置仍在
5. **再产品化**
   - 设置页「检查更新」、更新说明、稍后提醒；需要再上强制最低版本 / 代码签名

**阶段 2 完成标志**：只改发布侧 `version.json`，已装客户端即可升到新 Setup。

> 实现状态（2026-08-21）：MVP 已合入——`agent/updater/`、设置页「在线更新」、启动检查；`build.ps1 -Installer` 自动生成 `dist/version.json`（含 sha256）。上传 Setup + version.json 到下载目录即可。详见 `releases/README.md`。

### 第三步：阶段 3 UI（更新跑通后再做）

1. 先打磨现有 PySide6（快）
2. 再评估是否内嵌 `agent/web`
3. 不轻易上 Electron

### 建议迭代节奏

| 迭代 | 交付物 |
|------|--------|
| Sprint A | onedir 能跑通 |
| Sprint B | Inno Setup 可安装包 + 验收清单 |
| Sprint C | 发布目录 + `version.json` + 更新 MVP |
| Sprint D | 更新体验打磨 +（可选）GUI 稳定性修复 |
| 之后 | UI 渐进 |

### 当前立刻该动的第一件事

~~改 `build/inspection-agent.spec` 与 `scripts/build.ps1`，把 **单文件改成 onedir**~~（已完成）。

下一步：本机执行 `.\scripts\build.ps1` 验证 onedir；安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php) 后执行 `.\scripts\build.ps1 -Installer` 产出 `dist\SteadyOps-Setup-x.y.z.exe`。

---

## 不建议的做法

| 做法 | 原因 |
|------|------|
| 继续发单文件 exe + 手动覆盖 | 更新与稳定性都难做好 |
| 为了「像安装包」先上 Electron | 双运行时、体积、与现有 PySide6 逻辑成本高 |
| 只换皮肤不改打包方式 | 观感略好，不稳与不能更新仍在 |
| 把用户数据塞进安装目录 | 升级/权限问题多，与现有 AppData 设计相悖 |
| 再改回 WinSparkle 作为主方案 | 与已选定的 Setup + Python 栈重复建设，后期定制更差 |

---

## 相关代码与文件

| 用途 | 路径 |
|------|------|
| 桌面入口 | `agent/launcher.py` → `agent/desktop/app.py` |
| UI / 主题 | `agent/desktop/`、`agent/desktop/theme.py`、`styles.qss` |
| 打包 spec | `build/inspection-agent.spec` |
| 构建脚本 | `scripts/build.ps1`（`-Installer` 打 Setup） |
| Inno 脚本 | `installers/SteadyOps.iss` |
| 用户数据目录 | `agent/paths.py`（`%APPDATA%\SteadyOps`） |
| Web 控制台（可内嵌） | `agent/web/` |
| 稳定性相关缺陷 | `docs/ops-defects-review.md`（P3-2 / P3-3 / P3-4） |
| 本方案文档 | `docs/desktop-delivery-roadmap.md` |
