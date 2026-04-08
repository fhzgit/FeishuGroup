"""
飞书群机器人入口文件
使用 WebSocket 长连接监听事件，无需公网地址
"""
import io
import logging
import os
import sys
import threading
import time

# Windows 下强制 stdout 使用 UTF-8，防止 GBK 编码错误
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImChatMemberBotAddedV1, P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
    CallBackToast,
)

import config
from handlers.chat_member_handler import do_p2_im_chat_member_bot_added_v1
from handlers.message_handler import do_p2_im_message_receive_v1
from handlers.card_handler import handle_card_action
from handlers.auto_dissolve import start_idle_checker

# ── 日志配置 ────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── 健康检测：记录最后一次收到业务事件的时间 ──────────────────
_last_event_time: float = time.time()
_NO_EVENT_THRESHOLD = 20 * 60  # 20 分钟无事件则自动重启


def _touch_event():
    global _last_event_time
    _last_event_time = time.time()


def _start_watchdog():
    """启动守护线程：超过阈值未收到事件时自动 execv 重启"""
    def _watchdog():
        while True:
            time.sleep(60)
            elapsed = time.time() - _last_event_time
            if elapsed > _NO_EVENT_THRESHOLD:
                logger.warning(
                    f"已 {elapsed/60:.0f} 分钟未收到业务事件，自动重启进程..."
                )
                os.execv(sys.executable, [sys.executable] + sys.argv)

    t = threading.Thread(target=_watchdog, daemon=True, name="watchdog")
    t.start()
    logger.info(f"健康检测已启动（{_NO_EVENT_THRESHOLD//60} 分钟无事件自动重启）")


def _start_env_watcher():
    """监控 .env 文件变更，修改后自动热重启"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        logger.warning(f".env 文件不存在，跳过热重启监控: {env_path}")
        return

    last_mtime = os.path.getmtime(env_path)

    def _watcher():
        nonlocal last_mtime
        while True:
            time.sleep(2)  # 每 2 秒检查一次
            try:
                current_mtime = os.path.getmtime(env_path)
                if current_mtime != last_mtime:
                    last_mtime = current_mtime
                    logger.info("检测到 .env 文件变更，2 秒后热重启...")
                    time.sleep(2)  # 等待写入完成
                    logger.info("正在热重启进程...")
                    os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as e:
                logger.error(f".env 监控异常: {e}")

    t = threading.Thread(target=_watcher, daemon=True, name="env-watcher")
    t.start()
    logger.info(f".env 热重启监控已启动: {env_path}")


# ── 卡片回调处理 ────────────────────────────────────────────
def do_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    """
    处理卡片交互事件（card.action.trigger）
    必须在 3 秒内返回响应，实际建群逻辑在异步线程中执行
    """
    try:
        _touch_event()
        action_value = data.event.action.value or {}
        operator_open_id = data.event.operator.open_id or ""
        context = data.event.context
        card_message_id = (context.open_message_id if context else "") or ""

        logger.debug(
            f"卡片回调: operator={operator_open_id}, "
            f"card_message_id={card_message_id}, value={action_value}"
        )

        response_body = handle_card_action(action_value, operator_open_id, card_message_id)

        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        toast.type = response_body.get("toast", {}).get("type", "info")
        toast.content = response_body.get("toast", {}).get("content", "处理中...")
        resp.toast = toast

        # 立刻更新卡片为「处理中」状态，防止按钮闪回
        processing_card = response_body.get("processing_card")
        if processing_card:
            from lark_oapi.event.callback.model.p2_card_action_trigger import CallBackCard
            card_resp = CallBackCard()
            card_resp.type = "raw"
            card_resp.data = processing_card
            resp.card = card_resp

        return resp

    except Exception as e:
        logger.exception(f"卡片回调处理异常: {e}")
        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        toast.type = "error"
        toast.content = "操作失败，请稍后重试"
        resp.toast = toast
        return resp


# ── 消息事件处理 ──────────────────────────────────────────
def _wrapped_message_handler(data: P2ImMessageReceiveV1) -> None:
    _touch_event()
    # 异步处理消息，避免阻塞飞书 SDK 的 WebSocket 线程导致重复下发（重试）
    threading.Thread(
        target=do_p2_im_message_receive_v1,
            args=(data,),
        daemon=True,
        name=f"MsgHandler-{data.header.event_id}"
    ).start()


def _wrapped_bot_added_handler(data: P2ImChatMemberBotAddedV1) -> None:
    _touch_event()
    threading.Thread(
        target=do_p2_im_chat_member_bot_added_v1,
        args=(data,),
        daemon=True,
        name=f"BotAdded-{data.header.event_id}"
    ).start()


def main():
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"\n{'='*50}\n配置错误，程序退出：\n{e}\n{'='*50}")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("飞书群机器人启动中...")
    logger.info(f"APP_ID: {config.APP_ID}")
    logger.info(f"白名单群: {config.ALLOWED_CHAT_IDS or '全部'}")
    for key, dept in config.DEPARTMENT_HANDLERS.items():
        logger.info(f"  部门[{dept['name']}]: {len(dept['ids'])} 名负责人")
    logger.info("=" * 50)

    # _start_watchdog()  # 注释掉以防止 20 分钟死锁重启
    _start_env_watcher()
    start_idle_checker()

    event_handler = (
        lark.EventDispatcherHandler.builder(
            encrypt_key=config.CARD_ENCRYPT_KEY,
            verification_token=config.CARD_VERIFICATION_TOKEN,
        )
        .register_p2_im_chat_member_bot_added_v1(_wrapped_bot_added_handler)
        .register_p2_im_message_receive_v1(_wrapped_message_handler)
        .register_p2_card_action_trigger(do_card_action_trigger)
        .build()
    )

    ws_client = lark.ws.Client(
        app_id=config.APP_ID,
        app_secret=config.APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG,  # 临时强制 DEBUG，查按钮事件
    )

    logger.info("WebSocket 长连接已启动，等待事件...")
    logger.info("按 Ctrl+C 停止程序")
    ws_client.start()


if __name__ == "__main__":
    main()
