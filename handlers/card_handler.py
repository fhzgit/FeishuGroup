"""
卡片按钮回调处理模块
处理用户点击「创建服务群」按钮的事件
"""
import logging
import threading
from typing import Any

import config
from services import card_builder, feishu_api

logger = logging.getLogger(__name__)

# 幂等锁：防止同一条消息被重复处理（多次点击）
_processed_messages: set[str] = set()
_lock = threading.Lock()


def handle_card_action(action_value: dict[str, Any], operator_open_id: str, card_message_id: str = "") -> dict:
    """
    卡片按钮回调主处理函数
    
    :param action_value: 按钮绑定的 value 数据
    :param operator_open_id: 点击按钮的用户 open_id
    :return: 立即返回给飞书的响应体（toast 提示）
    """
    action = action_value.get("action", "")

    if action != "create_service_group":
        logger.warning(f"未知的卡片动作: {action}")
        return _toast("warning", "未知操作")

    origin_message_id = action_value.get("origin_message_id", "")

    # 幂等校验：防止重复创群
    with _lock:
        if origin_message_id in _processed_messages:
            logger.info(f"重复点击，忽略: origin_message_id={origin_message_id}")
            return _toast("info", "服务群已创建，请勿重复点击")
        _processed_messages.add(origin_message_id)

    # 立即返回 toast（3秒限制内必须响应）
    # 异步执行真正的创群逻辑
    threading.Thread(
        target=_async_create_group,
        args=(action_value, operator_open_id, card_message_id),
        daemon=True,
    ).start()

    return _toast("info", "正在创建服务群，请稍候...")


def _async_create_group(action_value: dict[str, Any], operator_open_id: str, card_message_id: str = "") -> None:
    """
    异步执行创建服务群流程（在独立线程中运行，不受 3 秒限制）
    """
    asker_open_id: str = action_value.get("asker_open_id", "")
    handler_open_ids: list[str] = action_value.get("handler_open_ids", [])
    question_preview: str = action_value.get("question_preview", "")
    origin_message_id: str = action_value.get("origin_message_id", "")
    origin_chat_id: str = action_value.get("origin_chat_id", "")

    logger.info(
        f"开始创建服务群: asker={asker_open_id}, "
        f"handlers={handler_open_ids}, question={question_preview[:30]}"
    )

    # 服务群名称（截取问题前20字）
    name_suffix = question_preview[:20].strip() or "业务咨询"
    chat_name = f"{config.SERVICE_GROUP_PREFIX}-{name_suffix}"

    # 合并所有需要拉入群的成员（提问者 + 所有负责人 + 点击按钮的操作者）
    members = list({asker_open_id, operator_open_id, *handler_open_ids})

    # 1. 创建服务群
    new_chat_id = feishu_api.create_service_chat(
        chat_name=chat_name,
        user_open_ids=members,
    )

    # 2. 更新原卡片状态
    if new_chat_id:
        done_card = card_builder.build_done_card(
            asker_open_id=asker_open_id,
            handler_open_ids=handler_open_ids,
            question_preview=question_preview,
            new_chat_id=new_chat_id,
        )
        feishu_api.update_card_message(card_message_id, done_card)

        # 3. 在新服务群发送欢迎消息
        welcome_text = (
            f"👋 欢迎来到服务群！\n\n"
            f"📝 原始问题：\n{question_preview}\n\n"
            f"本群由机器人自动创建，专门处理以上问题，请相关同学在此群进行沟通。"
        )
        feishu_api.send_text_message(new_chat_id, welcome_text)
        logger.info(f"服务群创建完成: new_chat_id={new_chat_id}")
    else:
        # 创群失败，更新卡片为失败状态
        done_card = card_builder.build_done_card(
            asker_open_id=asker_open_id,
            handler_open_ids=handler_open_ids,
            question_preview=question_preview,
            error_msg="API 调用失败，请联系管理员",
        )
        feishu_api.update_card_message(card_message_id, done_card)

        # 失败时从幂等集合中移除，允许重试
        with _lock:
            _processed_messages.discard(origin_message_id)

        logger.error("服务群创建失败")


def _toast(toast_type: str, content: str) -> dict:
    """构建飞书卡片 toast 响应"""
    return {
        "toast": {
            "type": toast_type,  # info / success / warning / error
            "content": content,
        }
    }
