from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.config_mgr.setup import FeishuBotSetupPayload, FeishuSetupPayload, LLMSetupPayload
from agent.desktop.async_call import AsyncCall
from agent.desktop.constants import UNCHANGED
from agent.desktop.widgets.card import Card
from agent.desktop.widgets.form_rows import labeled_field_row, style_input
from agent.desktop.widgets.word_wrap_label import WordWrapLabel
from agent.services.agent_service import AgentService


class SettingsPage(QWidget):
    def __init__(self, service: AgentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        scroll.setWidget(body)

        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        llm_card = Card()
        llm_title = QLabel("大模型")
        llm_title.setObjectName("sectionTitle")
        self.provider = QComboBox()
        self.provider.addItems(["openai", "ollama"])
        self.base_url = QLineEdit()
        self.model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ollama_url = QLineEdit("http://localhost:11434")
        for field in (self.base_url, self.model, self.api_key, self.ollama_url):
            style_input(field)
        style_input(self.provider)

        llm_box = QVBoxLayout()
        llm_box.setSpacing(12)
        llm_box.setContentsMargins(0, 0, 0, 0)
        llm_box.addLayout(labeled_field_row("Provider", self.provider))
        llm_box.addLayout(labeled_field_row("API Base URL", self.base_url))
        llm_box.addLayout(labeled_field_row("模型", self.model))
        llm_box.addLayout(labeled_field_row("API Key", self.api_key))
        llm_box.addLayout(labeled_field_row("Ollama 地址", self.ollama_url))

        llm_card.content_layout.setSpacing(12)
        llm_card.content_layout.addWidget(llm_title)
        llm_card.content_layout.addLayout(llm_box)
        llm_card.content_layout.addStretch(1)

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
        self.result.setMinimumHeight(72)
        self.result.setMaximumHeight(96)
        llm_card.content_layout.addLayout(btn_row)
        llm_card.content_layout.addWidget(self.result)
        llm_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        feishu_card = Card()
        feishu_title = QLabel("飞书")
        feishu_title.setObjectName("sectionTitle")

        self.feishu_enabled = QCheckBox("启用飞书告警")
        self.app_id = QLineEdit()
        self.app_secret = QLineEdit()
        self.app_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.chat_id = QLineEdit()
        self.bot_command_enabled = QCheckBox("启用飞书 @机器人 指令")
        self.bot_command_chat_id = QLineEdit()
        self.bot_command_chat_id.setPlaceholderText("留空则与告警 Chat ID 相同")
        self.bot_require_at_mention = QCheckBox("仅 @机器人 时响应")
        for field in (self.app_id, self.app_secret, self.chat_id, self.bot_command_chat_id):
            style_input(field)

        feishu_basic_box = QVBoxLayout()
        feishu_basic_box.setSpacing(12)
        feishu_basic_box.setContentsMargins(0, 0, 0, 0)
        feishu_basic_box.addWidget(self.feishu_enabled)
        feishu_basic_box.addLayout(labeled_field_row("App ID", self.app_id))
        feishu_basic_box.addLayout(labeled_field_row("App Secret", self.app_secret))
        feishu_basic_box.addLayout(labeled_field_row("告警 Chat ID", self.chat_id))

        bot_hint = WordWrapLabel(
            "群内 @机器人 只读指令（需在开放平台配置长连接）。"
            "与「启用飞书告警」独立，可不勾选告警仅开指令。"
        )
        bot_hint.setObjectName("mutedText")
        self._bot_hint = bot_hint

        feishu_bot_box = QVBoxLayout()
        feishu_bot_box.setSpacing(12)
        feishu_bot_box.setContentsMargins(0, 0, 0, 0)
        feishu_bot_box.addWidget(self.bot_command_enabled)
        feishu_bot_box.addLayout(labeled_field_row("指令群 Chat ID", self.bot_command_chat_id))
        feishu_bot_box.addWidget(self.bot_require_at_mention)

        feishu_card.content_layout.setSpacing(12)
        feishu_card.content_layout.addWidget(feishu_title)
        feishu_card.content_layout.addLayout(feishu_basic_box)
        feishu_card.content_layout.addSpacing(6)
        feishu_card.content_layout.addWidget(bot_hint)
        feishu_card.content_layout.addSpacing(10)
        feishu_card.content_layout.addLayout(feishu_bot_box)
        feishu_card.content_layout.addStretch(1)
        feishu_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        llm_wrap = QWidget()
        llm_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        llm_wrap_layout = QVBoxLayout(llm_wrap)
        llm_wrap_layout.setContentsMargins(0, 0, 0, 0)
        llm_wrap_layout.setSpacing(0)
        llm_wrap_layout.addWidget(llm_card, 1)

        feishu_wrap = QWidget()
        feishu_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        feishu_wrap_layout = QVBoxLayout(feishu_wrap)
        feishu_wrap_layout.setContentsMargins(0, 0, 0, 0)
        feishu_wrap_layout.setSpacing(0)
        feishu_wrap_layout.addWidget(feishu_card, 1)

        grid.addWidget(llm_wrap, 0, 0)
        grid.addWidget(feishu_wrap, 0, 1)
        grid.setRowStretch(0, 1)
        outer.addLayout(grid)

        memory_card = Card()
        memory_title = QLabel("AI 记忆")
        memory_title.setObjectName("sectionTitle")
        self.auto_extract = QCheckBox("自动提取记忆（每轮对话结束后）")
        memory_toolbar = QHBoxLayout()
        self.memory_refresh_btn = QPushButton("刷新")
        self.memory_refresh_btn.setObjectName("secondaryButton")
        self.memory_add_btn = QPushButton("手动添加")
        self.memory_add_btn.setObjectName("secondaryButton")
        self.memory_delete_btn = QPushButton("删除选中")
        self.memory_delete_btn.setObjectName("secondaryButton")
        memory_toolbar.addWidget(self.auto_extract)
        memory_toolbar.addStretch()
        memory_toolbar.addWidget(self.memory_refresh_btn)
        memory_toolbar.addWidget(self.memory_add_btn)
        memory_toolbar.addWidget(self.memory_delete_btn)

        self.memory_table = QTableWidget(0, 5)
        self.memory_table.setHorizontalHeaderLabels(["分类", "键", "值", "来源对话", "更新时间"])
        self.memory_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.memory_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.memory_table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)

        memory_form = QFormLayout()
        memory_form.setSpacing(8)
        self.memory_category = QComboBox()
        self.memory_category.addItems(["preference", "service_fact", "ops_note"])
        self.memory_key = QLineEdit()
        self.memory_value = QLineEdit()
        memory_form.addRow("分类", self.memory_category)
        memory_form.addRow("键", self.memory_key)
        memory_form.addRow("值", self.memory_value)

        memory_card.content_layout.addWidget(memory_title)
        memory_card.content_layout.addLayout(memory_toolbar)
        memory_card.content_layout.addWidget(self.memory_table)
        memory_card.content_layout.addLayout(memory_form)
        outer.addWidget(memory_card)

        save_btn.clicked.connect(self.save_settings)
        test_llm_btn.clicked.connect(self.test_llm)
        test_feishu_btn.clicked.connect(self.test_feishu)
        self.memory_refresh_btn.clicked.connect(self.load_memory)
        self.memory_add_btn.clicked.connect(self.add_memory)
        self.memory_delete_btn.clicked.connect(self.delete_memory)
        self.auto_extract.toggled.connect(self._on_auto_extract_changed)

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._on_async_finished)
        self._bridge.failed.connect(lambda msg: self.result.setPlainText(f"错误: {msg}"))

        self._memory_bridge = AsyncCall(self)
        self._memory_bridge.finished.connect(self._on_memory_async_finished)
        self._memory_bridge.failed.connect(lambda msg: self.result.setPlainText(f"记忆操作失败: {msg}"))

        self.load_form()
        self.load_memory()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._bot_hint._sync_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._bot_hint._sync_height()

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
        bot = feishu.get("bot") or {}
        self.bot_command_enabled.setChecked(bool(bot.get("command_enabled")))
        self.bot_command_chat_id.setText(bot.get("command_chat_id", ""))
        self.bot_require_at_mention.setChecked(
            bot.get("require_at_mention", True) if bot else True
        )
        try:
            memory_settings = self.service.get_memory_settings()
            self.auto_extract.blockSignals(True)
            self.auto_extract.setChecked(bool(memory_settings.get("auto_extract", True)))
            self.auto_extract.blockSignals(False)
        except Exception:
            self.auto_extract.setChecked(True)

    def _on_async_finished(self, result) -> None:
        self.result.setPlainText(str(result))

    def _on_memory_async_finished(self, result) -> None:
        if isinstance(result, list):
            self._render_memory_table(result)
            return
        if isinstance(result, dict) and result.get("deleted"):
            self.load_memory()
            self.result.setPlainText("记忆已删除")
            return
        if isinstance(result, dict) and result.get("id"):
            self.load_memory()
            self.result.setPlainText("记忆已保存")
            return
        self.result.setPlainText(str(result))

    def load_memory(self) -> None:
        self._memory_bridge.submit(self.service.list_knowledge())

    def _render_memory_table(self, entries: list[dict]) -> None:
        self.memory_table.setRowCount(0)
        for entry in entries:
            row = self.memory_table.rowCount()
            self.memory_table.insertRow(row)
            for col, key in enumerate(
                ["category", "key", "value", "source_conv_id", "updated_at"]
            ):
                item = QTableWidgetItem(str(entry.get(key) or ""))
                item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
                self.memory_table.setItem(row, col, item)

    def add_memory(self) -> None:
        category = self.memory_category.currentText()
        key = self.memory_key.text().strip()
        value = self.memory_value.text().strip()
        if not key or not value:
            self.result.setPlainText("请填写键和值")
            return
        self._memory_bridge.submit(self.service.create_knowledge(category, key, value))
        self.memory_key.clear()
        self.memory_value.clear()

    def delete_memory(self) -> None:
        row = self.memory_table.currentRow()
        if row < 0:
            self.result.setPlainText("请先选择一条记忆")
            return
        item = self.memory_table.item(row, 0)
        entry_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not entry_id:
            return
        self._memory_bridge.submit(self.service.delete_knowledge(entry_id))

    def _on_auto_extract_changed(self, checked: bool) -> None:
        self._memory_bridge.submit(self.service.save_memory_settings(checked))

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
            bot=FeishuBotSetupPayload(
                command_enabled=self.bot_command_enabled.isChecked(),
                command_chat_id=self.bot_command_chat_id.text().strip(),
                require_at_mention=self.bot_require_at_mention.isChecked(),
            ),
        )

    def save_settings(self) -> None:
        self.result.setPlainText("保存中…")
        self._bridge.submit(
            self.service.save_llm_feishu_async(self._llm_payload(), self._feishu_payload())
        )

    def test_llm(self) -> None:
        self.result.setPlainText("测试中…")
        self._bridge.submit(self.service.test_llm(self._llm_payload()))

    def test_feishu(self) -> None:
        self.result.setPlainText("发送中…")
        self._bridge.submit(self.service.test_feishu(self._feishu_payload()))
