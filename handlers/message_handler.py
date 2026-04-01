"""
消息事件处理模块
处理话题群中新建话题的首条消息，回复 AI 解答卡片
"""
import json
import logging
import os
import threading
import time

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

import config
from services import card_builder, feishu_api, ai_service
from handlers.resolve_handler import handle_resolve

logger = logging.getLogger(__name__)

# ── 持久化去重缓存：防止进程重启后飞书重放历史事件导致重复处理 ──
_DEDUP_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".processed_msgs")
_DEDUP_TTL = 24 * 60 * 60  # 24小时内的消息 ID 不重复处理
_dedup_lock = threading.Lock()


def _load_dedup_cache() -> dict:
    """从文件加载去重缓存 {message_id: timestamp}"""
    try:
        if os.path.exists(_DEDUP_FILE):
            with open(_DEDUP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_dedup_cache(cache: dict) -> None:
    """保存去重缓存到文件"""
    try:
        with open(_DEDUP_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as e:
        logger.warning(f"去重缓存写入失败: {e}")


def _is_already_processed(message_id: str) -> bool:
    """检查 message_id 是否已处理过，同时完成注册和过期清理"""
    with _dedup_lock:
        now = time.time()
        cache = _load_dedup_cache()
        # 清理过期条目
        cache = {k: v for k, v in cache.items() if now - v < _DEDUP_TTL}
        if message_id in cache:
            return True
        cache[message_id] = now
        _save_dedup_cache(cache)
        return False


def _extract_text(content_str: str) -> str:
    """从消息内容 JSON 中提取纯文本"""
    try:
        content = json.loads(content_str)
        if "text" in content:
            return content["text"].strip()
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




def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    """
    消息事件主处理函数（由 WebSocket 事件分发器回调）

    触发条件：话题群中新建话题的首条消息（parent_id 为空）
    """
    try:
        message = data.event.message
        sender = data.event.sender

        # ── 过滤：只处理群消息 ──────────────────────────────
        if message.chat_type != "group":
            logger.debug(f"忽略非群消息: chat_type={message.chat_type}")
            return

        # ── 过滤：忽略机器人自身消息 ────────────────────────
        if sender.sender_type == "app":
            logger.debug("忽略机器人自身消息")
            return

        chat_id = message.chat_id
        message_id = message.message_id
        sender_open_id = sender.sender_id.open_id
        msg_type = message.message_type or "text"
        msg_text = _extract_text(message.content)

        # ── 持久化去重：防止重启后飞书重放历史事件 ─────────
        if _is_already_processed(message_id):
            logger.info(f"忽略已处理过的消息（去重）: message_id={message_id}")
            return

        # ── 服务群消息追踪（更新自动解散计时器）────────────
        from handlers.auto_dissolve import is_tracked_group, on_message_received
        if is_tracked_group(chat_id):
            on_message_received(chat_id)
            logger.debug(f"服务群消息已追踪: chat_id={chat_id}")

        # ── 归档触发检测（优先于所有过滤，任意群消息均可触发）──
        # 服务群中 @机器人 + 归档触发词 → 启动归档流程
        mentions = message.mentions or []
        has_resolve_keyword = any(kw in msg_text for kw in config.RESOLVE_KEYWORDS)
        if has_resolve_keyword and mentions:
            logger.info(f"检测到归档触发词: chat_id={chat_id}, sender={sender_open_id}")
            handle_resolve(chat_id, sender_open_id)
            return

        # ── 过滤：只处理话题首条消息（parent_id 为空）───────
        # 话题群中，话题的第一条消息 parent_id 为 None 或空字符串
        # 之后在该话题下的回复消息均有 parent_id
        if message.parent_id:
            logger.debug(f"忽略话题回复消息: parent_id={message.parent_id}")
            return

        logger.info(
            f"收到话题首条消息: chat_id={chat_id}, "
            f"message_id={message_id}, sender={sender_open_id}, "
            f"type={msg_type}, content={msg_text[:50]}"
        )

        # ── 白名单群过滤 ────────────────────────────────────
        if config.ALLOWED_CHAT_IDS and chat_id not in config.ALLOWED_CHAT_IDS:
            logger.info(f"非白名单群，跳过: chat_id={chat_id}")
            return

        # ── Step 1: 立刻发出「思考中」占位卡片 ───────────────
        loading_card = card_builder.build_loading_card()
        sent_msg_id = feishu_api.send_card_message(
            chat_id, loading_card, reply_to_message_id=message_id
        )
        if not sent_msg_id:
            logger.error("占位卡片发送失败")
            return
        logger.info(f"占位卡片已发送: sent_msg_id={sent_msg_id}")

        # ── Step 2: 调用 Aily 获取真实回答（可能耗时 5-15s）──
        ai_answer = ai_service.generate_answer(msg_text, msg_type)

        # ── Step 3: 用真实内容更新卡片 ────────────────────────
        card_json = card_builder.build_ai_reply_card(
            asker_open_id=sender_open_id,
            ai_answer=ai_answer,
            origin_message_id=message_id,
            origin_chat_id=chat_id,
            departments=config.DEPARTMENT_HANDLERS,
        )
        feishu_api.update_card_message(sent_msg_id, card_json)
        logger.info(f"AI 解答卡片已更新: sent_msg_id={sent_msg_id}")

    except Exception as e:
        logger.exception(f"处理消息事件时发生异常: {e}")
