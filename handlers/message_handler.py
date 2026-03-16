"""
消息事件处理模块
处理 im.message.receive_v1 事件
"""
import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

import config
from services import card_builder, feishu_api

logger = logging.getLogger(__name__)


def _extract_text(content_str: str) -> str:
    """从消息内容 JSON 中提取纯文本"""
    try:
        content = json.loads(content_str)
        # 文本消息
        if "text" in content:
            return content["text"].strip()
        # 富文本消息
        if "content" in content:
            texts = []
            for line in content.get("content", []):
                for seg in line:
                    if seg.get("tag") == "text":
                        texts.append(seg.get("text", ""))
            return " ".join(texts).strip()
    except Exception:
        pass
    return content_str


def _is_question(text: str) -> bool:
    """判断消息是否为问题（根据配置策略）"""
    if config.MONITOR_MODE == "all":
        return True
    # keyword 模式：包含任一关键词即视为问题
    return any(kw in text for kw in config.QUESTION_KEYWORDS)


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    """
    消息事件主处理函数
    由 WebSocket 事件分发器回调
    """
    try:
        message = data.event.message
        sender = data.event.sender

        # 过滤条件：
        # 1. 只处理群消息（chat_type == "group"）
        # 2. 忽略机器人自身发送的消息
        if message.chat_type != "group":
            logger.debug(f"忽略非群消息: chat_type={message.chat_type}")
            return

        if sender.sender_type == "app":
            logger.debug("忽略机器人自身消息")
            return

        chat_id = message.chat_id
        message_id = message.message_id
        sender_open_id = sender.sender_id.open_id
        msg_text = _extract_text(message.content)

        logger.info(
            f"收到群消息: chat_id={chat_id}, "
            f"sender={sender_open_id}, content={msg_text[:50]}"
        )

        # 判断是否需要触发卡片
        if not _is_question(msg_text):
            logger.debug(f"消息不符合触发条件，跳过: {msg_text[:30]}")
            return

        # 问题预览（截取前200字）
        question_preview = msg_text[:200] + ("..." if len(msg_text) > 200 else "")

        # 构建卡片
        card_json = card_builder.build_question_card(
            asker_open_id=sender_open_id,
            handler_open_ids=config.HANDLER_OPEN_IDS,
            question_preview=question_preview,
            origin_message_id=message_id,
            chat_id=chat_id,
        )

        # 发送卡片到群
        sent_msg_id = feishu_api.send_card_message(chat_id, card_json)
        if sent_msg_id:
            logger.info(f"卡片消息已发送: sent_msg_id={sent_msg_id}")
        else:
            logger.error("卡片消息发送失败")

    except Exception as e:
        logger.exception(f"处理消息事件时发生异常: {e}")
