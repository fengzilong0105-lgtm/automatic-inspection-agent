const UNCHANGED = "__UNCHANGED__";

function dashboard() {
  return {
    hosts: [],
    allServices: [],
    summary: [],
    incidents: [],
    messages: [],
    conversations: [],
    chatUsage: "",
    input: "",
    activeHost: "",
    activeService: "",
    pendingRestart: null,
    pendingWrite: null,
    pendingMemory: null,
    knowledgeEntries: [],
    memoryAutoExtract: true,
    memoryDraft: { category: "service_fact", key: "", value: "" },
    sessionId: "",
    showSettings: false,
    setupCompleted: false,
    settingsForm: {
      llm: { provider: "openai", base_url: "", model: "", api_key: "", ollama_base_url: "http://localhost:11434", api_key_masked: null },
      feishu: {
        enabled: false,
        app_id: "",
        app_secret: "",
        alert_chat_id: "",
        bot: { command_enabled: false, command_chat_id: "", require_at_mention: true },
      },
    },
    hostEditor: null,
    hostEditorResult: "",
    settingsSshResult: "",
    settingsLlmResult: "",
    settingsFeishuResult: "",
    scanning: false,
    scanningHostId: "",
    scanningMessage: "",
    inspecting: false,
    inspectingMessage: "",
    summaryLoading: false,
    summaryError: "",
    view: "home",
    serviceFilter: "ok",
    chatSending: false,
    chatToolStatus: "",

    goHome() {
      this.view = "home";
      this.serviceSearch = "";
    },

    openServiceList(filter) {
      this.serviceFilter = filter;
      this.serviceSearch = "";
      this.view = "services";
    },

    openIncidents() {
      this.view = "incidents";
    },

    isServiceOk(item) {
      if (item.status.running !== true) return false;
      if (item.status.health_ok === false) return false;
      return true;
    },

    isServiceBad(item) {
      if (item.status.running === null || item.status.running === undefined) return false;
      return !this.isServiceOk(item);
    },

    pendingServiceCount() {
      return this.hostSummary().filter(
        (item) => !item.disabled && (item.status.running === null || item.status.running === undefined)
      ).length;
    },

    okServiceCount() {
      return this.hostSummary().filter((item) => this.isServiceOk(item)).length;
    },

    badServiceCount() {
      return this.hostSummary().filter((item) => this.isServiceBad(item)).length;
    },

    filteredServiceList() {
      const q = this.serviceSearch.trim().toLowerCase();
      return this.hostSummary()
        .filter((item) => (this.serviceFilter === "ok" ? this.isServiceOk(item) : this.isServiceBad(item)))
        .filter((item) => {
          if (!q) return true;
          const name = (item.service.name || item.service.id || "").toLowerCase();
          const id = (item.service.id || "").toLowerCase();
          return name.includes(q) || id.includes(q);
        });
    },

    serviceListTitle() {
      return this.serviceFilter === "ok" ? "正常服务" : "异常服务";
    },

    activeHostName() {
      const host = this.hosts.find((h) => h.id === this.activeHost);
      return host ? host.name : "";
    },

    formatDateTime(value) {
      if (!value) return "-";
      try {
        const d = new Date(value);
        if (Number.isNaN(d.getTime())) return String(value);
        return d.toLocaleString("zh-CN", { hour12: false });
      } catch {
        return String(value);
      }
    },

    incidentStatusLabel(status) {
      const map = {
        open: "未处理",
        diagnosing: "分析中",
        notified: "已通知",
        resolved: "已解决",
      };
      return map[status] || status;
    },

    incidentStatusClass(status) {
      if (status === "resolved") return "status-ok";
      if (status === "notified" || status === "diagnosing") return "status-unknown";
      return "status-bad";
    },

    async selectService(item) {
      this.activeService = item.service.id;
      await this.setActiveService();
    },

    isScanningHost(hostId) {
      return this.scanning && this.scanningHostId === hostId;
    },

    showProgressBanner() {
      return this.isScanningHost(this.activeHost) || this.inspecting;
    },

    progressMessage() {
      if (this.inspecting) return this.inspectingMessage || "正在巡检…";
      return this.scanningMessage || "正在扫描服务…";
    },

    progressBannerClass() {
      return this.inspecting ? "scan-banner inspect-banner" : "scan-banner";
    },

    isBusy() {
      return this.scanning || this.inspecting;
    },

    runningBadge(running) {
      return running ? "status-ok" : "status-bad";
    },

    runningLabel(running) {
      return running ? "运行中" : "已停止";
    },

    healthBadge(healthOk) {
      if (healthOk === true) return "status-ok";
      if (healthOk === false) return "status-bad";
      return "status-unknown";
    },

    healthLabel(healthOk) {
      if (healthOk === true) return "正常";
      if (healthOk === false) return "异常";
      return "未检测";
    },

    cardStatusClass(item) {
      if (item.status.running) return "card-up";
      return "card-down";
    },

    escapeHtml(text) {
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    },

    renderInlineMarkdown(text) {
      return text
        .replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    },

    parseTableRow(line) {
      return line
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => cell.trim());
    },

    buildTable(headers, rows) {
      const th = headers.map((h) => `<th>${this.renderInlineMarkdown(h)}</th>`).join("");
      const trs = rows
        .map((row) => `<tr>${row.map((c) => `<td>${this.renderInlineMarkdown(c)}</td>`).join("")}</tr>`)
        .join("");
      return `<div class="md-table-wrap"><table class="md-table"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`;
    },

    renderMarkdown(text) {
      const src = String(text || "");
      const placeholders = [];
      let processed = this.escapeHtml(src);

      processed = processed.replace(/```(\w*)\r?\n?([\s\S]*?)```/g, (_, lang, code) => {
        const id = placeholders.length;
        const langLabel = lang ? `<span class="md-code-lang">${lang}</span>` : "";
        placeholders.push(
          `<div class="md-codeblock">${langLabel}<pre><code>${code.trim()}</code></pre></div>`
        );
        return `\x00BLOCK${id}\x00`;
      });

      const lines = processed.split(/\r?\n/);
      const blocks = [];
      let paraBuf = [];
      let listBuf = null;
      let listItems = [];

      const flushParagraph = () => {
        if (!paraBuf.length) return;
        blocks.push(`<p class="md-p">${this.renderInlineMarkdown(paraBuf.join("<br>"))}</p>`);
        paraBuf = [];
      };

      const flushList = () => {
        if (!listItems.length) return;
        const tag = listBuf || "ul";
        blocks.push(
          `<${tag} class="md-list">${listItems
            .map((item) => `<li>${this.renderInlineMarkdown(item)}</li>`)
            .join("")}</${tag}>`
        );
        listItems = [];
        listBuf = null;
      };

      const isTableRow = (line) => /^\s*\|.+\|\s*$/.test(line);
      const isTableSep = (line) => /^\s*\|?[\s|:-]+\|[\s|:-]+\|?\s*$/.test(line);

      let i = 0;
      while (i < lines.length) {
        const line = lines[i];
        const trimmed = line.trim();

        if (!trimmed) {
          flushList();
          flushParagraph();
          i += 1;
          continue;
        }

        if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
          flushList();
          flushParagraph();
          const headers = this.parseTableRow(line);
          i += 2;
          const rows = [];
          while (i < lines.length && isTableRow(lines[i])) {
            rows.push(this.parseTableRow(lines[i]));
            i += 1;
          }
          blocks.push(this.buildTable(headers, rows));
          continue;
        }

        const h3 = trimmed.match(/^###\s+(.+)$/);
        if (h3) {
          flushList();
          flushParagraph();
          blocks.push(`<h3 class="md-h3">${this.renderInlineMarkdown(h3[1])}</h3>`);
          i += 1;
          continue;
        }

        const h2 = trimmed.match(/^##\s+(.+)$/);
        if (h2) {
          flushList();
          flushParagraph();
          blocks.push(`<h2 class="md-h2">${this.renderInlineMarkdown(h2[1])}</h2>`);
          i += 1;
          continue;
        }

        const h1 = trimmed.match(/^#\s+(.+)$/);
        if (h1) {
          flushList();
          flushParagraph();
          blocks.push(`<h2 class="md-h1">${this.renderInlineMarkdown(h1[1])}</h2>`);
          i += 1;
          continue;
        }

        const quote = trimmed.match(/^>\s?(.*)$/);
        if (quote) {
          flushList();
          flushParagraph();
          blocks.push(`<blockquote class="md-quote">${this.renderInlineMarkdown(quote[1])}</blockquote>`);
          i += 1;
          continue;
        }

        if (/^---+$/.test(trimmed) || /^\*\*\*+$/.test(trimmed)) {
          flushList();
          flushParagraph();
          blocks.push('<hr class="md-hr" />');
          i += 1;
          continue;
        }

        const ul = trimmed.match(/^[-*]\s+(.+)$/);
        if (ul) {
          flushParagraph();
          if (listBuf && listBuf !== "ul") flushList();
          listBuf = "ul";
          listItems.push(ul[1]);
          i += 1;
          continue;
        }

        const ol = trimmed.match(/^\d+\.\s+(.+)$/);
        if (ol) {
          flushParagraph();
          if (listBuf && listBuf !== "ol") flushList();
          listBuf = "ol";
          listItems.push(ol[1]);
          i += 1;
          continue;
        }

        flushList();
        paraBuf.push(line);
        i += 1;
      }

      flushList();
      flushParagraph();

      let html = blocks.join("");
      html = html.replace(/\x00BLOCK(\d+)\x00/g, (_, id) => placeholders[Number(id)] || "");
      return html || '<p class="md-p muted">（空回复）</p>';
    },

    renderMessage(msg) {
      const text = msg.text || "";
      if (msg.role === "user") {
        return `<div class="md-user-text">${this.escapeHtml(text).replace(/\n/g, "<br>")}</div>`;
      }
      if (msg.streaming) {
        const parts = [];
        if (text) {
          const body = this.escapeHtml(text).replace(/\n/g, "<br>");
          parts.push(`<div class="md-streaming">${body}<span class="stream-cursor">▋</span></div>`);
        }
        if (msg.status || !text) {
          const status = this.escapeHtml(msg.status || "正在思考…");
          parts.push(
            `<div class="ai-thinking${text ? " ai-thinking-sub" : ""}">` +
              `<span class="spinner" aria-hidden="true"></span>` +
              `<span class="ai-thinking-text">${status}</span>` +
            `</div>`
          );
        }
        return parts.join("");
      }
      return this.renderMarkdown(text);
    },

    scrollChatToBottom() {
      requestAnimationFrame(() => {
        const el = document.getElementById("chat-messages");
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    hostServices() {
      if (!this.activeHost) return this.allServices;
      return this.allServices.filter((svc) => svc.host_id === this.activeHost);
    },

    hostSummary() {
      const services = this.hostServices();
      const summaryForHost = this.summary.filter(
        (item) => !this.activeHost || item.service.host_id === this.activeHost
      );
      const summaryMap = new Map(summaryForHost.map((item) => [item.service.id, item]));
      if (!services.length) {
        return summaryForHost;
      }
      return services.map((service) => {
        const existing = summaryMap.get(service.id);
        if (existing) return existing;
        return {
          service,
          status: {
            service_id: service.id,
            running: null,
            detail: this.summaryLoading
              ? "状态检测中…"
              : this.summaryError || "状态未刷新，请点击「立即巡检」",
            health_ok: null,
            health_detail: "",
          },
        };
      });
    },

    hostIncidents() {
      if (!this.activeHost) return this.incidents;
      return this.incidents.filter((inc) => inc.host_id === this.activeHost);
    },

    goSetup() {
      window.location.assign("/setup");
    },

    async init() {
      try {
        const status = await this.api("/api/setup/status");
        this.setupCompleted = !!status.setup_completed;
      } catch (_) {
        this.setupCompleted = false;
      }
      await this.refresh({ skipSummary: true });
      this.refreshSummaryInBackground(this.activeHost);
      await this.loadSettingsForm();
      await this.loadChatWorkspace();
    },

    async loadChatWorkspace(conversationId = null) {
      const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
      const data = await this.api(`/api/chat/workspace${query}`);
      this.conversations = data.conversations || [];
      this.sessionId = data.conversation?.id || "";
      this.chatUsage = this.formatChatUsage(data.usage);
      this.messages = (data.messages || []).map((msg) => ({
        role: msg.role === "system" || msg.role === "tool" ? "assistant" : msg.role,
        text: msg.content || "",
        streaming: false,
      }));
      this.$nextTick(() => this.scrollChatToBottom());
    },

    formatChatUsage(usage) {
      if (!usage) return "";
      const icon = usage.level_icon || "";
      const hint = usage.hint ? ` · ${usage.hint}` : "";
      return `${icon} 上下文 ${usage.used_label || usage.used} / ${usage.limit_label || usage.limit} (${usage.percent || 0}%)${hint}`.trim();
    },

    async createConversation() {
      const data = await this.api("/api/chat/conversations", {
        method: "POST",
        body: JSON.stringify({ title: null }),
      });
      await this.loadChatWorkspace(data.conversation?.id);
    },

    async switchConversation(conversationId) {
      if (!conversationId || conversationId === this.sessionId) return;
      await this.loadChatWorkspace(conversationId);
    },

    async api(path, options = {}) {
      const { timeoutMs, ...fetchOptions } = options;
      const controller = timeoutMs ? new AbortController() : null;
      const timer =
        controller && timeoutMs
          ? setTimeout(() => controller.abort(), timeoutMs)
          : null;
      try {
        const res = await fetch(path, {
          headers: { "Content-Type": "application/json", ...(fetchOptions.headers || {}) },
          ...fetchOptions,
          signal: controller ? controller.signal : fetchOptions.signal,
        });
        if (!res.ok) {
          let detail = await res.text();
          try {
            const parsed = JSON.parse(detail);
            detail = parsed.detail || detail;
          } catch {
            /* keep raw text */
          }
          throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
        }
        return res.json();
      } catch (e) {
        if (e && e.name === "AbortError") {
          throw new Error("请求超时，请稍后重试");
        }
        throw e;
      } finally {
        if (timer) clearTimeout(timer);
      }
    },

    blankHostEditor() {
      const suffix = Date.now().toString(36).slice(-4);
      return {
        id: `host-${suffix}`,
        name: "新主机",
        ssh: { host: "", port: 22, user: "", key_file: "", password: "", use_sudo_su: false, sudo_password: "" },
        isNew: true,
      };
    },

    editHost(host) {
      this.hostEditor = {
        id: host.id,
        name: host.name,
        ssh: {
          host: host.ssh.host,
          port: host.ssh.port,
          user: host.ssh.user,
          key_file: host.ssh.key_file || "",
          password: "",
          use_sudo_su: !!host.ssh.use_sudo_su,
          sudo_password: "",
        },
        isNew: false,
      };
      this.hostEditorResult = "";
      this.settingsSshResult = "";
    },

    startAddHost() {
      this.hostEditor = this.blankHostEditor();
      this.hostEditorResult = "";
      this.settingsSshResult = "";
    },

    cancelHostEditor() {
      this.hostEditor = null;
      this.hostEditorResult = "";
      this.settingsSshResult = "";
    },

    buildHostPayload(editor) {
      const ssh = { ...editor.ssh };
      if (!ssh.password) ssh.password = UNCHANGED;
      if (!ssh.sudo_password) ssh.sudo_password = UNCHANGED;
      return {
        id: editor.id.trim(),
        name: editor.name.trim(),
        ssh,
      };
    },

    async saveHostEditor() {
      if (!this.hostEditor) return;
      const payload = this.buildHostPayload(this.hostEditor);
      if (!payload.id || !payload.name || !payload.ssh.host || !payload.ssh.user) {
        return alert("请填写主机 ID、名称、IP 和用户名");
      }
      const isNew = this.hostEditor.isNew;
      const hostId = payload.id;
      try {
        if (isNew) {
          await this.api("/api/hosts", { method: "POST", body: JSON.stringify(payload) });
        } else {
          await this.api(`/api/hosts/${encodeURIComponent(this.hostEditor.id)}`, {
            method: "PUT",
            body: JSON.stringify(payload),
          });
        }
        this.hostEditor = null;
        if (isNew) {
          this.showSettings = false;
        }
        await this.refresh();
        if (isNew) {
          await this.api(`/api/hosts/active?host_id=${encodeURIComponent(hostId)}`, { method: "PUT" });
          this.activeHost = hostId;
          await this.scanAndRegisterHost(hostId, { auto: true });
        } else {
          alert("主机已保存");
        }
      } catch (e) {
        alert(`保存失败: ${e.message}`);
      }
    },

    async deleteHost(host) {
      if (!confirm(`确定删除主机「${host.name}」？关联服务需先解除绑定。`)) return;
      try {
        await this.api(`/api/hosts/${encodeURIComponent(host.id)}`, { method: "DELETE" });
        if (this.hostEditor && this.hostEditor.id === host.id) this.hostEditor = null;
        await this.refresh();
        alert("主机已删除");
      } catch (e) {
        alert(`删除失败: ${e.message}`);
      }
    },

    async testHostEditorSsh() {
      if (!this.hostEditor) return;
      this.settingsSshResult = "测试中...";
      const ssh = { ...this.hostEditor.ssh };
      if (!ssh.password) ssh.password = UNCHANGED;
      if (!ssh.sudo_password) ssh.sudo_password = UNCHANGED;
      try {
        if (this.hostEditor.isNew) {
          const result = await this.api("/api/setup/test-ssh", {
            method: "POST",
            body: JSON.stringify({ host: ssh }),
          });
          this.settingsSshResult = result.success ? `成功\n${result.stdout}` : `失败\n${result.stderr}`;
        } else {
          await this.saveHostEditorSilent();
          const result = await this.api(`/api/hosts/${encodeURIComponent(this.hostEditor.id)}/test-ssh`, {
            method: "POST",
          });
          this.settingsSshResult = result.success ? `成功\n${result.stdout}` : `失败\n${result.stderr}`;
        }
      } catch (e) {
        this.settingsSshResult = `失败: ${e.message}`;
      }
    },

    async saveHostEditorSilent() {
      if (!this.hostEditor || this.hostEditor.isNew) return;
      const payload = this.buildHostPayload(this.hostEditor);
      await this.api(`/api/hosts/${encodeURIComponent(this.hostEditor.id)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
    },

    async loadSettingsForm() {
      const data = await this.api("/api/setup/form");
      this.settingsForm.llm = { ...data.llm, api_key: "" };
      this.settingsForm.feishu = {
        ...data.feishu,
        app_secret: "",
        bot: {
          command_enabled: false,
          command_chat_id: "",
          require_at_mention: true,
          ...(data.feishu?.bot || {}),
        },
      };
      await this.loadKnowledge();
      try {
        const memorySettings = await this.api("/api/chat/memory-settings");
        this.memoryAutoExtract = !!memorySettings.auto_extract;
      } catch {
        this.memoryAutoExtract = true;
      }
    },

    async loadKnowledge() {
      this.knowledgeEntries = await this.api("/api/chat/knowledge");
    },

    async saveMemoryAutoExtract() {
      await this.api("/api/chat/memory-settings", {
        method: "PUT",
        body: JSON.stringify({ auto_extract: this.memoryAutoExtract }),
      });
    },

    async addKnowledgeEntry() {
      const draft = this.memoryDraft;
      if (!draft.key.trim() || !draft.value.trim()) {
        alert("请填写键和值");
        return;
      }
      await this.api("/api/chat/knowledge", {
        method: "POST",
        body: JSON.stringify({
          category: draft.category,
          key: draft.key.trim(),
          value: draft.value.trim(),
        }),
      });
      this.memoryDraft.key = "";
      this.memoryDraft.value = "";
      await this.loadKnowledge();
    },

    async deleteKnowledgeEntry(entryId) {
      if (!confirm("删除这条记忆？")) return;
      await this.api(`/api/chat/knowledge/${encodeURIComponent(entryId)}`, { method: "DELETE" });
      await this.loadKnowledge();
    },

    async confirmMemory() {
      if (!this.pendingMemory) return;
      const mem = this.pendingMemory;
      await this.api("/api/chat/knowledge/confirm", {
        method: "POST",
        body: JSON.stringify({
          session_id: this.sessionId,
          category: mem.category,
          key: mem.key,
          value: mem.value,
        }),
      });
      this.messages.push({ role: "system", text: "已记住该条信息" });
      this.pendingMemory = null;
      this.scrollChatToBottom();
    },

    buildSettingsPayload() {
      const payload = {
        host: this.buildSettingsHostFallback(),
        llm: { ...this.settingsForm.llm },
        feishu: { ...this.settingsForm.feishu },
        complete: true,
      };
      if (!payload.llm.api_key) payload.llm.api_key = UNCHANGED;
      if (!payload.host.ssh.password) payload.host.ssh.password = UNCHANGED;
      if (!payload.feishu.app_secret) payload.feishu.app_secret = UNCHANGED;
      return payload;
    },

    buildSettingsHostFallback() {
      const host = this.hosts.find((h) => h.id === this.activeHost) || this.hosts[0];
      if (host) {
        return {
          id: host.id,
          name: host.name,
          ssh: {
            host: host.ssh.host,
            port: host.ssh.port,
            user: host.ssh.user,
            key_file: host.ssh.key_file || "",
            password: UNCHANGED,
          },
        };
      }
      return {
        id: "prod-01",
        name: "生产服务器",
        ssh: { host: "", port: 22, user: "", key_file: "", password: UNCHANGED },
      };
    },

    async saveSettings() {
      try {
        await this.api("/api/setup/save", {
          method: "PUT",
          body: JSON.stringify(this.buildSettingsPayload()),
        });
        this.showSettings = false;
        this.cancelHostEditor();
        await this.refresh();
        alert("设置已保存");
      } catch (e) {
        alert(`保存失败: ${e.message}`);
      }
    },

    async testFeishuSettings() {
      this.settingsFeishuResult = "测试中...";
      const feishu = { ...this.settingsForm.feishu };
      if (!feishu.app_secret) feishu.app_secret = UNCHANGED;
      const result = await this.api("/api/setup/test-feishu", {
        method: "POST",
        body: JSON.stringify(feishu),
      });
      this.settingsFeishuResult = result.success ? result.message : `失败: ${result.message}`;
    },

    async testLlmSettings() {
      this.settingsLlmResult = "测试中...";
      const llm = { ...this.settingsForm.llm };
      if (!llm.api_key) llm.api_key = UNCHANGED;
      const result = await this.api("/api/setup/test-llm", {
        method: "POST",
        body: JSON.stringify(llm),
      });
      this.settingsLlmResult = result.success ? `成功: ${result.response}` : `失败: ${result.response}`;
    },

    async refreshMetadata() {
      const hostData = await this.api("/api/hosts");
      this.hosts = hostData.hosts || [];
      this.activeHost = hostData.active_host_id || this.activeHost || (this.hosts[0] && this.hosts[0].id) || "";

      const svc = await this.api("/api/services");
      this.allServices = svc.services || [];
      this.activeService = svc.active_service_id || "";

      const visibleServices = this.hostServices();
      if (this.activeService && !visibleServices.some((s) => s.id === this.activeService)) {
        this.activeService = visibleServices[0] ? visibleServices[0].id : "";
        if (this.activeService) {
          await this.api(`/api/services/active?service_id=${encodeURIComponent(this.activeService)}`, {
            method: "PUT",
          });
        }
      }

      try {
        this.incidents = await this.api("/api/incidents");
      } catch {
        this.incidents = [];
      }
    },

    async refreshSummary(hostId = null) {
      const query = hostId ? `?host_id=${encodeURIComponent(hostId)}` : "";
      const count = hostId
        ? this.allServices.filter((svc) => svc.host_id === hostId).length
        : this.allServices.length;
      const timeoutMs = Math.max(300000, count * 12000);
      this.summaryLoading = true;
      this.summaryError = "";
      try {
        const partial = await this.api(`/api/status/summary${query}`, { timeoutMs });
        if (hostId) {
          const others = this.summary.filter((item) => item.service.host_id !== hostId);
          this.summary = [...others, ...partial];
        } else {
          this.summary = partial;
        }
      } catch (e) {
        this.summaryError = e.message || "状态刷新失败，请点击「立即巡检」重试";
        if (!hostId) this.summary = [];
      } finally {
        this.summaryLoading = false;
      }
    },

    refreshSummaryInBackground(hostId) {
      if (!hostId) return;
      this.refreshSummary(hostId).catch((e) => {
        this.summaryError = e.message || "后台状态刷新失败，请点击「立即巡检」重试";
        this.summaryLoading = false;
      });
    },

    async refresh(options = {}) {
      const { skipSummary = false, hostId = null } = options;
      await this.refreshMetadata();
      if (!skipSummary) {
        await this.refreshSummary(hostId);
      }
    },

    async setActiveHost() {
      if (!this.activeHost) return;
      const result = await this.api(`/api/hosts/active?host_id=${encodeURIComponent(this.activeHost)}`, {
        method: "PUT",
      });
      this.activeHost = result.active_host_id;
      this.activeService = result.active_service_id || "";
      const visibleServices = this.hostServices();
      if (!this.activeService && visibleServices.length) {
        this.activeService = visibleServices[0].id;
        await this.setActiveService();
      }
      this.refreshSummaryInBackground(this.activeHost);
    },

    async syncRuntime() {
      if (!this.activeService) return alert("请选择服务");
      try {
        const result = await this.api(`/api/services/${encodeURIComponent(this.activeService)}/sync-runtime`, {
          method: "POST",
        });
        await this.refresh();
        alert(result.updated ? `已补全: ${JSON.stringify(result.synced)}` : result.message);
      } catch (e) {
        alert(`同步失败: ${e.message}`);
      }
    },

    mapDiscoveredServices(discovered) {
      return discovered.map((d) => ({
        id: d.suggested_id,
        host_id: d.host_id,
        name: d.suggested_name,
        type: d.service_type,
        // 未运行的服务默认停用巡检，避免注册后立刻告警
        enabled: d.running !== false,
        jar_path: d.jar_path,
        deploy_dir: d.deploy_dir,
        systemd_unit: d.systemd_unit,
        container_name: d.container_name,
        compose_file: d.compose_file,
        compose_service: d.compose_service,
        health_url: d.health_url,
        log_path: d.log_path,
        listen_ports: d.listen_ports || [],
        config_files: d.config_files || [],
        active_profile: d.spring_profile,
      }));
    },

    async scanAndRegisterHost(hostId, { auto = false } = {}) {
      if (this.isBusy()) return null;
      const host = this.hosts.find((h) => h.id === hostId);
      const hostLabel = host ? `${host.name} (${host.ssh.host})` : hostId;
      this.scanning = true;
      this.scanningHostId = hostId;
      this.scanningMessage = auto
        ? `正在扫描新主机 ${hostLabel} 上的服务…`
        : `正在扫描 ${hostLabel} 上的服务…`;
      try {
        const discovered = await this.api("/api/discovery/scan", {
          method: "POST",
          body: JSON.stringify({ host_id: hostId }),
        });
        if (!discovered.length) {
          this.scanningMessage = "扫描完成：未发现可注册服务";
          if (!auto) alert("未发现服务");
          return { registered: 0, discovered: [] };
        }
        this.scanningMessage = `扫描完成，发现 ${discovered.length} 个候选服务，正在注册…`;
        const selected = discovered.filter((d) => d.confidence >= 0.7);
        if (!selected.length) {
          this.scanningMessage = `扫描完成：发现 ${discovered.length} 个服务，但无高置信度结果`;
          if (!auto) alert("没有高置信度服务可注册");
          return { registered: 0, discovered };
        }
        await this.api("/api/discovery/register", {
          method: "POST",
          body: JSON.stringify({ services: this.mapDiscoveredServices(selected) }),
        });
        await this.refresh({ skipSummary: true });
        this.scanningMessage = `扫描完成，已注册 ${selected.length} 个服务`;
        if (!auto) alert(`已注册 ${selected.length} 个服务，正在后台检测状态…`);
        this.refreshSummaryInBackground(hostId);
        return { registered: selected.length, discovered: selected };
      } catch (e) {
        this.scanningMessage = `扫描失败: ${e.message}`;
        if (!auto) alert(`扫描失败: ${e.message}`);
        throw e;
      } finally {
        if (!this.scanningHostId) return;
        const scannedId = this.scanningHostId;
        setTimeout(async () => {
          if (this.scanningHostId === scannedId) {
            this.scanning = false;
            this.scanningHostId = "";
            this.scanningMessage = "";
          }
        }, auto ? 2500 : 800);
      }
    },

    async scanHost() {
      if (!this.activeHost) return alert("请选择主机");
      if (this.isBusy()) return;
      await this.scanAndRegisterHost(this.activeHost);
    },

    async setActiveService() {
      if (!this.activeService) return;
      await this.api(`/api/services/active?service_id=${encodeURIComponent(this.activeService)}`, {
        method: "PUT",
      });
    },

    async runInspection() {
      if (this.isBusy()) return;
      const count = this.hostSummary().length;
      const hostLabel = this.activeHostName() || "当前主机";
      this.inspecting = true;
      this.inspectingMessage =
        count > 0
          ? `正在巡检 ${hostLabel}，检查 ${count} 个服务状态与 ERROR 日志…`
          : `正在巡检 ${hostLabel}…`;
      try {
        const result = await this.api("/api/inspection/run", { method: "POST" });
        this.inspectingMessage = "巡检完成，正在刷新服务状态…";
        await this.refreshSummary(this.activeHost || null);
        const created = result.created || 0;
        this.inspectingMessage = `巡检完成：新建告警 ${created} 条 · 正常 ${this.okServiceCount()} · 异常 ${this.badServiceCount()}`;
      } catch (e) {
        this.inspectingMessage = `巡检失败: ${e.message}`;
      } finally {
        setTimeout(() => {
          this.inspecting = false;
          this.inspectingMessage = "";
        }, 3000);
      }
    },

    async analyze(incidentId) {
      this.view = "home";
      await this.streamChatMessage(
        `请分析 incident ${incidentId}`,
        `分析 incident ${incidentId}`
      );
    },

    async streamChatMessage(userText, displayText = null) {
      if (this.chatSending) return;
      this.messages.push({ role: "user", text: displayText || userText });
      this.scrollChatToBottom();
      this.chatSending = true;
      this.chatToolStatus = "";

      const assistantIdx = this.messages.length;
      this.messages.push({
        role: "assistant",
        text: "",
        streaming: true,
        status: "正在思考…",
      });
      this.scrollChatToBottom();

      const patchAssistant = (patch) => {
        this.messages[assistantIdx] = {
          role: "assistant",
          ...this.messages[assistantIdx],
          ...patch,
        };
      };

      const finishAssistant = (text, streaming = false) => {
        patchAssistant({ text, streaming, status: null });
      };

      try {
        const res = await fetch("/api/chat/stream", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: this.sessionId, message: userText }),
        });
        if (!res.ok) {
          let detail = await res.text();
          try {
            const parsed = JSON.parse(detail);
            detail = parsed.detail || detail;
          } catch {
            /* keep raw */
          }
          throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
        }
        if (!res.body) throw new Error("流式响应不可用");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split("\n\n");
          buffer = parts.pop() || "";
          for (const part of parts) {
            const line = part.trim();
            if (!line.startsWith("data:")) continue;
            let payload;
            try {
              payload = JSON.parse(line.slice(5).trim());
            } catch {
              continue;
            }
            const event = payload.event;
            const data = payload.data;
            if (event === "delta") {
              const cur = this.messages[assistantIdx];
              patchAssistant({
                text: (cur.text || "") + (data || ""),
                streaming: true,
                status: null,
              });
              this.scrollChatToBottom();
            } else if (event === "tool_start") {
              patchAssistant({ status: `正在调用工具: ${data}…` });
            } else if (event === "tool_end") {
              patchAssistant({ status: "正在整理回答…" });
            } else if (event === "history_reset") {
              const cur = this.messages[assistantIdx];
              const prefix = "（上下文已自动重置）\n\n";
              if (!(cur.text || "").startsWith(prefix)) {
                patchAssistant({
                  text: prefix + (cur.text || ""),
                  streaming: true,
                  status: null,
                });
              }
              patchAssistant({ status: data || "上下文已重置…" });
            } else if (event === "confirm_restart") {
              this.pendingRestart = data;
              finishAssistant(data.message || "请确认是否重启", false);
              return;
            } else if (event === "confirm_write") {
              this.pendingWrite = data;
              patchAssistant({ status: "等待你确认文件操作…" });
            } else if (event === "error") {
              finishAssistant(data || "对话处理失败", false);
              return;
            } else if (event === "usage") {
              this.chatUsage = this.formatChatUsage(data);
            } else if (event === "compaction") {
              this.messages.push({ role: "system", text: data });
              this.scrollChatToBottom();
            } else if (event === "memory") {
              const autoSaved = (data && data.auto_saved) || [];
              if (autoSaved.length) {
                this.messages.push({
                  role: "system",
                  text: `已自动记住 ${autoSaved.length} 条信息`,
                });
              }
              const suggestions = (data && data.memory_suggestions) || [];
              if (suggestions.length) {
                this.pendingMemory = suggestions[0];
              }
            } else if (event === "done") {
              const cur = this.messages[assistantIdx];
              finishAssistant(cur.text || "已完成查询。", false);
              return;
            }
          }
        }
        const cur = this.messages[assistantIdx];
        finishAssistant(cur.text || "已完成查询。", false);
      } catch (e) {
        finishAssistant(`请求失败: ${e.message}`, false);
      } finally {
        this.chatSending = false;
        this.chatToolStatus = "";
        this.scrollChatToBottom();
      }
    },

    async clearChat() {
      if (!confirm("清空对话？助手将忘记本轮上下文（不影响已注册服务）。")) return;
      try {
        await this.api("/api/chat/clear", {
          method: "POST",
          body: JSON.stringify({ session_id: this.sessionId }),
        });
        this.messages = [];
        this.pendingRestart = null;
        this.pendingWrite = null;
        const usage = await this.api(`/api/chat/conversations/${encodeURIComponent(this.sessionId)}/usage`);
        this.chatUsage = this.formatChatUsage(usage);
      } catch (e) {
        alert(`清空失败: ${e.message}`);
      }
    },

    async sendMessage() {
      const text = this.input.trim();
      if (!text || this.chatSending) return;
      this.input = "";
      await this.streamChatMessage(text);
    },

    async confirmRestart() {
      if (!this.pendingRestart) return;
      const result = await this.api("/api/chat/confirm-restart", {
        method: "POST",
        body: JSON.stringify({
          session_id: this.sessionId,
          service_id: this.pendingRestart.service_id,
        }),
      });
      this.messages.push({
        role: "assistant",
        text: result.success ? `重启成功: ${result.stdout}` : `重启失败: ${result.stderr}`,
      });
      this.scrollChatToBottom();
      this.pendingRestart = null;
      await this.refreshSummary(this.activeHost || null);
    },

    async syncPendingFileOp() {
      // 仅用于页面初次加载；日常对话依赖本轮 confirm_write 事件，避免把历史残留再次捞出
      if (this.pendingWrite) return;
      try {
        const data = await this.api(`/api/chat/pending-file-op?session_id=${encodeURIComponent(this.sessionId)}`);
        if (data && data.pending) {
          this.pendingWrite = data;
        }
      } catch {
        /* ignore */
      }
    },

    async cancelPendingWrite() {
      const pending = this.pendingWrite;
      this.pendingWrite = null;
      if (!pending) return;
      const opId = pending.op_id || pending.write_id;
      try {
        await this.api("/api/chat/cancel-pending-write", {
          method: "POST",
          body: JSON.stringify({
            session_id: this.sessionId,
            write_id: opId,
            op_id: opId,
          }),
        });
      } catch {
        /* ignore */
      }
    },

    async confirmWrite() {
      if (!this.pendingWrite) return;
      const opId = this.pendingWrite.op_id || this.pendingWrite.write_id;
      const result = await this.api("/api/chat/confirm-write", {
        method: "POST",
        body: JSON.stringify({
          session_id: this.sessionId,
          write_id: opId,
          op_id: opId,
        }),
      });
      const path = this.pendingWrite.path || "远程文件";
      const action = this.pendingWrite.action === "delete" ? "删除" : "写入";
      this.messages.push({
        role: "assistant",
        text: result.success
          ? `${action}成功: ${path}\n${result.stdout || ""}`
          : `${action}失败 (${path}): ${result.stderr || result.stdout || "未知错误"}`,
      });
      this.scrollChatToBottom();
      this.pendingWrite = null;
    },
  };
}
