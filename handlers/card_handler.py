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

    # 立即返回 toast + 处理中卡片（3秒限制内必须响应）
    # 返回 processing_card 可让飞书立刻更新按钮为「创建中...」，避免 UI 闪回
    asker_open_id = action_value.get("asker_open_id", "")
    handler_open_ids = action_value.get("handler_open_ids", config.HANDLER_OPEN_IDS)
    question_preview = action_value.get("question_preview", "")
    processing_card = card_builder.build_processing_card(
        asker_open_id=asker_open_id,
        handler_open_ids=handler_open_ids,
        question_preview=question_preview,
    )

    threading.Thread(
        target=_async_create_group,
        args=(action_value, operator_open_id, card_message_id),
        daemon=True,
    ).start()

    return {
        "toast": {"type": "info", "content": "正在创建服务群，请稍候..."},
        "processing_card": processing_card,
    }


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
    new_chat_id, err_code, err_msg = feishu_api.create_service_chat(
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
            f"本群由机器人自动创建，专门处理以下问题，请相关同学在此群进行沟通。\n\n"
            f"📝 以下是原始问题内容："
        )
        feishu_api.send_text_message(new_chat_id, welcome_text)

        # 4. 转发原始消息到服务群（保留图片等原始格式）
        if origin_message_id:
            feishu_api.forward_message(origin_message_id, new_chat_id)
        logger.info(f"服务群创建完成: new_chat_id={new_chat_id}")
    else:
        # 根据错误码生成友好提示
        friendly_msg = _get_friendly_error(err_code, err_msg)

        done_card = card_builder.build_done_card(
            asker_open_id=asker_open_id,
            handler_open_ids=handler_open_ids,
            question_preview=question_preview,
            error_msg=friendly_msg,
        )
        feishu_api.update_card_message(card_message_id, done_card)

        # 失败时从幂等集合中移除，允许重试
        with _lock:
            _processed_messages.discard(origin_message_id)

        logger.error(f"服务群创建失败: code={err_code}, msg={err_msg}")


def _get_friendly_error(code: int, msg: str) -> str:
    """根据飞书 API 错误码返回友好的中文提示"""
    # 权限相关
    if code == 99991672 or "Access denied" in msg or "scope" in msg.lower():
        return "当前用户暂未开通使用权限，已通知开发人员处理"
    # 用户不在机器人可见范围
    if code == 232043 or "invisible" in msg.lower() or "unavailable ids" in msg.lower():
        return "部分用户尚未开通该应用的使用权限，已通知开发人员处理"
    # 成员无效（open_id 无效或用户不存在）
    if code == 230001 or "invalid" in msg.lower():
        return "部分成员信息无效，请联系管理员检查配置"
    # 限频
    if code == 230020 or "rate" in msg.lower() or "频" in msg:
        return "操作过于频繁，请稍后再试"
    # 机器人能力未开启
    if code == 230006:
        return "机器人能力未启用，已通知开发人员处理"
    # 其他
    return f"创建失败（错误码: {code}），已通知开发人员处理"


def _toast(toast_type: str, content: str) -> dict:
    """构建飞书卡片 toast 响应"""
    return {
        "toast": {
            "type": toast_type,  # info / success / warning / error
            "content": content,
        }
    }
