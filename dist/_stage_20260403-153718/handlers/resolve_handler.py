"""
归档处理模块
处理「@机器人 问题已解决」→ 收集历史消息 → 写入多维表格 → 解散群
"""
import json
import logging
import threading
from datetime import datetime, timezone, timedelta

import config
from services import feishu_api

logger = logging.getLogger(__name__)

# 幂等锁：防止同一个群被重复归档
_archiving_chats: set[str] = set()
_lock = threading.Lock()

# 北京时间时区
_BJT = timezone(timedelta(hours=8))


def handle_resolve(chat_id: str, sender_open_id: str) -> None:
    """
    归档入口：在异步线程中执行归档流程
    """
    with _lock:
        if chat_id in _archiving_chats:
            logger.info(f"群 {chat_id} 正在归档中，忽略重复请求")
            return
        _archiving_chats.add(chat_id)

    threading.Thread(
        target=_async_archive,
        args=(chat_id, sender_open_id),
        daemon=True,
    ).start()


def _async_archive(chat_id: str, sender_open_id: str) -> None:
    """异步执行归档流程"""
    try:
        # 0. 发送提示
        feishu_api.send_text_message(chat_id, "📦 正在归档聊天记录，请稍候...")

        # 1. 获取群名称
        chat_name = feishu_api.get_chat_info(chat_id) or chat_id

        # 2. 获取全部历史消息
        messages = feishu_api.list_chat_messages(chat_id)
        if not messages:
            feishu_api.send_text_message(chat_id, "⚠️ 未获取到历史消息，归档取消")
            with _lock:
                _archiving_chats.discard(chat_id)
            return

        # 3. 拼接聊天记录文本
        chat_log = _format_chat_log(messages)
        now = datetime.now(_BJT)

        # 4. 写入多维表格（一群一行）
        if config.BITABLE_APP_TOKEN and config.BITABLE_TABLE_ID:
            fields = {
                "服务群名称": chat_name,
                "服务群ID": chat_id,
                "聊天记录": chat_log,
                "消息条数": str(len(messages)),
                "归档时间": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            success = feishu_api.create_bitable_record(
                config.BITABLE_APP_TOKEN,
                config.BITABLE_TABLE_ID,
                fields,
            )
            if not success:
                feishu_api.send_text_message(
                    chat_id, "❌ 写入多维表格失败，请联系管理员"
                )
                with _lock:
                    _archiving_chats.discard(chat_id)
                return
        else:
            logger.warning("BITABLE_APP_TOKEN 或 BITABLE_TABLE_ID 未配置，跳过写入")

        # 5. 发送归档完成通知
        feishu_api.send_text_message(
            chat_id,
            f"✅ 归档完成！共 {len(messages)} 条消息已写入多维表格。\n"
            f"本群即将解散，感谢配合 🙏"
        )

        # 6. 解散群聊
        import time
        time.sleep(3)  # 等 3 秒让用户看到通知
        deleted = feishu_api.delete_chat(chat_id)
        if not deleted:
            logger.error(f"解散群失败: chat_id={chat_id}")

        logger.info(f"归档流程完成: chat={chat_name}, messages={len(messages)}")

    except Exception as e:
        logger.exception(f"归档流程异常: {e}")
        try:
            feishu_api.send_text_message(chat_id, f"❌ 归档失败：{e}")
        except Exception:
            pass
    finally:
        with _lock:
            _archiving_chats.discard(chat_id)


def _format_chat_log(messages: list[dict]) -> str:
    """
    将消息列表拼接为可读的聊天记录文本
    格式：[时间] 发送人: 内容
    """
    lines = []
    for msg in messages:
        # 跳过系统消息和卡片消息（无实际内容）
        msg_type = msg.get("msg_type", "")
        if msg_type in ("system", "interactive"):
            continue

        # 时间格式化
        try:
            ts = int(msg["create_time"]) / 1000  # 毫秒→秒
            time_str = datetime.fromtimestamp(ts, tz=_BJT).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            time_str = "未知时间"

        # 发送人
        sender = msg.get("sender_id", "未知")

        # 消息内容解析
        content = _extract_content(msg_type, msg.get("content", ""))

        lines.append(f"[{time_str}] {sender}: {content}")

    return "\n".join(lines)


def _extract_content(msg_type: str, content_str: str) -> str:
    """从消息内容 JSON 中提取可读文本"""
    try:
        content = json.loads(content_str) if content_str else {}
    except (json.JSONDecodeError, TypeError):
        return content_str or ""

    if msg_type == "text":
        return content.get("text", "").strip()
    elif msg_type == "post":
        # 富文本：提取所有文本段
        texts = []
        title = content.get("title", "")
        if title:
            texts.append(title)
        for line in content.get("content", []):
            for seg in line:
                if seg.get("tag") == "text":
                    texts.append(seg.get("text", ""))
        return " ".join(texts).strip() or "[富文本]"
    elif msg_type == "image":
        return "[图片]"
    elif msg_type == "file":
        return f"[文件: {content.get('file_name', '未知')}]"
    elif msg_type == "audio":
        return "[语音]"
    elif msg_type == "video":
        return "[视频]"
    elif msg_type == "sticker":
        return "[表情]"
    elif msg_type == "interactive":
        return "[卡片消息]"
    elif msg_type == "share_chat":
        return "[分享群聊]"
    elif msg_type == "share_user":
        return "[分享名片]"
    elif msg_type == "system":
        return "[系统消息]"
    else:
        return f"[{msg_type}]" if msg_type else "[未知消息类型]"
