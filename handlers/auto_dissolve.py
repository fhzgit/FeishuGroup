"""
服务群自动解散模块

流程：
  1. 服务群创建后注册到追踪器
  2. 后台线程每 30 秒检查各群最后消息时间
  3. 空闲超过 IDLE_THRESHOLD → 发送提醒消息
  4. 用户点击"确认解散" → 发送倒计时消息
  5. 倒计时结束 & 无新消息 → 归档 + 解散
  6. 兜底：提醒发出 24 小时后无操作 → 直接解散
"""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from services import feishu_api
from handlers.resolve_handler import handle_resolve

logger = logging.getLogger(__name__)

# ── 时间配置（秒）─ 测试阶段用短时间 ──────────────────────
IDLE_THRESHOLD = 5 * 60        # 空闲多久后发提醒（测试: 5分钟，生产: 30分钟）
COUNTDOWN_DURATION = 1 * 60    # 已读后倒计时多久解散（测试: 1分钟，生产: 3分钟）
FALLBACK_TIMEOUT = 24 * 3600   # 兜底：提醒发出后多久无操作直接解散（24小时）
CHECK_INTERVAL = 30            # 检查间隔（秒）

# ── 持久化文件路径 ────────────────────────────────────────
_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".dissolve_state.json"
)


class GroupState(Enum):
    """服务群状态"""
    ACTIVE = "active"               # 正常活跃
    WARNED = "warned"               # 已发提醒，等待确认
    COUNTDOWN = "countdown"         # 确认解散，倒计时中
    DISSOLVING = "dissolving"       # 正在解散


@dataclass
class ServiceGroup:
    """服务群追踪信息"""
    chat_id: str
    created_at: float = field(default_factory=time.time)
    last_message_time: float = field(default_factory=time.time)
    state: GroupState = GroupState.ACTIVE
    warning_msg_id: Optional[str] = None      # 提醒消息 ID
    warning_sent_at: Optional[float] = None    # 提醒发送时间
    countdown_start: Optional[float] = None    # 倒计时开始时间
    countdown_msg_id: Optional[str] = None     # 倒计时消息 ID
    archive_done: bool = False                 # 归档是否已完成（点击确认时即归档）


# ── 全局追踪器 ────────────────────────────────────────────
_groups: dict[str, ServiceGroup] = {}
_lock = threading.Lock()
_checker_started = False


# ── 持久化：保存 / 加载 ────────────────────────────────────
def _save_state() -> None:
    """将当前所有服务群状态序列化到文件（需在 _lock 内调用或调用时已加锁）"""
    try:
        data = {}
        for chat_id, g in _groups.items():
            data[chat_id] = {
                "created_at": g.created_at,
                "last_message_time": g.last_message_time,
                "state": g.state.value,
                "warning_msg_id": g.warning_msg_id,
                "warning_sent_at": g.warning_sent_at,
                "countdown_start": g.countdown_start,
                "countdown_msg_id": g.countdown_msg_id,
                "archive_done": g.archive_done,
            }
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[AutoDissolve] 状态保存失败: {e}")


def _load_state() -> None:
    """从文件恢复服务群状态（进程启动时调用）"""
    if not os.path.exists(_STATE_FILE):
        return
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        for chat_id, d in data.items():
            state = GroupState(d.get("state", "active"))
            # 跳过"正在解散"的历史残留
            if state == GroupState.DISSOLVING:
                continue
            g = ServiceGroup(
                chat_id=chat_id,
                created_at=d.get("created_at", now),
                last_message_time=d.get("last_message_time", now),
                state=state,
                warning_msg_id=d.get("warning_msg_id"),
                warning_sent_at=d.get("warning_sent_at"),
                countdown_start=d.get("countdown_start"),
                countdown_msg_id=d.get("countdown_msg_id"),
                archive_done=d.get("archive_done", False),
            )
            # 倒计时状态特殊处理：若倒计时已经超时，直接触发解散；否则保留继续跑
            if state == GroupState.COUNTDOWN and g.countdown_start:
                elapsed = now - g.countdown_start
                if elapsed >= COUNTDOWN_DURATION:
                    logger.info(
                        f"[AutoDissolve] 恢复: 群 {chat_id} 倒计时在重启前已超时"
                        f"({elapsed:.0f}s)，将立即触发解散"
                    )
                    # 启动后由 _check_countdown 在下一个检查周期处理
                    g.countdown_start = now - COUNTDOWN_DURATION  # 让下次检查立即生效
            _groups[chat_id] = g
            logger.info(f"[AutoDissolve] 恢复服务群状态: {chat_id} -> {state.value}")
    except Exception as e:
        logger.warning(f"[AutoDissolve] 状态加载失败: {e}")



def register_service_group(chat_id: str) -> None:
    """注册新建的服务群到追踪器"""
    with _lock:
        _groups[chat_id] = ServiceGroup(chat_id=chat_id)
        _save_state()
    logger.info(f"[AutoDissolve] 注册服务群: {chat_id}")


def on_message_received(chat_id: str) -> None:
    """
    服务群收到新消息时调用，更新最后消息时间。
    如果处于倒计时阶段，取消解散。
    """
    with _lock:
        group = _groups.get(chat_id)
        if not group:
            return
        group.last_message_time = time.time()

        if group.state == GroupState.COUNTDOWN:
            # 倒计时期间有新消息 → 取消解散，回到活跃状态
            logger.info(f"[AutoDissolve] 倒计时期间收到新消息，取消解散: {chat_id}")
            group.state = GroupState.ACTIVE
            group.warning_msg_id = None
            group.warning_sent_at = None
            group.countdown_start = None
            _save_state()
            feishu_api.send_text_message(
                chat_id,
                "📝 检测到新消息，已取消自动解散。如问题已解决，"
                "请 @机器人 并发送「问题已解决」手动归档。"
            )

        elif group.state == GroupState.WARNED:
            # 提醒阶段有新消息 → 回到活跃状态
            logger.info(f"[AutoDissolve] 提醒阶段收到新消息，重置计时器: {chat_id}")
            group.state = GroupState.ACTIVE
            group.warning_msg_id = None
            group.warning_sent_at = None
            _save_state()


def on_dissolve_action(action: str, chat_id: str, operator_name: str, message_id: str) -> None:
    """处理空闲提醒卡片的按钮点击"""
    with _lock:
        group = _groups.get(chat_id)
        
        # 兼容程序热重启后内存被清空的情况：如果不存在，重新把它加回追踪列表
        if not group:
            logger.info(f"[AutoDissolve] 内存态未找到群 {chat_id}，主动恢复其状态")
            group = ServiceGroup(chat_id=chat_id, state=GroupState.WARNED, warning_msg_id=message_id)
            _groups[chat_id] = group
            
        if group.state != GroupState.WARNED:
            logger.warning(f"[AutoDissolve] 忽略无效应答 (当前群不在WARN状态): action={action}, group_state={group.state}")
            return

        if action == "confirm_dissolve":
            group.state = GroupState.COUNTDOWN
            group.countdown_start = time.time()
            _save_state()
            logger.info(f"[AutoDissolve] 群 {chat_id} 确认解散，立即开始归档")

            # 立刻更新卡片为"整理中"状态
            from services import card_builder
            card_json = card_builder.build_idle_countdown_card(0, operator_name, archiving=True)
            feishu_api.update_card_message(message_id, card_json)

            # 启动异步归档线程（归档完成后再次更新卡片）
            import threading as _threading
            _threading.Thread(
                target=_async_archive_and_update,
                args=(chat_id, message_id, operator_name),
                daemon=True,
                name=f"Archive-{chat_id}",
            ).start()

        elif action == "cancel_dissolve":
            group.state = GroupState.ACTIVE
            group.warning_msg_id = None
            group.warning_sent_at = None
            _save_state()
            logger.info(f"[AutoDissolve] 群 {chat_id} 取消解散，恢复活跃")
            from services import card_builder
            card_json = card_builder.build_cancel_dissolve_card(operator_name)
            feishu_api.update_card_message(message_id, card_json)


def _async_archive_and_update(chat_id: str, warning_msg_id: str, operator_name: str) -> None:
    """异步归档并实时更新卡片。用于用户点击『确认解散』后立即触发。"""
    from services import card_builder
    import config
    from datetime import datetime, timezone, timedelta
    import json

    _BJT = timezone(timedelta(hours=8))

    try:
        # 1. 获取群名称
        chat_name = feishu_api.get_chat_info(chat_id) or chat_id

        # 2. 获取全部历史消息
        messages = feishu_api.list_chat_messages(chat_id)
        msg_count = len(messages) if messages else 0
        logger.info(f"[AutoDissolve] 归档获取 {msg_count} 条消息: {chat_id}")

        if messages and config.BITABLE_APP_TOKEN and config.BITABLE_TABLE_ID:
            # 3. 拼接聊天记录文本
            from handlers.resolve_handler import _format_chat_log
            chat_log = _format_chat_log(messages)
            now = datetime.now(_BJT)
            fields = {
                "服务群名称": chat_name,
                "服务群ID": chat_id,
                "聊天记录": chat_log,
                "消息条数": str(msg_count),
                "归档时间": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            ok = feishu_api.create_bitable_record(
                config.BITABLE_APP_TOKEN,
                config.BITABLE_TABLE_ID,
                fields,
            )
            if not ok:
                logger.error(f"[AutoDissolve] 归档写入多维表格失败: {chat_id}")

        # 4. 归档完成，更新卡片为倒计时状态（显示消息数量）
        minutes = COUNTDOWN_DURATION // 60
        card_json = card_builder.build_idle_countdown_card(
            minutes, operator_name, archiving=False, msg_count=msg_count
        )
        feishu_api.update_card_message(warning_msg_id, card_json)
        logger.info(f"[AutoDissolve] 归档完成，卡片已更新: {msg_count} 条记录")
        # 标记归档已完成
        with _lock:
            g = _groups.get(chat_id)
            if g:
                g.archive_done = True
                _save_state()

    except Exception as e:
        logger.exception(f"[AutoDissolve] 归档异常: {e}")


def is_tracked_group(chat_id: str) -> bool:
    """检查是否为追踪中的服务群"""
    with _lock:
        return chat_id in _groups


def start_idle_checker() -> None:
    """启动后台空闲检查线程，同时从文件恢复状态"""
    global _checker_started
    if _checker_started:
        return
    _checker_started = True
    _load_state()  # 从持久化文件恢复服务群状态

    def _checker():
        while True:
            time.sleep(CHECK_INTERVAL)
            _check_idle_groups()

    t = threading.Thread(target=_checker, daemon=True, name="auto-dissolve-checker")
    t.start()
    logger.info(
        f"[AutoDissolve] 空闲检查器已启动 "
        f"(空闲阈值={IDLE_THRESHOLD}s, 倒计时={COUNTDOWN_DURATION}s, "
        f"检查间隔={CHECK_INTERVAL}s)"
    )


def _check_idle_groups() -> None:
    """检查所有追踪中的服务群"""
    now = time.time()

    with _lock:
        groups_snapshot = list(_groups.values())

    for group in groups_snapshot:
        try:
            if group.state == GroupState.ACTIVE:
                _check_active(group, now)
            elif group.state == GroupState.WARNED:
                _check_warned(group, now)
            elif group.state == GroupState.COUNTDOWN:
                _check_countdown(group, now)
        except Exception as e:
            logger.exception(f"[AutoDissolve] 检查群 {group.chat_id} 时异常: {e}")


def _check_active(group: ServiceGroup, now: float) -> None:
    """检查活跃状态的群：空闲超时 → 发提醒"""
    idle_seconds = now - group.last_message_time
    if idle_seconds < IDLE_THRESHOLD:
        return

    logger.info(
        f"[AutoDissolve] 群 {group.chat_id} 已空闲 {idle_seconds:.0f}s，发送提醒"
    )

    from services import card_builder
    card_json = card_builder.build_idle_warning_card(group.chat_id)
    msg_id = feishu_api.send_card_message(group.chat_id, card_json)

    with _lock:
        group.state = GroupState.WARNED
        group.warning_msg_id = msg_id
        group.warning_sent_at = now
        _save_state()


def _check_warned(group: ServiceGroup, now: float) -> None:
    """检查已提醒状态的群：兜底超时 → 直接解散"""
    if not group.warning_sent_at:
        return

    elapsed = now - group.warning_sent_at
    if elapsed < FALLBACK_TIMEOUT:
        return

    logger.info(
        f"[AutoDissolve] 群 {group.chat_id} 提醒已发出 {elapsed/3600:.1f}h 无人已读，"
        f"触发兜底解散"
    )
    _dissolve(group)


def _check_countdown(group: ServiceGroup, now: float) -> None:
    """检查倒计时状态的群：倒计时结束 & 无新消息 → 解散"""
    if not group.countdown_start:
        return

    elapsed = now - group.countdown_start
    if elapsed < COUNTDOWN_DURATION:
        return

    # 检查倒计时期间是否有新消息
    if group.last_message_time > group.countdown_start:
        logger.info(
            f"[AutoDissolve] 群 {group.chat_id} 倒计时期间有新消息，取消解散"
        )
        with _lock:
            group.state = GroupState.ACTIVE
            group.countdown_start = None
        return

    logger.info(
        f"[AutoDissolve] 群 {group.chat_id} 倒计时结束，无新消息，开始解散"
    )
    _dissolve(group)


def _dissolve(group: ServiceGroup) -> None:
    """执行解散：删除群聊。
    常规触发：倒计时结束（已归档）。
    兄底触发：24小时无任何互动，调用完整归档流程。
    """
    with _lock:
        if group.state == GroupState.DISSOLVING:
            return
        group.state = GroupState.DISSOLVING

    chat_id = group.chat_id
    logger.info(f"[AutoDissolve] 开始解散服务群: {chat_id}")

    # COUNTDOWN 倒计时结束（已归档）则直接删群；24小时兆底触发则先归档再删
    if group.archive_done:
        import time as _time
        _time.sleep(2)
        feishu_api.delete_chat(chat_id)
        logger.info(f"[AutoDissolve] 倒计时完成，群已删除: {chat_id}")
    else:
        # 24小时兆底：群里一直没有人确认，现在归档并删群
        handle_resolve(chat_id, "system_auto_dissolve")

    # 解散后从追踪器移除
    with _lock:
        _groups.pop(chat_id, None)
        _save_state()
