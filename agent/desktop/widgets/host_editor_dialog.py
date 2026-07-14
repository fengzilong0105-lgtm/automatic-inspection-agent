from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from agent.config_mgr.setup import HostSetupPayload, InlineSSHTestPayload, SSHSetupPayload
from agent.desktop.async_call import AsyncCall
from agent.desktop.constants import UNCHANGED
from agent.desktop.widgets.form_rows import style_input
from agent.desktop.widgets.word_wrap_label import WordWrapLabel
from agent.services.agent_service import AgentService


def _blank_host() -> dict:
    suffix = hex(int(time.time() * 1000))[-4:]
    return {
        "id": f"host-{suffix}",
        "name": "新服务器",
        "ssh": {
            "host": "",
            "port": 22,
            "user": "",
            "key_file": "",
            "use_sudo_su": False,
        },
    }


class HostEditorDialog(QDialog):
    def __init__(
        self,
        service: AgentService,
        *,
        host: dict | None = None,
        is_new: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self._is_new = is_new
        self._original_id = (host or {}).get("id", "")
        self._saved_host: dict | None = None
        self._pending_action = ""

        self.setWindowTitle("添加服务器" if is_new else "编辑服务器")
        self.setMinimumWidth(520)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        hint = WordWrapLabel(
            "各服务器 SSH 配置独立保存，可在顶部下拉切换当前主机。"
        )
        hint.setObjectName("mutedText")
        root.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(8)

        self.host_id = QLineEdit()
        self.host_name = QLineEdit()
        self.ssh_host = QLineEdit()
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(22)
        self.ssh_user = QLineEdit()
        self.key_file = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("留空保持不变" if not is_new else "不用密钥时填写")
        self.use_sudo = QCheckBox("登录后需 sudo su 提权至 root")
        self.sudo_password = QLineEdit()
        self.sudo_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.sudo_password.setPlaceholderText("留空则使用 SSH 密码")

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
            style_input(field)

        if not is_new:
            self.host_id.setReadOnly(True)

        form.addRow("主机 ID", self.host_id)
        form.addRow("显示名称", self.host_name)
        form.addRow("IP / 域名", self.ssh_host)
        form.addRow("端口", self.ssh_port)
        form.addRow("用户名", self.ssh_user)
        form.addRow("私钥路径", self.key_file)
        form.addRow("SSH 密码", self.password)
        form.addRow("", self.use_sudo)
        form.addRow("sudo 密码", self.sudo_password)
        root.addLayout(form)

        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(88)
        root.addWidget(self.result)

        actions = QHBoxLayout()
        test_btn = QPushButton("测试 SSH")
        test_btn.setObjectName("secondaryButton")
        test_btn.clicked.connect(self._test_ssh)
        actions.addWidget(test_btn)
        actions.addStretch()
        root.addLayout(actions)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._bridge = AsyncCall(self)
        self._bridge.finished.connect(self._on_async_done)
        self._bridge.failed.connect(self._on_async_failed)

        data = host or _blank_host()
        self._load_host(data)

    def saved_host(self) -> dict | None:
        return self._saved_host

    def _load_host(self, host: dict) -> None:
        ssh = host.get("ssh", {})
        self.host_id.setText(host.get("id", ""))
        self.host_name.setText(host.get("name", ""))
        self.ssh_host.setText(ssh.get("host", ""))
        self.ssh_port.setValue(int(ssh.get("port", 22)))
        self.ssh_user.setText(ssh.get("user", ""))
        self.key_file.setText(ssh.get("key_file", "") or "")
        self.use_sudo.setChecked(bool(ssh.get("use_sudo_su")))

    def _build_payload(self) -> HostSetupPayload:
        pwd = self.password.text().strip() or (UNCHANGED if not self._is_new else "")
        sudo = self.sudo_password.text().strip() or (UNCHANGED if not self._is_new else "")
        return HostSetupPayload(
            id=self.host_id.text().strip(),
            name=self.host_name.text().strip(),
            ssh=SSHSetupPayload(
                host=self.ssh_host.text().strip(),
                port=self.ssh_port.value(),
                user=self.ssh_user.text().strip(),
                key_file=self.key_file.text().strip() or None,
                password=pwd or None,
                use_sudo_su=self.use_sudo.isChecked(),
                sudo_password=sudo or None,
            ),
        )

    def _validate(self, payload: HostSetupPayload) -> str | None:
        if not payload.id:
            return "请填写主机 ID"
        if not payload.name:
            return "请填写显示名称"
        if not payload.ssh.host:
            return "请填写 IP / 域名"
        if not payload.ssh.user:
            return "请填写用户名"
        if self._is_new:
            hosts = self.service.list_hosts().get("hosts", [])
            if any(item.get("id") == payload.id for item in hosts):
                return f"主机 ID 已存在: {payload.id}"
        return None

    def _test_ssh(self) -> None:
        payload = self._build_payload()
        error = self._validate(payload)
        if error:
            self.result.setPlainText(error)
            return
        self.result.setPlainText("测试中…")
        self._pending_action = "test"
        self._bridge.submit(
            self.service.test_ssh(InlineSSHTestPayload(host=payload.ssh))
        )

    def _save(self) -> None:
        payload = self._build_payload()
        error = self._validate(payload)
        if error:
            QMessageBox.warning(self, "提示", error)
            return
        self.result.setPlainText("保存中…")
        self._pending_action = "save"
        host_id = None if self._is_new else self._original_id
        self._bridge.submit(self.service.upsert_host_config(payload, host_id=host_id))

    def _on_async_done(self, result) -> None:
        if getattr(self, "_pending_action", "") == "test":
            if isinstance(result, dict) and result.get("success"):
                self.result.setPlainText(f"连接成功\n{result.get('stdout', '')}")
            elif isinstance(result, dict):
                self.result.setPlainText(
                    f"连接失败 ({result.get('exit_code')})\n"
                    f"{result.get('stderr') or result.get('stdout')}"
                )
            else:
                self.result.setPlainText(str(result))
            return

        if isinstance(result, dict):
            self._saved_host = result
            self.accept()

    def _on_async_failed(self, msg: str) -> None:
        if getattr(self, "_pending_action", "") == "test":
            self.result.setPlainText(f"错误: {msg}")
        else:
            QMessageBox.critical(self, "保存失败", msg)
