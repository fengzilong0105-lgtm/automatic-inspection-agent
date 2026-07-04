from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

from agent.config_mgr.setup import (
    FeishuSetupPayload,
    HostSetupPayload,
    InlineSSHTestPayload,
    LLMSetupPayload,
    SetupSavePayload,
    SSHSetupPayload,
)
from agent.desktop.async_call import AsyncCall
from agent.desktop.constants import UNCHANGED
from agent.desktop.widgets.card import Card
from agent.models import ServiceConfig
from agent.brand import PRODUCT_NAME
from agent.desktop.assets import load_app_icon
from agent.services.agent_service import AgentService


def _style_form(form: QFormLayout) -> None:
    form.setSpacing(12)
    form.setVerticalSpacing(12)
    form.setHorizontalSpacing(16)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)


def _style_field(widget: QLineEdit | QSpinBox | QComboBox) -> None:
    widget.setMinimumHeight(36)


def _build_wizard_page(page: QWizardPage) -> QVBoxLayout:
    scroll = QScrollArea(page)
    scroll.setObjectName("wizardScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    body = QWidget()
    body.setObjectName("wizardPageBody")
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(24, 8, 24, 24)
    body_layout.setSpacing(12)
    scroll.setWidget(body)

    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.setSpacing(0)
    page_layout.addWidget(scroll)
    return body_layout


def _make_result_box(max_height: int = 120) -> QTextEdit:
    box = QTextEdit()
    box.setObjectName("wizardResultBox")
    box.setReadOnly(True)
    box.setMinimumHeight(72)
    box.setMaximumHeight(max_height)
    return box


class SSHPage(QWizardPage):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setTitle("SSH 连接")
        self.setSubTitle("填写 Linux 目标机的 SSH 信息。")

        layout = _build_wizard_page(self)

        form = QFormLayout()
        self.host_id = QLineEdit("prod-01")
        self.host_name = QLineEdit("生产服务器")
        self.ssh_host = QLineEdit()
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(22)
        self.ssh_user = QLineEdit()
        self.key_file = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.use_sudo = QCheckBox("需要 sudo su（读 root 目录时勾选）")
        self.sudo_password = QLineEdit()
        self.sudo_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.result = _make_result_box()

        for field in (
            self.host_id,
            self.host_name,
            self.ssh_host,
            self.ssh_port,
            self.ssh_user,
            self.key_file,
            self.password,
            self.sudo_password,
        ):
            _style_field(field)

        _style_form(form)
        form.addRow("主机 ID", self.host_id)
        form.addRow("显示名称", self.host_name)
        form.addRow("IP / 域名", self.ssh_host)
        form.addRow("端口", self.ssh_port)
        form.addRow("用户名", self.ssh_user)
        form.addRow("私钥路径（可选）", self.key_file)
        form.addRow("密码（可选）", self.password)
        form.addRow("", self.use_sudo)
        form.addRow("sudo 密码", self.sudo_password)

        form_card = Card(padding=20)
        form_card.content_layout.addLayout(form)

        test_btn = QPushButton("测试连接")
        test_btn.setObjectName("primaryButton")
        test_btn.clicked.connect(self.test_ssh)

        result_title = QLabel("测试结果")
        result_title.setObjectName("fieldLabel")

        layout.addWidget(form_card)
        layout.addWidget(test_btn)
        layout.addWidget(result_title)
        layout.addWidget(self.result)
        layout.addStretch()

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._on_test_done)
        self._bridge.failed.connect(lambda msg: self._show_result(f"错误: {msg}"))

    def load_form(self, data: dict) -> None:
        host = data.get("host", {})
        ssh = host.get("ssh", {})
        self.host_id.setText(host.get("id", "prod-01"))
        self.host_name.setText(host.get("name", "生产服务器"))
        self.ssh_host.setText(ssh.get("host", ""))
        self.ssh_port.setValue(int(ssh.get("port", 22)))
        self.ssh_user.setText(ssh.get("user", ""))
        self.key_file.setText(ssh.get("key_file", "") or "")
        self.use_sudo.setChecked(bool(ssh.get("use_sudo_su")))

    def build_host_payload(self) -> HostSetupPayload:
        pwd = self.password.text().strip() or UNCHANGED
        sudo = self.sudo_password.text().strip() or UNCHANGED
        return HostSetupPayload(
            id=self.host_id.text().strip() or "prod-01",
            name=self.host_name.text().strip() or "生产服务器",
            ssh=SSHSetupPayload(
                host=self.ssh_host.text().strip(),
                port=self.ssh_port.value(),
                user=self.ssh_user.text().strip(),
                key_file=self.key_file.text().strip() or None,
                password=pwd,
                use_sudo_su=self.use_sudo.isChecked(),
                sudo_password=sudo,
            ),
        )

    def test_ssh(self) -> None:
        self.result.setPlainText("测试中...")
        payload = InlineSSHTestPayload(host=self.build_host_payload().ssh)
        self._bridge.submit(self.service.test_ssh(payload))

    def _on_test_done(self, result: dict) -> None:
        if result.get("success"):
            self._show_result(f"连接成功\n{result.get('stdout', '')}")
        else:
            self._show_result(
                f"连接失败 ({result.get('exit_code')})\n{result.get('stderr') or result.get('stdout')}"
            )

    def _show_result(self, text: str) -> None:
        self.result.setPlainText(text)


class LLMPage(QWizardPage):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setTitle("大模型")
        self.setSubTitle("选择 Ollama 本地模型或 OpenAI 兼容 API。")

        layout = _build_wizard_page(self)

        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItems(["openai", "ollama"])
        self.base_url = QLineEdit("https://api.openai.com/v1")
        self.model = QLineEdit("gpt-4o-mini")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ollama_url = QLineEdit("http://localhost:11434")
        self.result = _make_result_box()

        for field in (self.provider, self.base_url, self.model, self.api_key, self.ollama_url):
            _style_field(field)

        self.provider.currentTextChanged.connect(self._sync_provider_fields)
        _style_form(form)
        form.addRow("Provider", self.provider)
        form.addRow("API Base URL", self.base_url)
        form.addRow("模型名", self.model)
        form.addRow("API Key", self.api_key)
        form.addRow("Ollama 地址", self.ollama_url)

        form_card = Card(padding=20)
        form_card.content_layout.addLayout(form)

        test_btn = QPushButton("测试 LLM")
        test_btn.setObjectName("primaryButton")
        test_btn.clicked.connect(self.test_llm)

        layout.addWidget(form_card)
        layout.addWidget(test_btn)
        layout.addWidget(self.result)
        layout.addStretch()

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._on_test_done)
        self._bridge.failed.connect(lambda msg: self.result.setPlainText(f"错误: {msg}"))

    def load_form(self, data: dict) -> None:
        llm = data.get("llm", {})
        idx = self.provider.findText(llm.get("provider", "openai"))
        if idx >= 0:
            self.provider.setCurrentIndex(idx)
        self.base_url.setText(llm.get("base_url", "https://api.openai.com/v1"))
        self.model.setText(llm.get("model", "gpt-4o-mini"))
        self.ollama_url.setText(llm.get("ollama_base_url", "http://localhost:11434"))
        self._sync_provider_fields(self.provider.currentText())

    def _sync_provider_fields(self, provider: str) -> None:
        is_ollama = provider == "ollama"
        self.base_url.setEnabled(not is_ollama)
        self.api_key.setEnabled(not is_ollama)

    def build_payload(self) -> LLMSetupPayload:
        return LLMSetupPayload(
            provider=self.provider.currentText(),
            base_url=self.base_url.text().strip(),
            model=self.model.text().strip(),
            api_key=self.api_key.text().strip() or UNCHANGED,
            ollama_base_url=self.ollama_url.text().strip(),
        )

    def test_llm(self) -> None:
        self.result.setPlainText("测试中...")
        self._bridge.submit(self.service.test_llm(self.build_payload()))

    def _on_test_done(self, result: dict) -> None:
        if result.get("success"):
            self.result.setPlainText(f"LLM 响应: {result.get('response')}")
        else:
            self.result.setPlainText(f"失败: {result.get('response')}")


class FeishuPage(QWizardPage):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setTitle("飞书告警（可选）")
        self.setSubTitle("可跳过；启用后告警会推送到飞书群。")

        layout = _build_wizard_page(self)

        form = QFormLayout()
        self.enabled = QCheckBox("启用飞书告警")
        self.app_id = QLineEdit()
        self.app_secret = QLineEdit()
        self.app_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.chat_id = QLineEdit()
        self.result = _make_result_box()

        for field in (self.app_id, self.app_secret, self.chat_id):
            _style_field(field)

        _style_form(form)
        form.addRow("", self.enabled)
        form.addRow("App ID", self.app_id)
        form.addRow("App Secret", self.app_secret)
        form.addRow("告警 Chat ID", self.chat_id)

        form_card = Card(padding=20)
        form_card.content_layout.addLayout(form)

        test_btn = QPushButton("发送测试消息")
        test_btn.setObjectName("primaryButton")
        test_btn.clicked.connect(self.test_feishu)

        layout.addWidget(form_card)
        layout.addWidget(test_btn)
        layout.addWidget(self.result)
        layout.addStretch()

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(
            lambda r: self.result.setPlainText(
                r.get("message", str(r)) if isinstance(r, dict) else str(r)
            )
        )
        self._bridge.failed.connect(lambda msg: self.result.setPlainText(f"错误: {msg}"))

    def load_form(self, data: dict) -> None:
        feishu = data.get("feishu", {})
        self.enabled.setChecked(bool(feishu.get("enabled")))
        self.app_id.setText(feishu.get("app_id", ""))
        self.chat_id.setText(feishu.get("alert_chat_id", ""))

    def build_payload(self) -> FeishuSetupPayload:
        return FeishuSetupPayload(
            enabled=self.enabled.isChecked(),
            app_id=self.app_id.text().strip(),
            app_secret=self.app_secret.text().strip() or UNCHANGED,
            alert_chat_id=self.chat_id.text().strip(),
        )

    def test_feishu(self) -> None:
        self.result.setPlainText("发送中...")
        self._bridge.submit(self.service.test_feishu(self.build_payload()))


class ScanPage(QWizardPage):
    def __init__(self, service: AgentService, wizard, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.wizard_ref = wizard
        self.setTitle("扫描并注册服务")
        self.setSubTitle("扫描 Linux 主机上的 Java / Docker / 中间件并注册。")

        layout = _build_wizard_page(self)

        self.scan_btn = QPushButton("扫描服务")
        self.scan_btn.setObjectName("primaryButton")
        self.scan_btn.clicked.connect(self.scan_services)
        self.result = _make_result_box(240)
        self.discovered: list[dict] = []

        layout.addWidget(self.scan_btn)
        layout.addWidget(self.result)
        layout.addStretch()

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._on_scan_done)
        self._bridge.failed.connect(lambda msg: self.result.setPlainText(f"扫描失败: {msg}"))

    def scan_services(self) -> None:
        try:
            self.wizard_ref.save_partial_config()
        except Exception as exc:
            QMessageBox.warning(self, "提示", str(exc))
            return
        host_id = self.wizard_ref.ssh_page.host_id.text().strip()
        if not host_id:
            QMessageBox.warning(self, "提示", "请先填写主机 ID")
            return
        self.result.setPlainText("扫描中，请稍候...")
        self._bridge.submit(self.service.scan_host(host_id))

    def _on_scan_done(self, items: list) -> None:
        self.discovered = items
        lines = [f"发现 {len(items)} 个服务："]
        for item in items:
            lines.append(
                f"- {item.get('suggested_id')} ({item.get('type')}) "
                f"confidence={item.get('confidence')}"
            )
        self.result.setPlainText("\n".join(lines))


class SetupWizard(QWizard):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(f"{PRODUCT_NAME} — 初始化向导")
        app_icon = load_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.setMinimumSize(760, 640)
        self.resize(800, 680)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)

        form = service.setup_form()
        self.ssh_page = SSHPage(service)
        self.llm_page = LLMPage(service)
        self.feishu_page = FeishuPage(service)
        self.scan_page = ScanPage(service, self)

        self.ssh_page.load_form(form)
        self.llm_page.load_form(form)
        self.feishu_page.load_form(form)

        self.addPage(self.ssh_page)
        self.addPage(self.llm_page)
        self.addPage(self.feishu_page)
        self.addPage(self.scan_page)

        self.setButtonText(QWizard.WizardButton.FinishButton, "完成并进入控制台")
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)

    def save_partial_config(self) -> None:
        payload = SetupSavePayload(
            host=self.ssh_page.build_host_payload(),
            llm=self.llm_page.build_payload(),
            feishu=self.feishu_page.build_payload(),
            complete=False,
        )
        self.service.save_setup(payload)

    def accept(self) -> None:
        try:
            self.save_partial_config()
            host_id = self.ssh_page.host_id.text().strip()
            if self.scan_page.discovered:
                services = self.service.discovered_to_services(host_id, self.scan_page.discovered)
                self.service.register_services(services)
            self.service.complete_setup()
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        super().accept()
