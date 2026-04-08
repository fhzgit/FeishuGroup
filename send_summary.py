import json
import logging
import os
import threading
from typing import Optional

from services import feishu_api

logger = logging.getLogger(__name__)
_SENT_CHATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".summary_sent_chats.json")
_sent_chats_lock = threading.Lock()


SUMMARY_CARD = {
  "schema": "2.0",
  "config": {
    "update_multi": True,
    "wide_screen_mode": True
  },
  "header": {
    "title": {
      "tag": "plain_text",
      "content": "🤖 智能服务群机器人 - 使用指南"
    },
    "template": "blue"
  },
  "body": {
    "elements": [
      {
        "tag": "markdown",
        "content": "**欢迎使用智能服务群机器人！**\n我不仅能通过内置知识库为您全天候极速解答问题，还在您需要人工协助时支持一键拉起专家专属群，问题排查结果自动归档跟进。"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "✨ **<font color='blue'>核心功能</font>**\n- **✅ 极速智能解答**：结合 Aily 知识归纳技术，对群内提问进行智能、精准的回复。\n- **👷‍♂️ 一键转专家群**：AI 无法解决的问题，可直接点击卡片按扭转接对应业务专家。新群聊直达上下文！\n- **♻️ 群生命周期管理**：转交拉群处理完毕后，如果服务群闲置判定超时，系统会自动清理释放保持列表极简。\n- **📊 知识无缝沉淀**：极速解决与人工兜底的每笔处理记录，自动化推送到飞书多维表分析归栏。"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "🚀 **<font color='blue'>极简使用步骤</font>**"
      },
      {
        "tag": "markdown",
        "content": "- **<font color='green'>Step 1. 发起即时提问</font>**：在接入机器人的话题群中直接 **新建话题** 发出您的需求，（无需@机器人）底层大模型即会被唤醒检索给出解答。\n- **<font color='orange'>Step 2. AI 回答闭环</font>**：如果拦截方案可行，直接点击解答下方的 **✅ 已解决** 按钮即刻复盘结案，无需其它操作。\n- **<font color='purple'>Step 3. 专家对口流转</font>**：遇到复杂冷门难题？点击对应专业的 **转接人工** 按钮，机器人全自动为您拉取独立服务群连线探讨。 <font color='grey'>（注：讨论结束放置数分钟后，服务组内会触发自动释放回收）</font>"
      },
      {
        "tag": "hr"
      },
      {
        "tag": "markdown",
        "content": "<font color='grey'>🏷 致力于打造极简清爽的群客诉流转分发体验</font>"
      }
    ]
  }
}

def send_summary_card(chat_id: str) -> Optional[str]:
    """向指定群发送使用说明卡片。"""
    return feishu_api.send_card_message(
        chat_id,
        json.dumps(SUMMARY_CARD, ensure_ascii=False),
    )


def _load_sent_chats() -> set[str]:
    try:
        if os.path.exists(_SENT_CHATS_FILE):
            with open(_SENT_CHATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return {item for item in data if isinstance(item, str) and item}
    except Exception as e:
        logger.warning(f"摘要卡片发送记录加载失败: {e}")
    return set()


def _save_sent_chats(chat_ids: set[str]) -> None:
    try:
        with open(_SENT_CHATS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(chat_ids), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"摘要卡片发送记录保存失败: {e}")


def send_summary_card_once(chat_id: str) -> Optional[str]:
    """同一个群只发送一次使用说明卡片。"""
    with _sent_chats_lock:
        sent_chats = _load_sent_chats()
        if chat_id in sent_chats:
            logger.info(f"摘要卡片已发送过，跳过: chat_id={chat_id}")
            return None

        message_id = send_summary_card(chat_id)
        if message_id:
            sent_chats.add(chat_id)
            _save_sent_chats(sent_chats)
        return message_id
