"""
飞书卡片构建模块（schema 2.0）
负责构建三种卡片状态：初始态、处理中、已解决
"""
import json
import re
from typing import Optional

_AT_OPEN_TAG_PATTERN = re.compile(r"<at\b([^>]*)>", re.IGNORECASE)
_AT_CLOSE_TAG_PATTERN = re.compile(r"</at>", re.IGNORECASE)
_AT_ID_ATTR_PATTERN = re.compile(
    r"\bid\s*=\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s>]+))",
    re.IGNORECASE,
)
_RAW_INTERNAL_AT_PATTERN = re.compile(
    r"@(?:itw|ou|on|open)_[-a-zA-Z0-9_]+(?:[\u4e00-\u9fff]{0,12})?",
    re.IGNORECASE,
)


def _sanitize_ai_markdown(text: str) -> str:
    """
    清洗 AI 输出中的飞书 @ 标签，避免非法 id 导致卡片更新失败。
    例如：<at id='itw_xxx'></at> / <at id=itw_xxx> -> @itw_xxx
    """
    if not text:
        return ""

    def _replace_open_tag(match: re.Match) -> str:
        attrs = match.group(1) or ""
        id_match = _AT_ID_ATTR_PATTERN.search(attrs)
        if id_match:
            at_id = next((g for g in id_match.groups() if g), "")
            if at_id:
                return f"@{at_id}"
        return "@成员"

    sanitized = _AT_OPEN_TAG_PATTERN.sub(_replace_open_tag, text)
    sanitized = _AT_CLOSE_TAG_PATTERN.sub("", sanitized)
    # 清除模型直接输出的内部人员标识，如 @itw_xxx、@ou_xxx
    sanitized = _RAW_INTERNAL_AT_PATTERN.sub("@相关同事", sanitized)
    return sanitized


def _build_dept_buttons(
    departments: dict,
    asker_open_id: str,
    origin_message_id: str,
    origin_chat_id: str,
    ai_answer_short: str,
    stats_record_id: str = "",
) -> list[dict]:
    """生成部门按钮列表（纵向排列，兼容所有屏幕宽度）"""
    buttons = []
    for key in departments:
        dept = departments[key]
        button_name = dept.get("button_name") or dept.get("name") or key
        department_name = dept.get("name") or button_name
        callback_value = {
            "action": "create_service_group",
            "department": key,
            "department_name": department_name,
            "handler_open_ids": dept.get("ids", []),
            "asker_open_id": asker_open_id,
            "origin_message_id": origin_message_id,
            "origin_chat_id": origin_chat_id,
            "ai_answer": ai_answer_short,
            "stats_record_id": stats_record_id,
        }
        buttons.append({
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": f"{dept.get('icon', '🏢')}  {button_name}",
            },
            "type": "primary",
            "size": "medium",
            "width": "fill",
            "behaviors": [
                {
                    "type": "callback",
                    "value": callback_value,
                }
            ],
        })
    return buttons


def build_loading_card() -> str:
    """构建「AI 思考中」占位卡片，消息发出后立即展示，待 Aily 回答后原地更新"""
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "智能客服正在思考中..."},
            "subtitle": {"tag": "plain_text", "content": "由 AI 自动生成 · 仅供参考"},
            "template": "indigo",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "⏳ **AI 正在分析您的问题，请稍候...**",
                }
            ]
        },
    }
    return json.dumps(card, ensure_ascii=False)


def build_ai_reply_card(
    asker_open_id: str,
    ai_answer: str,
    origin_message_id: str,
    origin_chat_id: str,
    departments: dict,
    stats_record_id: str = "",
) -> str:
    """
    构建初始态卡片：AI 回答区 + 已解决按钮 + 部门按钮（schema 2.0）
    """
    ai_answer = _sanitize_ai_markdown(ai_answer)
    ai_answer_short = ai_answer[:500]
    button_rows = _build_dept_buttons(
        departments, asker_open_id, origin_message_id, origin_chat_id, ai_answer_short, stats_record_id
    )

    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "智能客服已进行回答，请点击查看详细内容"},
            "subtitle": {"tag": "plain_text", "content": "由 AI 自动生成 · 仅供参考"},
            "template": "indigo",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": ai_answer,
                },
                # ── 已解决按钮（AI 内容正下方）──────────────────
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅  AI 回答已解决我的问题"},
                    "type": "primary",
                    "width": "fill",
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "action": "ai_solved",
                                "ai_answer": ai_answer_short,
                                "origin_message_id": origin_message_id,
                                "origin_chat_id": origin_chat_id,
                                "stats_record_id": stats_record_id,
                            },
                        }
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "padding": "0px 16px 0px 16px",
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": "#### 🙋 联系人工客服\n\n如 AI 解答无法满足您的需求，请选择对应板块，机器人将自动为您建立专属服务群：",
                                }
                            ],
                        }
                    ],
                },
                *button_rows,
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": "<font color='grey'>⚡ 点击按钮后，机器人将自动建群，并将您与对应板块负责人拉入群中，请稍候...</font>",
                },
            ]
        },
    }

    return json.dumps(card, ensure_ascii=False)


def build_ai_solved_card(ai_answer: str, operator_open_id: str) -> str:
    """
    构建「AI 已解决」状态卡片（用户点击已解决后更新）

    :param ai_answer:        AI 原始回答内容
    :param operator_open_id: 点击用户的 open_id
    """
    ai_answer = _sanitize_ai_markdown(ai_answer)
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✅ 问题已解决"},
            "subtitle": {"tag": "plain_text", "content": "AI 回答成功解决了您的问题"},
            "template": "green",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": ai_answer,
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": f"感谢 <at id='{operator_open_id}'></at> 的反馈！此次问题已标记为已解决，本次 AI 回答将帮助不断优化服务质量。",
                },
                {
                    "tag": "markdown",
                    "content": "<font color='grey'>📊 您的满意度反馈对我们非常重要，感谢使用！</font>",
                },
            ]
        },
    }
    return json.dumps(card, ensure_ascii=False)


def build_processing_card(ai_answer: str, department_name: str) -> dict:
    """
    构建处理中态卡片（返回 dict，用于卡片 action 立即响应）
    移除所有按钮，显示正在建群提示
    """
    ai_answer = _sanitize_ai_markdown(ai_answer)
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⏳ 正在处理..."},
            "subtitle": {
                "tag": "plain_text",
                "content": f"正在为您联系「{department_name}」团队",
            },
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": ai_answer,
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": f"#### ⏳ 处理中\n\n正在为您联系「**{department_name}**」团队，建立专属服务群，请稍候...",
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": "<font color='grey'>⚡ 服务群创建中，请勿重复点击...</font>",
                },
            ]
        },
    }


def build_done_card(
    ai_answer: str,
    department_name: str,
    new_chat_id: Optional[str] = None,
    error_msg: Optional[str] = None,
    departments: Optional[dict] = None,
    asker_open_id: str = "",
    origin_message_id: str = "",
    origin_chat_id: str = "",
) -> str:
    """
    构建已解决态卡片（JSON 字符串，用于异步更新原卡片）
    更新状态为"已跟进解决"，同时保留部门按钮供其他用户点击拉群

    :param ai_answer:          原 AI 回答内容（保留展示）
    :param department_name:    处理部门名称
    :param new_chat_id:        新建服务群 chat_id（成功时有值）
    :param error_msg:          错误信息（失败时有值）
    :param departments:        config.DEPARTMENT_HANDLERS（保留按钮用）
    :param asker_open_id:      提问者 open_id
    :param origin_message_id:  原始消息 ID
    :param origin_chat_id:     原始群 chat_id
    :return: 卡片 JSON 字符串
    """
    ai_answer = _sanitize_ai_markdown(ai_answer)
    if error_msg:
        header_title = "❌ 服务群创建失败"
        header_subtitle = "请稍后重试或联系管理员"
        header_template = "red"
        status_md = f"❌ 创建失败：{error_msg}"
    else:
        header_title = "✅ 已跟进解决"
        header_subtitle = f"已由「{department_name}」团队跟进"
        header_template = "green"
        status_md = (
            f"✅ 已安排「**{department_name}**」团队为您建立专属服务群，"
            f"请在群中继续沟通。"
        )

    elements = [
        {
            "tag": "markdown",
            "content": ai_answer,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": status_md,
        },
    ]

    # 保留部门按钮（供其他用户点击拉群）
    if departments:
        ai_answer_short = ai_answer[:500]
        buttons = _build_dept_buttons(
            departments, asker_open_id, origin_message_id, origin_chat_id, ai_answer_short
        )
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": "#### 🙋 联系人工客服\n\n如需其他板块协助，请点击对应按钮加入服务群：",
        })
        elements.extend(buttons)
        elements.append({
            "tag": "markdown",
            "content": "<font color='grey'>⚡ 点击按钮后，机器人将自动拉您入对应服务群。</font>",
        })

    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "subtitle": {"tag": "plain_text", "content": header_subtitle},
            "template": header_template,
        },
        "body": {
            "elements": elements,
        },
    }

    return json.dumps(card, ensure_ascii=False)


def build_idle_warning_card(chat_id: str) -> str:
    """构建服务群空闲提醒卡片（仅「确认解散」按钮，附兜底说明）"""
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "服务群闲置提醒"},
            "subtitle": {"tag": "plain_text", "content": "此服务群长时间无新消息"},
            "template": "wathet",
        },
        "body": {
            "elements": [
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": "**问题已得到解决了吗？**\n\n"
                                               "该服务群已超过一段时间没有新消息。\n"
                                               "如果问题已处理完毕，请点击下方按钮确认解散群聊，"
                                               "我们将自动整理并归档本次服务记录。",
                                }
                            ],
                        }
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅  确认问题已解决，解散群聊"},
                    "type": "primary",
                    "width": "fill",
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": {
                                "action": "confirm_dissolve",
                                "chat_id": chat_id,
                            },
                        }
                    ],
                },
                {
                    "tag": "markdown",
                    "content": "<font color='grey'>💬 如仍有问题需讨论，直接在群内发送新消息即可继续。\n📦 若 24 小时内无任何新消息且未确认，该群将自动归档解散。</font>",
                },
            ]
        },
    }
    return json.dumps(card, ensure_ascii=False)


def build_idle_countdown_card(
    minutes: int,
    operator_name: str,
    archiving: bool = False,
    msg_count: int = 0,
) -> str:
    """
    构建确认倒计时卡片（绿色，已解决状态）

    :param minutes:       倒计时分钟数
    :param operator_name: 确认人姓名
    :param archiving:     True = 正在归档中（占位状态），False = 归档完成
    :param msg_count:     归档完成时的消息条数
    """
    if archiving:
        subtitle = "正在整理聊天记录，请稍候..."
        body_text = (
            f"感谢 {operator_name} 的确认！\n\n"
            "⏳ **正在归档聊天记录...**"
        )
        footer = ""
    else:
        subtitle = f"群聊将在 {minutes} 分钟后自动解散"
        archive_note = f"📂 已归档 **{msg_count}** 条聊天记录" if msg_count else "📂 聊天记录已归档"
        body_text = (
            f"感谢 {operator_name} 的确认！\n\n"
            f"{archive_note}，本群将在 **{minutes} 分钟**后自动解散。"
        )
        footer = "<font color='grey'>💬 若仍有问题需要继续沟通，请直接在群内发送消息，解散将自动取消。</font>"

    elements = [
        {
            "tag": "column_set",
            "flex_mode": "none",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": body_text,
                        }
                    ],
                }
            ],
        },
        {"tag": "hr"},
    ]

    if footer:
        elements.append({"tag": "markdown", "content": footer})

    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "✅ 问题已确认解决"},
            "subtitle": {"tag": "plain_text", "content": subtitle},
            "template": "green",
        },
        "body": {"elements": elements},
    }
    return json.dumps(card, ensure_ascii=False)


def build_cancel_dissolve_card(operator_name: str) -> str:
    """构建取消解散卡片"""
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "已取消解散"},
            "template": "green",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"由 {operator_name} 手动取消，解散流程已终止。请继续在群内沟通。",
                }
            ]
        },
    }
    return json.dumps(card, ensure_ascii=False)


def build_welcome_card(
    department_name: str,
    department_icon: str,
    asker_open_id: str,
    handler_open_ids: list,
) -> str:
    """
    构建服务群开场欢迎卡片

    :param department_name:  部门名称，如「产品咨询」
    :param department_icon:  部门图标 emoji，如「📦」
    :param asker_open_id:    提问者 open_id（用于 @）
    :param handler_open_ids: 负责人 open_id 列表（用于 @）
    :return: 卡片 JSON 字符串
    """
    # 构建负责人 @ 文本
    handlers_at = " ".join(f"<at id='{uid}'></at>" for uid in handler_open_ids)

    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"{department_icon} 专属服务群已就绪"},
            "subtitle": {"tag": "plain_text", "content": f"{department_name}团队为您提供一对一服务"},
            "template": "indigo",
        },
        "body": {
            "elements": [
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": (
                                        f"👤 **提问方**\n<at id='{asker_open_id}'></at>\n\n"
                                        f"🧑‍💼 **服务团队**\n{handlers_at if handlers_at else department_name}"
                                    ),
                                }
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 1,
                            "elements": [
                                {
                                    "tag": "markdown",
                                    "content": (
                                        "📌 **使用指引**\n\n"
                                        "• 请在本群描述您的详细问题\n"
                                        "• 可发送截图、文件辅助说明\n"
                                        "• 问题解决后将自动归档"
                                    ),
                                }
                            ],
                        },
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "content": "<font color='grey'>📂 问题解决后，本群将自动归档记录并解散。如需继续沟通请直接在群内发送消息。</font>",
                },
            ]
        },
    }
    return json.dumps(card, ensure_ascii=False)
