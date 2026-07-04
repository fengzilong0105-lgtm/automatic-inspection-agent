const UNCHANGED = "__UNCHANGED__";

function setupWizard() {
  return {
    step: 1,
    alreadyCompleted: false,
    stepLabels: ["SSH 连接", "大模型", "飞书", "扫描服务"],
    testing: false,
    scanning: false,
    sshResult: "",
    llmResult: "",
    scanResult: "",
    discovered: [],
    feishuResult: "",
    form: {
      host: { id: "prod-01", name: "生产服务器", ssh: { host: "", port: 22, user: "", key_file: "", password: "", use_sudo_su: false, sudo_password: "" } },
      llm: { provider: "openai", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini", api_key: "", temperature: 0.2, ollama_base_url: "http://localhost:11434", api_key_masked: null },
      feishu: {
        enabled: false,
        app_id: "",
        app_secret: "",
        alert_chat_id: "",
        app_secret_masked: null,
        bot: { command_enabled: false, command_chat_id: "", require_at_mention: true },
      },
    },

    async init() {
      const status = await this.api("/api/setup/status");
      this.alreadyCompleted = !status.setup_needed && status.setup_completed;
      const data = await this.api("/api/setup/form");
      this.form.host.id = data.host.id;
      this.form.host.name = data.host.name;
      this.form.host.ssh.host = data.host.ssh.host;
      this.form.host.ssh.port = data.host.ssh.port;
      this.form.host.ssh.user = data.host.ssh.user;
      this.form.host.ssh.key_file = data.host.ssh.key_file || "";
      this.form.host.ssh.use_sudo_su = !!data.host.ssh.use_sudo_su;
      this.form.host.ssh.sudo_password = "";
      this.form.llm = { ...this.form.llm, ...data.llm, api_key: "" };
      this.form.feishu = {
        ...this.form.feishu,
        ...data.feishu,
        app_secret: "",
        bot: {
          command_enabled: false,
          command_chat_id: "",
          require_at_mention: true,
          ...(data.feishu?.bot || {}),
        },
      };
    },

    async api(path, options = {}) {
      const res = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },

    buildPayload(complete = false) {
      const payload = {
        host: this.form.host,
        llm: { ...this.form.llm },
        feishu: { ...this.form.feishu },
        complete,
      };
      if (!payload.llm.api_key) payload.llm.api_key = UNCHANGED;
      if (!payload.feishu.app_secret) payload.feishu.app_secret = UNCHANGED;
      if (!payload.host.ssh.password) payload.host.ssh.password = UNCHANGED;
      if (!payload.host.ssh.sudo_password) payload.host.ssh.sudo_password = UNCHANGED;
      return payload;
    },

    async saveConfig(complete = false) {
      await this.api("/api/setup/save", {
        method: "PUT",
        body: JSON.stringify(this.buildPayload(complete)),
      });
    },

    async testSsh() {
      this.testing = true;
      this.sshResult = "测试中...";
      try {
        await this.saveConfig(false);
        const body = { host: { ...this.form.host.ssh } };
        if (!body.host.password) body.host.password = UNCHANGED;
        if (!body.host.sudo_password) body.host.sudo_password = UNCHANGED;
        const result = await this.api("/api/setup/test-ssh", {
          method: "POST",
          body: JSON.stringify({ host: body.host }),
        });
        this.sshResult = result.success
          ? `连接成功\n${result.stdout}`
          : `连接失败 (${result.exit_code})\n${result.stderr || result.stdout}`;
      } catch (e) {
        this.sshResult = `错误: ${e.message}`;
      } finally {
        this.testing = false;
      }
    },

    async testLlm() {
      this.testing = true;
      this.llmResult = "测试中...";
      try {
        const llm = { ...this.form.llm };
        if (!llm.api_key) llm.api_key = UNCHANGED;
        const result = await this.api("/api/setup/test-llm", {
          method: "POST",
          body: JSON.stringify(llm),
        });
        this.llmResult = result.success ? `LLM 响应: ${result.response}` : `失败: ${result.response}`;
      } catch (e) {
        this.llmResult = `错误: ${e.message}`;
      } finally {
        this.testing = false;
      }
    },

    async testFeishu() {
      this.testing = true;
      this.feishuResult = "测试中...";
      try {
        await this.saveConfig(false);
        const feishu = { ...this.form.feishu };
        if (!feishu.app_secret) feishu.app_secret = UNCHANGED;
        const result = await this.api("/api/setup/test-feishu", {
          method: "POST",
          body: JSON.stringify(feishu),
        });
        this.feishuResult = result.success ? result.message : `失败: ${result.message}`;
      } catch (e) {
        this.feishuResult = `错误: ${e.message}`;
      } finally {
        this.testing = false;
      }
    },

    async saveAndNext(currentStep) {
      try {
        await this.saveConfig(false);
        this.step = currentStep + 1;
      } catch (e) {
        alert(`保存失败: ${e.message}`);
      }
    },

    async scanServices() {
      this.scanning = true;
      this.scanResult = "扫描中...";
      try {
        await this.saveConfig(false);
        const hostId = this.form.host.id;
        const items = await this.api("/api/discovery/scan", {
          method: "POST",
          body: JSON.stringify({ host_id: hostId }),
        });
        this.discovered = items.map((d) => ({ ...d, _selected: d.confidence >= 0.7 }));
        this.scanResult = items.length ? `发现 ${items.length} 个服务/组件` : "未发现服务，请检查 SSH 权限";
      } catch (e) {
        this.scanResult = `扫描失败: ${e.message}`;
      } finally {
        this.scanning = false;
      }
    },

    async finishSetup() {
      const selected = this.discovered.filter((d) => d._selected);
      if (selected.length) {
        await this.api("/api/discovery/register", {
          method: "POST",
          body: JSON.stringify({
            services: selected.map((d) => ({
              id: d.suggested_id,
              host_id: d.host_id,
              name: d.suggested_name,
              type: d.service_type,
              enabled: true,
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
            })),
          }),
        });
      }
      await this.api("/api/setup/complete", { method: "POST" });
      window.location.href = "/";
    },
  };
}
