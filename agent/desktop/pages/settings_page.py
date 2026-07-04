from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.config_mgr.setup import FeishuSetupPayload, LLMSetupPayload
from agent.desktop.async_call import AsyncCall
from agent.desktop.constants import UNCHANGED
from agent.desktop.widgets.card import Card
from agent.services.agent_service import AgentService


class SettingsPage(QWidget):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        grid = QGridLayout()
        grid.setSpacing(12)

        llm_card = Card()
        llm_title = QLabel("大模型")
        llm_title.setObjectName("sectionTitle")
        llm_form = QFormLayout()
        llm_form.setSpacing(10)
        llm_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.provider = QComboBox()
        self.provider.addItems(["openai", "ollama"])
        self.base_url = QLineEdit()
        self.model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ollama_url = QLineEdit("http://localhost:11434")
        llm_form.addRow("Provider", self.provider)
        llm_form.addRow("API Base URL", self.base_url)
        llm_form.addRow("模型", self.model)
        llm_form.addRow("API Key", self.api_key)
        llm_form.addRow("Ollama 地址", self.ollama_url)
        llm_card.content_layout.addWidget(llm_title)
        llm_card.content_layout.addLayout(llm_form)

        feishu_card = Card()
        feishu_title = QLabel("飞书告警")
        feishu_title.setObjectName("sectionTitle")
        feishu_form = QFormLayout()
        feishu_form.setSpacing(10)
        self.feishu_enabled = QCheckBox("启用飞书告警")
        self.app_id = QLineEdit()
        self.app_secret = QLineEdit()
        self.app_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.chat_id = QLineEdit()
        feishu_form.addRow("", self.feishu_enabled)
        feishu_form.addRow("App ID", self.app_id)
        feishu_form.addRow("App Secret", self.app_secret)
        feishu_form.addRow("Chat ID", self.chat_id)
        feishu_card.content_layout.addWidget(feishu_title)
        feishu_card.content_layout.addLayout(feishu_form)

        grid.addWidget(llm_card, 0, 0)
        grid.addWidget(feishu_card, 0, 1)
        outer.addLayout(grid)

        action_card = Card()
        btn_row = QHBoxLayout()
        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("primaryButton")
        test_llm_btn = QPushButton("测试 LLM")
        test_llm_btn.setObjectName("secondaryButton")
        test_feishu_btn = QPushButton("测试飞书")
        test_feishu_btn.setObjectName("secondaryButton")
        btn_row.addWidget(save_btn)
        btn_row.addWidget(test_llm_btn)
        btn_row.addWidget(test_feishu_btn)
        btn_row.addStretch()
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(72)
        action_card.content_layout.addLayout(btn_row)
        action_card.content_layout.addWidget(self.result)
        outer.addWidget(action_card)
        outer.addStretch()

        save_btn.clicked.connect(self.save_settings)
        test_llm_btn.clicked.connect(self.test_llm)
        test_feishu_btn.clicked.connect(self.test_feishu)

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(lambda r: self.result.setPlainText(str(r)))
        self._bridge.failed.connect(lambda msg: self.result.setPlainText(f"错误: {msg}"))

        self.load_form()

    def load_form(self) -> None:
        data = self.service.setup_form()
        llm = data.get("llm", {})
        feishu = data.get("feishu", {})
        idx = self.provider.findText(llm.get("provider", "openai"))
        if idx >= 0:
            self.provider.setCurrentIndex(idx)
        self.base_url.setText(llm.get("base_url", ""))
        self.model.setText(llm.get("model", ""))
        self.ollama_url.setText(llm.get("ollama_base_url", "http://localhost:11434"))
        self.feishu_enabled.setChecked(bool(feishu.get("enabled")))
        self.app_id.setText(feishu.get("app_id", ""))
        self.chat_id.setText(feishu.get("alert_chat_id", ""))

    def _llm_payload(self) -> LLMSetupPayload:
        return LLMSetupPayload(
            provider=self.provider.currentText(),
            base_url=self.base_url.text().strip(),
            model=self.model.text().strip(),
            api_key=self.api_key.text().strip() or UNCHANGED,
            ollama_base_url=self.ollama_url.text().strip(),
        )

    def _feishu_payload(self) -> FeishuSetupPayload:
        return FeishuSetupPayload(
            enabled=self.feishu_enabled.isChecked(),
            app_id=self.app_id.text().strip(),
            app_secret=self.app_secret.text().strip() or UNCHANGED,
            alert_chat_id=self.chat_id.text().strip(),
        )

    def save_settings(self) -> None:
        try:
            self.service.save_llm_feishu(self._llm_payload(), self._feishu_payload())
            self.result.setPlainText("设置已保存")
        except Exception as exc:
            self.result.setPlainText(f"保存失败: {exc}")

    def test_llm(self) -> None:
        self.result.setPlainText("测试中…")
        self._bridge.submit(self.service.test_llm(self._llm_payload()))

    def test_feishu(self) -> None:
        self.result.setPlainText("发送中…")
        self._bridge.submit(self.service.test_feishu(self._feishu_payload()))
