"""
群成员事件处理模块
用于处理机器人被拉入新群时的初始化动作
"""
import logging

from lark_oapi.api.im.v1 import P2ImChatMemberBotAddedV1

import config
import send_summary

logger = logging.getLogger(__name__)


def do_p2_im_chat_member_bot_added_v1(data: P2ImChatMemberBotAddedV1) -> None:
    """
    机器人被拉入群聊时触发。
    仅在普通监听群中发送一次摘要卡片，排除机器人创建的服务群。
    """
    event = data.event
    chat_id = event.chat_id or ""
    chat_name = event.name or ""

    if not chat_id:
        logger.warning("机器人入群事件缺少 chat_id，跳过发送摘要卡片")
        return

    service_group_prefix = f"{config.SERVICE_GROUP_PREFIX}-"
    if chat_name.startswith(service_group_prefix):
        logger.info(f"检测到服务群入群事件，跳过摘要卡片: chat_id={chat_id}, chat_name={chat_name}")
        return

    message_id = send_summary.send_summary_card_once(chat_id)
    if message_id:
        logger.info(f"机器人入群后已发送摘要卡片: chat_id={chat_id}, message_id={message_id}")
    else:
        logger.info(f"机器人入群后未发送摘要卡片（可能已发送过）: chat_id={chat_id}")
