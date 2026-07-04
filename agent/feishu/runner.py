from __future__ import annotations

import asyncio
import logging
import threading

from agent.settings import get_settings

logger = logging.getLogger(__name__)

_runner: FeishuBotRunner | None = None


class FeishuBotRunner:
    """Feishu WebSocket long-connection client.

    Agent / SSH tool calls are dispatched to BackgroundRuntime's asyncio loop
    (same as desktop/Web chat), not a separate loop.
    """

    def __init__(self) -> None:
        self._ws_thread: threading.Thread | None = None
        self._superseded = False
        self._bot_service = None

    @property
    def bot_service(self):
        if self._bot_service is None:
            from agent.feishu.bot_service import FeishuBotService

            self._bot_service = FeishuBotService(self)
        return self._bot_service

    def start_if_enabled(self) -> None:
        cfg = get_settings().config.feishu
        if not cfg.bot.command_enabled:
            logger.info("Feishu bot command channel disabled (未勾选「启用飞书 @机器人 指令」)")
            return
        if not cfg.app_id or not cfg.app_secret:
            logger.warning("Feishu bot enabled but app_id/app_secret missing")
            return
        command_chat = (cfg.bot.command_chat_id or cfg.alert_chat_id or "").strip()
        if not command_chat:
            logger.warning("Feishu bot enabled but command_chat_id/alert_chat_id missing")
            return
        self.start()

    def start(self) -> None:
        if self._ws_thread and self._ws_thread.is_alive():
            return
        self._ws_thread = threading.Thread(target=self._ws_main, name="feishu-bot-ws", daemon=True)
        self._ws_thread.start()
        logger.info("Feishu bot long connection starting (app_id=%s)", get_settings().config.feishu.app_id)

    def _dispatch_event(self, data: object) -> None:
        if self._superseded:
            return
        from agent.runtime.background import get_runtime

        runtime = get_runtime()
        loop = runtime._loop
        if loop is None or not loop.is_running():
            logger.warning("Background runtime not ready; drop Feishu message event")
            return
        asyncio.run_coroutine_threadsafe(self.bot_service.handle_event(data), loop)

    def _ws_main(self) -> None:
        ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(ws_loop)
        try:
            import lark_oapi as lark
            import lark_oapi.ws.client as lark_ws_client

            lark_ws_client.loop = ws_loop
        except ImportError as exc:
            logger.error(
                "lark-oapi not installed; run: pip install lark-oapi (%s)",
                exc,
            )
            ws_loop.close()
            return

        feishu = get_settings().config.feishu

        def on_message(data: lark.im.v1.P2ImMessageReceiveV1) -> None:
            self._dispatch_event(data)

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )
        cli = lark.ws.Client(
            feishu.app_id,
            feishu.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        try:
            cli.start()
        except Exception:
            logger.exception("Feishu WebSocket client stopped")
        finally:
            ws_loop.close()


def get_feishu_bot_runner() -> FeishuBotRunner:
    global _runner
    if _runner is None:
        _runner = FeishuBotRunner()
    return _runner


def start_feishu_bot_if_enabled() -> None:
    get_feishu_bot_runner().start_if_enabled()


def restart_feishu_bot_if_enabled() -> None:
    global _runner
    if _runner is not None:
        _runner._superseded = True
        _runner = None
    get_feishu_bot_runner().start_if_enabled()


def schedule_feishu_bot_restart() -> None:
    """Restart Feishu bot in a background thread (never block UI)."""

    def _worker() -> None:
        try:
            restart_feishu_bot_if_enabled()
        except Exception:
            logger.exception("Failed to restart Feishu bot after config save")

    threading.Thread(target=_worker, name="feishu-bot-restart", daemon=True).start()
