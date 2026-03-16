"""
飞书 API 封装模块
统一封装所有对飞书 Open API 的调用
"""
import logging
import json
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateChatRequest,
    CreateChatRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
)

import config

logger = logging.getLogger(__name__)

# 全局飞书客户端（由 main.py 初始化后注入）
_client: Optional[lark.Client] = None


def get_client() -> lark.Client:
    global _client
    if _client is None:
        _client = (
            lark.Client.builder()
            .app_id(config.APP_ID)
            .app_secret(config.APP_SECRET)
            .log_level(lark.LogLevel.DEBUG if config.LOG_LEVEL == "DEBUG" else lark.LogLevel.INFO)
            .build()
        )
    return _client


# ── 发送消息 ────────────────────────────────────────────────

def send_card_message(chat_id: str, card_json: str) -> Optional[str]:
    """
    向指定群发送互动卡片消息
    :return: 发送成功返回 message_id，失败返回 None
    """
    client = get_client()
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(card_json)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        logger.error(
            f"发送卡片消息失败: code={response.code}, msg={response.msg}"
        )
        return None
    message_id = response.data.message_id
    logger.info(f"卡片消息发送成功: message_id={message_id}")
    return message_id


def update_card_message(message_id: str, card_json: str) -> bool:
    """
    更新已发送的卡片消息（用于将按钮改为「已处理」状态）
    :return: 是否更新成功
    """
    client = get_client()
    request = (
        PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            PatchMessageRequestBody.builder()
            .content(card_json)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.patch(request)
    if not response.success():
        logger.error(
            f"更新卡片消息失败: message_id={message_id}, "
            f"code={response.code}, msg={response.msg}"
        )
        return False
    logger.info(f"卡片消息更新成功: message_id={message_id}")
    return True


# ── 创建群组 ────────────────────────────────────────────────

def create_service_chat(
    chat_name: str,
    user_open_ids: list[str],
) -> Optional[str]:
    """
    创建服务群并拉入指定用户
    :param chat_name: 群名称
    :param user_open_ids: 初始成员 open_id 列表（机器人自动加入，无需重复添加）
    :return: 创建成功返回 chat_id，失败返回 None
    """
    client = get_client()

    # 去重
    unique_ids = list(dict.fromkeys(user_open_ids))
    if len(unique_ids) > 50:
        logger.warning(f"成员数量超过50人限制，仅取前50人")
        unique_ids = unique_ids[:50]

    request = (
        CreateChatRequest.builder()
        .user_id_type("open_id")
        .request_body(
            CreateChatRequestBody.builder()
            .name(chat_name)
            .user_id_list(unique_ids)
            .description("由飞书机器人自动创建，专用于处理业务问题")
            .build()
        )
        .build()
    )
    response = client.im.v1.chat.create(request)
    if not response.success():
        logger.error(
            f"创建群组失败: code={response.code}, msg={response.msg}"
        )
        return None
    chat_id = response.data.chat_id
    logger.info(f"服务群创建成功: chat_id={chat_id}, name={chat_name}")
    return chat_id


def send_text_message(chat_id: str, text: str) -> Optional[str]:
    """
    向指定群发送普通文本消息
    """
    client = get_client()
    content = json.dumps({"text": text}, ensure_ascii=False)
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(content)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        logger.error(
            f"发送文本消息失败: code={response.code}, msg={response.msg}"
        )
        return None
    return response.data.message_id
