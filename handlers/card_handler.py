"""
卡片按钮回调处理模块
处理用户点击部门按钮后的建群 / 拉人逻辑

核心逻辑：
  - 同一张卡片的同一个部门按钮，只创建一次群
  - 后续点击同一按钮的用户，直接拉入已有群
  - 防抖：同一用户 2 秒内重复点击忽略
"""
import json
import logging
import os
import threading
import time
from typing import Any, Optional

import config
from services import card_builder, feishu_api
from handlers import auto_dissolve

logger = logging.getLogger(__name__)

# ── 持久化建群缓存文件 ────────────────────────────────────
_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".group_cache.json"
)

# ── 已创建群缓存：(card_message_id, department) → chat_id ──
_group_cache: dict[tuple[str, str], str] = {}
# ── 防抖缓存：(card_message_id, department, operator_id) → 上次点击时间 ──
_click_timestamps: dict[tuple[str, str, str], float] = {}
# ── 正在创建中的锁：(card_message_id, department) → Event ──
_creating_events: dict[tuple[str, str], threading.Event] = {}
_lock = threading.Lock()

DEBOUNCE_SECONDS = 2.0  # 防抖间隔


def _load_group_cache() -> None:
    """从文件加载建群缓存"""
    global _group_cache
    if not os.path.exists(_CACHE_FILE):
        return
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # JSON key 是字符串，需还原为 tuple
        _group_cache = {tuple(k.split("|", 1)): v for k, v in raw.items()}
        logger.info(f"[CardHandler] 已恢复建群缓存: {len(_group_cache)} 条记录")
    except Exception as e:
        logger.warning(f"[CardHandler] 建群缓存加载失败: {e}")


def _save_group_cache() -> None:
    """将建群缓存写入文件（在 _lock 内调用）"""
    try:
        # tuple key 转成字符串存储
        raw = {f"{k[0]}|{k[1]}": v for k, v in _group_cache.items()}
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[CardHandler] 建群缓存保存失败: {e}")


# 模块加载时即恢复缓存
_load_group_cache()



def handle_card_action(
    action_value: dict[str, Any],
    operator_open_id: str,
    card_message_id: str = "",
) -> dict:
    """
    卡片按钮回调主处理函数（必须在 3 秒内返回）

    :param action_value:     按钮绑定的 value 数据
    :param operator_open_id: 点击按钮的用户 open_id
    :param card_message_id:  机器人发出的卡片消息 ID（用于后续更新）
    :return: 立即返回给飞书的响应体（含 toast）
    """
    action = action_value.get("action", "")
    
    if action in ["confirm_dissolve", "cancel_dissolve"]:
        chat_id = action_value.get("chat_id")
        threading.Thread(
            target=auto_dissolve.on_dissolve_action,
            args=(action, chat_id, f"<at id='{operator_open_id}'></at>", card_message_id),
            daemon=True
        ).start()
        return _toast("success", "已收到你的反馈")

    if action == "ai_solved":
        # 用户点击「AI 已解决」→ 异步更新卡片为绿色已解决状态
        ai_answer = action_value.get("ai_answer", "")

        def _update_solved():
            solved_card = card_builder.build_ai_solved_card(
                ai_answer=ai_answer,
                operator_open_id=operator_open_id,
            )
            feishu_api.update_card_message(card_message_id, solved_card)
            logger.info(f"[CardHandler] 已解决卡片已更新: msg_id={card_message_id}, user={operator_open_id}")

        threading.Thread(target=_update_solved, daemon=True).start()
        return _toast("success", "✅ 感谢您的反馈，问题已标记为已解决！")

    if action != "create_service_group":
        logger.warning(f"未知的卡片动作: {action}")
        return _toast("warning", "未知操作")

    department = action_value.get("department", "")
    department_name = action_value.get("department_name", "客服")
    cache_key = (card_message_id, department)
    click_key = (card_message_id, department, operator_open_id)

    # ── 防抖：同一用户 2 秒内重复点击忽略 ─────────────────
    with _lock:
        now = time.time()
        last_click = _click_timestamps.get(click_key, 0)
        if now - last_click < DEBOUNCE_SECONDS:
            logger.info(f"防抖忽略: {click_key}")
            return _toast("info", "操作过于频繁，请稍候...")
        _click_timestamps[click_key] = now

    # ── 检查是否已有群（之前已创建过）────────────────────
    with _lock:
        existing_chat_id = _group_cache.get(cache_key)

    if existing_chat_id:
        # 群已存在 → 直接拉人
        logger.info(f"群已存在，拉人入群: chat_id={existing_chat_id}, user={operator_open_id}")
        threading.Thread(
            target=_async_add_member,
            args=(existing_chat_id, operator_open_id, department_name),
            daemon=True,
        ).start()
        return _toast("success", f"正在将您拉入「{department_name}」服务群...")

    # ── 检查是否正在创建中（另一个人正在建群）──────────────
    with _lock:
        event = _creating_events.get(cache_key)
        if event is not None:
            # 正在创建中，等待完成后拉人
            logger.info(f"群正在创建中，等待后拉人: {cache_key}, user={operator_open_id}")
            threading.Thread(
                target=_async_wait_and_add,
                args=(cache_key, event, operator_open_id, department_name),
                daemon=True,
            ).start()
            return _toast("info", f"「{department_name}」服务群正在创建，稍后将拉您入群...")

        # 首次创建：设置 Event
        event = threading.Event()
        _creating_events[cache_key] = event

    # ── 首次点击 → 异步创建群 ─────────────────────────────
    ai_answer = action_value.get("ai_answer", "")

    threading.Thread(
        target=_async_create_group,
        args=(action_value, operator_open_id, card_message_id, cache_key, event),
        daemon=True,
    ).start()

    return _toast("info", f"正在联系「{department_name}」，请稍候...")


def _async_add_member(
    chat_id: str,
    operator_open_id: str,
    department_name: str,
) -> None:
    """拉用户入已有群"""
    success = feishu_api.add_chat_members(chat_id, [operator_open_id])
    if success:
        logger.info(f"用户 {operator_open_id} 已加入群 {chat_id}")
    else:
        logger.warning(f"拉人入群失败: user={operator_open_id}, chat={chat_id}")


def _async_wait_and_add(
    cache_key: tuple[str, str],
    event: threading.Event,
    operator_open_id: str,
    department_name: str,
) -> None:
    """等待群创建完成后拉人"""
    event.wait(timeout=30)  # 最多等 30 秒
    with _lock:
        chat_id = _group_cache.get(cache_key)
    if chat_id:
        feishu_api.add_chat_members(chat_id, [operator_open_id])
        logger.info(f"等待建群完成后拉人成功: user={operator_open_id}, chat={chat_id}")
    else:
        logger.error(f"等待建群超时或失败: {cache_key}, user={operator_open_id}")


def _async_create_group(
    action_value: dict[str, Any],
    operator_open_id: str,
    card_message_id: str,
    cache_key: tuple[str, str],
    event: threading.Event,
) -> None:
    """异步执行建群流程（在独立线程中运行，无 3 秒时间限制）"""
    asker_open_id: str = action_value.get("asker_open_id", "")
    handler_open_ids: list[str] = action_value.get("handler_open_ids", [])
    department_name: str = action_value.get("department_name", "客服")
    origin_message_id: str = action_value.get("origin_message_id", "")
    ai_answer: str = action_value.get("ai_answer", "")

    logger.info(
        f"开始创建服务群: dept={department_name}, "
        f"asker={asker_open_id}, handlers={handler_open_ids}"
    )

    # 合并群成员：提问者 + 点击者 + 该部门负责人（去重）
    members = list(dict.fromkeys([asker_open_id, operator_open_id, *handler_open_ids]))
    chat_name = f"{config.SERVICE_GROUP_PREFIX}-{department_name}"

    # ── 1. 创建服务群 ───────────────────────────────────────
    new_chat_id, err_code, err_msg = feishu_api.create_service_chat(
        chat_name=chat_name,
        user_open_ids=members,
    )

    if new_chat_id:
        # 缓存群 ID 并持久化
        with _lock:
            _group_cache[cache_key] = new_chat_id
            _save_group_cache()
            # 通知等待的线程
            event.set()
            _creating_events.pop(cache_key, None)

        # ── 2. 发送欢迎卡片 ─────────────────────────────────
        # 从 config 中获取该部门的图标
        dept_key = action_value.get("department", "")
        dept_config = config.DEPARTMENT_HANDLERS.get(dept_key, {})
        department_icon = dept_config.get("icon", "🏢")

        welcome_card = card_builder.build_welcome_card(
            department_name=department_name,
            department_icon=department_icon,
            asker_open_id=asker_open_id,
            handler_open_ids=handler_open_ids,
        )
        feishu_api.send_card_message(new_chat_id, welcome_card)

        # ── 3. 转发话题首条消息到服务群 ─────────────────────
        if origin_message_id:
            feishu_api.forward_message(origin_message_id, new_chat_id)

        # ── 4. 更新卡片为「已跟进解决」但保留按钮 ──────────
        done_card = card_builder.build_done_card(
            ai_answer=ai_answer,
            department_name=department_name,
            new_chat_id=new_chat_id,
            departments=config.DEPARTMENT_HANDLERS,
            asker_open_id=asker_open_id,
            origin_message_id=origin_message_id,
            origin_chat_id=action_value.get("origin_chat_id", ""),
        )
        feishu_api.update_card_message(card_message_id, done_card)

        # ── 5. 注册到自动解散追踪器 ──────────────────────────
        auto_dissolve.register_service_group(new_chat_id)
        logger.info(f"服务群创建完成: new_chat_id={new_chat_id}")

    else:
        # 建群失败：释放锁，允许重试
        with _lock:
            event.set()
            _creating_events.pop(cache_key, None)
            # 不缓存，允许下次重新创建

        friendly_msg = _get_friendly_error(err_code, err_msg)
        logger.error(f"服务群创建失败: code={err_code}, msg={err_msg}")


def _get_friendly_error(code: int, msg: str) -> str:
    """根据飞书 API 错误码返回友好中文提示"""
    if code == 99991672 or "Access denied" in msg or "scope" in msg.lower():
        return "当前应用暂无创建群聊权限，已通知开发人员处理"
    if code == 232043 or "invisible" in msg.lower() or "unavailable ids" in msg.lower():
        return "部分用户尚未开通该应用权限，已通知开发人员处理"
    if code == 230001 or "invalid" in msg.lower():
        return "部分成员信息无效，请联系管理员检查配置"
    if code == 230020 or "rate" in msg.lower():
        return "操作过于频繁，请稍后再试"
    if code == 230006:
        return "机器人能力未启用，已通知开发人员处理"
    return f"创建失败（错误码: {code}），已通知开发人员处理"


def _toast(toast_type: str, content: str) -> dict:
    return {"toast": {"type": toast_type, "content": content}}
