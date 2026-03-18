"""
飞书卡片 JSON 构建模块
"""
from typing import Optional


def build_question_card(
    asker_open_id: str,
    handler_open_ids: list[str],
    question_preview: str,
    origin_message_id: str,
    chat_id: str,
    image_key: str = "",
) -> str:
    """
    构建「收到新问题」互动卡片

    :param asker_open_id: 提问者 open_id
    :param handler_open_ids: 负责人 open_id 列表
    :param question_preview: 问题内容预览（截取前200字）
    :param origin_message_id: 原始消息 ID（用于创群后更新卡片状态）
    :param chat_id: 群 chat_id
    :param image_key: 图片消息的 image_key（空则为文本消息）
    :return: 卡片 JSON 字符串
    """
    import json

    # 构建 @负责人 文本
    at_handlers = " ".join(
        [f'<at id="{uid}"></at>' for uid in handler_open_ids]
    )

    # 按钮携带的业务数据
    button_value = {
        "action": "create_service_group",
        "asker_open_id": asker_open_id,
        "handler_open_ids": handler_open_ids,
        "question_preview": question_preview[:100],  # 限制长度
        "origin_message_id": origin_message_id,
        "origin_chat_id": chat_id,
    }

    # 构建卡片内容元素
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f'**提问者：** <at id="{asker_open_id}"></at>\n\n'
                    f"**问题内容：**\n{question_preview}\n\n"
                    f"**负责人：** {at_handlers} 请处理此问题 👆"
                ),
            },
        },
    ]

    # 图片消息：在文字描述后插入图片元素
    if image_key:
        elements.append({
            "tag": "img",
            "img_key": image_key,
            "alt": {"tag": "plain_text", "content": "用户发送的图片"},
        })

    elements.extend([
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "🚀 创建服务群"},
                    "type": "primary",
                    "value": button_value,
                }
            ],
        },
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "点击按钮后，将自动创建服务群并邀请提问者与负责人加入",
                }
            ],
        },
    ])

    card = {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 收到新问题 · 待处理"},
            "template": "orange",
        },
        "elements": elements,
    }

    return json.dumps(card, ensure_ascii=False)


def build_done_card(
    asker_open_id: str,
    handler_open_ids: list[str],
    question_preview: str,
    new_chat_id: Optional[str] = None,
    error_msg: Optional[str] = None,
) -> str:
    """
    构建「服务群已创建」更新卡片（覆盖原卡片，禁用按钮）
    """
    import json

    at_handlers = " ".join(
        [f'<at id="{uid}"></at>' for uid in handler_open_ids]
    )

    if error_msg:
        status_text = f"❌ 创建失败：{error_msg}"
        header_template = "red"
        header_title = "📋 服务群创建失败"
        button_text = "❌ 创建失败"
    else:
        status_text = "✅ 服务群已创建，提问者与负责人已自动拉群"
        header_template = "green"
        header_title = "✅ 已拉群"
        button_text = "✅ 已拉群"

    card = {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": header_template,
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**提问者：** <at id=\"{asker_open_id}\"></at>\n\n"
                        f"**问题内容：**\n{question_preview}\n\n"
                        f"**负责人：** {at_handlers}\n\n"
                        f"**状态：** {status_text}"
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {
                            "tag": "plain_text",
                            "content": button_text,
                        },
                        "type": "default",
                        "disabled": True,
                        "value": {"action": "done"},
                    }
                ],
            },
        ],
    }

    return json.dumps(card, ensure_ascii=False)


def build_processing_card(
    asker_open_id: str,
    handler_open_ids: list[str],
    question_preview: str,
) -> dict:
    """
    构建「正在处理中」状态卡片 dict（用于 card action 响应，立刻更新 UI）

    :return: 卡片 dict（不是 JSON 字符串）
    """
    at_handlers = " ".join(
        [f'<at id="{uid}"></at>' for uid in handler_open_ids]
    )

    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⏳ 服务群创建中..."},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**提问者：** <at id=\"{asker_open_id}\"></at>\n\n"
                        f"**问题内容：**\n{question_preview}\n\n"
                        f"**负责人：** {at_handlers}\n\n"
                        "**状态：** ⏳ 正在创建服务群，请稍候..."
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "⏳ 创建中..."},
                        "type": "default",
                        "disabled": True,
                        "value": {"action": "done"},
                    }
                ],
            },
        ],
    }
