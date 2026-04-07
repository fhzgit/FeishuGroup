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


def _extract_message_payload(content_str: str, msg_type: str) -> tuple[str, list[str], list[str]]:
    """
    从消息内容 JSON 中提取文本、图片 key 和文件 key。
    支持 text/post/image/file 类型。
    返回: (text, image_keys, file_keys)
    """
    text_parts: list[str] = []
    image_keys: list[str] = []
    file_keys: list[str] = []

    try:
        content = json.loads(content_str) if content_str else {}
    except Exception:
        fallback_text = "" if msg_type in ("image", "file") else (content_str or "").strip()
        return fallback_text, [], []

    if not isinstance(content, dict):
        fallback_text = "" if msg_type in ("image", "file") else (content_str or "").strip()
        return fallback_text, [], []

    # text 消息
    if isinstance(content.get("text"), str):
        text_parts.append(content["text"])

    # image 消息（顶层 image_key）
    top_image_key = content.get("image_key")
    if isinstance(top_image_key, str) and top_image_key:
        image_keys.append(top_image_key)

    # file 消息（顶层 file_key，包括 txt/doc/pdf 等）
    top_file_key = content.get("file_key")
    if isinstance(top_file_key, str) and top_file_key:
        file_keys.append(top_file_key)

    # post 富文本消息（支持 text/img/file 混合）
    rich_content = content.get("content")
    if isinstance(rich_content, list):
        for line in rich_content:
            if not isinstance(line, list):
                continue
            for seg in line:
                if not isinstance(seg, dict):
                    continue
                tag = seg.get("tag")
                if tag == "text":
                    seg_text = seg.get("text", "")
                    if isinstance(seg_text, str) and seg_text:
                        text_parts.append(seg_text)
                elif tag == "img":
                    image_key = seg.get("image_key", "")
                    if isinstance(image_key, str) and image_key:
                        image_keys.append(image_key)
                elif tag == "file":
                    file_key = seg.get("file_key", "")
                    if isinstance(file_key, str) and file_key:
                        file_keys.append(file_key)

    # 兼容某些结构可能直接给 image_keys 列表
    raw_image_keys = content.get("image_keys")
    if isinstance(raw_image_keys, list):
        for k in raw_image_keys:
            if isinstance(k, str) and k:
                image_keys.append(k)

    # 去重（保留顺序）
    def dedup(keys: list[str]) -> list[str]:
        seen: set[str] = set()
        result = []
        for k in keys:
            if k not in seen:
                result.append(k)
                seen.add(k)
        return result

    text = " ".join(text_parts).strip()
    return text, dedup(image_keys), dedup(file_keys)




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
        msg_text, image_keys, file_keys = _extract_message_payload(message.content, msg_type)

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
            f"type={msg_type}, images={len(image_keys)}, files={len(file_keys)}, content={msg_text[:50]}"
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
        ai_answer = ai_service.generate_answer(
            question=msg_text,
            msg_type=msg_type,
            message_id=message_id,
            image_keys=image_keys,
            file_keys=file_keys,
        )

        # ── Step 2.5: 记录话题信息到多维表格（状态:无操作自动归档）──
        stats_record_id = ""
        if config.BITABLE_APP_TOKEN and config.BITABLE_STATS_TABLE_ID:
            from datetime import datetime
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            question_for_stats = msg_text.strip()
            if image_keys:
                image_hint = f"[含图片 {len(image_keys)} 张]"
                question_for_stats = f"{question_for_stats} {image_hint}".strip() if question_for_stats else image_hint
            fields = {
                "话题消息ID": message_id,
                "提问内容": question_for_stats[:1000],
                "提问时间": now_str,
                "所在群ID": chat_id,
                "解决方式": "无操作自动归档",
                "AI回答内容": ai_answer[:2000],
                "记录时间": now_str,
            }
            stats_record_id = feishu_api.create_bitable_record(
                config.BITABLE_APP_TOKEN, config.BITABLE_STATS_TABLE_ID, fields
            ) or ""
            logger.info(f"多维表格统计已创建，record_id: {stats_record_id}")

        # ── Step 3: 用真实内容更新卡片 ────────────────────────
        card_json = card_builder.build_ai_reply_card(
            asker_open_id=sender_open_id,
            ai_answer=ai_answer,
            origin_message_id=message_id,
            origin_chat_id=chat_id,
            departments=config.DEPARTMENT_HANDLERS,
            stats_record_id=stats_record_id,
        )
        updated = feishu_api.update_card_message(sent_msg_id, card_json)
        if updated:
            logger.info(f"AI 解答卡片已更新: sent_msg_id={sent_msg_id}")
        else:
            logger.error(f"AI 解答卡片更新失败: sent_msg_id={sent_msg_id}")

    except Exception as e:
        logger.exception(f"处理消息事件时发生异常: {e}")
