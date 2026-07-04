from __future__ import annotations

import logging
from typing import Any

from agent.brand import PRODUCT_NAME
from agent.feishu.client import FeishuAPIError, send_feishu_text
from agent.settings import get_settings

logger = logging.getLogger(__name__)


class FeishuNotifier:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.config = self.settings.config.feishu

    @property
    def enabled(self) -> bool:
        return (
            self.config.enabled
            and bool(self.config.app_id)
            and bool(self.config.app_secret)
            and bool(self.config.alert_chat_id)
        )

    async def send_text(self, text: str) -> None:
        if not self.enabled:
            logger.info("Feishu disabled, alert: %s", text[:200])
            return
        try:
            await send_feishu_text(
                app_id=self.config.app_id,
                app_secret=self.config.app_secret,
                chat_id=self.config.alert_chat_id,
                text=text,
            )
        except FeishuAPIError as exc:
            logger.error("Feishu send failed: %s", exc)
            raise

    async def send_incident_card(self, incident: Any) -> None:
        text = (
            f"【{PRODUCT_NAME}告警】[{incident.severity.value}] {incident.title}\n"
            f"服务: {incident.service_id}\n"
            f"主机: {incident.host_id}\n"
            f"摘要: {incident.summary}\n"
            f"Incident ID: {incident.id}"
        )
        await self.send_text(text)
