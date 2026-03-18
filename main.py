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
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
    CallBackToast,
)

import config
from handlers.message_handler import do_p2_im_message_receive_v1
from handlers.card_handler import handle_card_action

# ── 日志配置 ────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ── 健康检测：记录最后一次收到业务事件的时间 ──────────────────
_last_event_time: float = time.time()
_NO_EVENT_THRESHOLD = 20 * 60  # 20 分钟无事件则自动重启


def _touch_event():
    """每次收到业务事件时调用，更新时间戳"""
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


# ── 卡片回调处理 ────────────────────────────────────────────
def do_card_action_trigger(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    """
    处理卡片交互事件（card.action.trigger）
    必须在 3 秒内返回响应，实际创群逻辑在异步线程中执行
    """
    try:
        _touch_event()  # 更新健康检测时间戳
        action_value = data.event.action.value or {}
        operator_open_id = data.event.operator.open_id or ""
        # context.open_message_id 是机器人发出的卡片消息 ID，用于后续 patch
        context = data.event.context
        card_message_id = (context.open_message_id if context else "") or ""
        logger.debug(f"卡片回调: operator={operator_open_id}, card_message_id={card_message_id}")
        response_body = handle_card_action(action_value, operator_open_id, card_message_id)

        resp = P2CardActionTriggerResponse()
        # 设置 toast 提示
        toast = CallBackToast()
        toast.type = response_body.get("toast", {}).get("type", "info")
        toast.content = response_body.get("toast", {}).get("content", "处理中...")
        resp.toast = toast

        # 如果有 processing_card，立刻更新卡片 UI（避免按钮闪回原始可点状态）
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


# ── 消息事件处理（包装，更新健康检测时间戳）──────────────────
def _wrapped_message_handler(data: P2ImMessageReceiveV1) -> None:
    _touch_event()
    do_p2_im_message_receive_v1(data)


def main():
    # 启动前校验配置
    try:
        config.validate()
    except ValueError as e:
        logger.error(f"\n{'='*50}\n配置错误，程序退出：\n{e}\n{'='*50}")
        sys.exit(1)

    logger.info("="*50)
    logger.info("飞书群机器人启动中...")
    logger.info(f"APP_ID: {config.APP_ID}")
    logger.info(f"监听模式: {config.MONITOR_MODE}")
    logger.info(f"负责人数量: {len(config.HANDLER_OPEN_IDS)} 人")
    logger.info(f"白名单群: {config.ALLOWED_CHAT_IDS or '全部'}")
    logger.info("="*50)

    # 启动健康检测守护线程
    _start_watchdog()

    # 构建事件分发器
    event_handler = (
        lark.EventDispatcherHandler.builder(
            encrypt_key=config.CARD_ENCRYPT_KEY,
            verification_token=config.CARD_VERIFICATION_TOKEN,
        )
        .register_p2_im_message_receive_v1(_wrapped_message_handler)
        .register_p2_card_action_trigger(do_card_action_trigger)
        .build()
    )

    # 启动 WebSocket 长连接客户端
    ws_client = lark.ws.Client(
        app_id=config.APP_ID,
        app_secret=config.APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.DEBUG if config.LOG_LEVEL == "DEBUG" else lark.LogLevel.INFO,
    )

    logger.info("WebSocket 长连接已启动，等待事件...")
    logger.info("按 Ctrl+C 停止程序")
    ws_client.start()


if __name__ == "__main__":
    main()
