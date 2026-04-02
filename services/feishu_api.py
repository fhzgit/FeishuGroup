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

def send_card_message(chat_id: str, card_json: str, reply_to_message_id: str = "") -> Optional[str]:
    """
    向指定群发送互动卡片消息
    :param reply_to_message_id: 回复指定消息（用于话题群回复到同一话题）
    :return: 发送成功返回 message_id，失败返回 None
    """
    client = get_client()

    # 如果指定了 reply_to_message_id，使用 reply 接口（话题群回复到同一话题）
    if reply_to_message_id:
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
        request = (
            ReplyMessageRequest.builder()
            .message_id(reply_to_message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(card_json)
                .build()
            )
            .build()
        )
        response = client.im.v1.message.reply(request)
    else:
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
) -> tuple[Optional[str], int, str]:
    """
    创建服务群并拉入指定用户
    :return: (chat_id, error_code, error_msg) 成功时 chat_id 有值且 error_code=0
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
        return None, response.code, response.msg
    chat_id = response.data.chat_id
    logger.info(f"服务群创建成功: chat_id={chat_id}, name={chat_name}")
    return chat_id, 0, ""


def add_chat_members(chat_id: str, user_open_ids: list[str]) -> bool:
    """
    将用户拉入已有群聊
    :param chat_id:        群聊 ID
    :param user_open_ids:  要拉入的用户 open_id 列表
    :return: 是否成功
    """
    client = get_client()
    from lark_oapi.api.im.v1 import CreateChatMembersRequest, CreateChatMembersRequestBody

    unique_ids = list(dict.fromkeys(user_open_ids))
    request = (
        CreateChatMembersRequest.builder()
        .chat_id(chat_id)
        .member_id_type("open_id")
        .request_body(
            CreateChatMembersRequestBody.builder()
            .id_list(unique_ids)
            .build()
        )
        .build()
    )
    response = client.im.v1.chat_members.create(request)
    if not response.success():
        logger.error(f"拉人入群失败: chat_id={chat_id}, code={response.code}, msg={response.msg}")
        return False
    logger.info(f"拉人入群成功: chat_id={chat_id}, users={unique_ids}")
    return True


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


# ── 获取群历史消息 ──────────────────────────────────────────

def list_chat_messages(chat_id: str) -> list[dict]:
    """
    分页获取群聊全部历史消息（按时间升序）
    :return: 消息列表，每条包含 sender_id, msg_type, content, create_time
    """
    from lark_oapi.api.im.v1 import ListMessageRequest

    client = get_client()
    all_messages = []
    page_token = ""

    while True:
        req_builder = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .sort_type("ByCreateTimeAsc")
            .page_size(50)
        )
        if page_token:
            req_builder = req_builder.page_token(page_token)

        request = req_builder.build()
        response = client.im.v1.message.list(request)

        if not response.success():
            logger.error(
                f"获取群消息失败: chat_id={chat_id}, "
                f"code={response.code}, msg={response.msg}"
            )
            break

        items = response.data.items or []
        for item in items:
            all_messages.append({
                "sender_id": item.sender.id if item.sender else "",
                "msg_type": item.msg_type or "",
                "content": item.body.content if item.body else "",
                "create_time": item.create_time or "",
            })

        if not response.data.has_more:
            break
        page_token = response.data.page_token or ""

    logger.info(f"获取群消息完成: chat_id={chat_id}, 共 {len(all_messages)} 条")
    return all_messages


# ── 获取群信息 ──────────────────────────────────────────────

def get_chat_info(chat_id: str) -> Optional[str]:
    """
    获取群聊名称
    :return: 群名称，失败返回 None
    """
    from lark_oapi.api.im.v1 import GetChatRequest

    client = get_client()
    request = GetChatRequest.builder().chat_id(chat_id).build()
    response = client.im.v1.chat.get(request)
    if not response.success():
        logger.error(f"获取群信息失败: code={response.code}, msg={response.msg}")
        return None
    return response.data.name if response.data else None


# ── 多维表格写入 ──────────────────────────────────────────

def create_bitable_record(app_token: str, table_id: str, fields: dict) -> Optional[str]:
    """
    向多维表格写入一行记录
    :param fields: 字段名→值的 dict
    :return: 成功返回 record_id，失败返回 None
    """
    from lark_oapi.api.bitable.v1 import (
        CreateAppTableRecordRequest,
        AppTableRecord,
    )

    client = get_client()
    record = AppTableRecord.builder().fields(fields).build()
    request = (
        CreateAppTableRecordRequest.builder()
        .app_token(app_token)
        .table_id(table_id)
        .request_body(record)
        .build()
    )
    response = client.bitable.v1.app_table_record.create(request)
    if not response.success():
        logger.error(
            f"写入多维表格失败: code={response.code}, msg={response.msg}"
        )
        return None
    logger.info("多维表格写入成功")
    record_id = response.data.record.record_id if response.data and response.data.record else ""
    return record_id

def update_bitable_record(app_token: str, table_id: str, record_id: str, fields: dict) -> bool:
    """
    更新多维表格的已有记录
    """
    from lark_oapi.api.bitable.v1 import (
        UpdateAppTableRecordRequest,
        AppTableRecord,
    )
    client = get_client()
    record = AppTableRecord.builder().fields(fields).build()
    request = (
        UpdateAppTableRecordRequest.builder()
        .app_token(app_token)
        .table_id(table_id)
        .record_id(record_id)
        .request_body(record)
        .build()
    )
    response = client.bitable.v1.app_table_record.update(request)
    if not response.success():
        logger.error(
            f"更新多维表格失败: code={response.code}, msg={response.msg}"
        )
        return False
    logger.info(f"多维表格更新成功: record_id={record_id}")
    return True


# ── 解散群聊 ──────────────────────────────────────────────

def delete_chat(chat_id: str) -> bool:
    """
    解散群聊（机器人必须是群主或创建者）
    """
    from lark_oapi.api.im.v1 import DeleteChatRequest

    client = get_client()
    request = DeleteChatRequest.builder().chat_id(chat_id).build()
    response = client.im.v1.chat.delete(request)
    if not response.success():
        logger.error(
            f"解散群聊失败: chat_id={chat_id}, "
            f"code={response.code}, msg={response.msg}"
        )
        return False
    logger.info(f"群聊已解散: chat_id={chat_id}")
    return True


# ── 转发消息 ──────────────────────────────────────────────

def forward_message(message_id: str, receive_id: str) -> bool:
    """
    转发已有消息到指定群聊（保留原始格式，包括图片/文件等）
    """
    from lark_oapi.api.im.v1 import ForwardMessageRequest, ForwardMessageRequestBody

    client = get_client()
    request = (
        ForwardMessageRequest.builder()
        .message_id(message_id)
        .receive_id_type("chat_id")
        .request_body(
            ForwardMessageRequestBody.builder()
            .receive_id(receive_id)
            .build()
        )
        .build()
    )
    response = client.im.v1.message.forward(request)
    if not response.success():
        logger.error(
            f"转发消息失败: message_id={message_id}, "
            f"code={response.code}, msg={response.msg}"
        )
        return False
    logger.info(f"消息转发成功: message_id={message_id} -> chat_id={receive_id}")
    return True
