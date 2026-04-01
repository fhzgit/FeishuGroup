"""
AI 内容生成服务模块
通过调用 Aily OpenAPI 生成回答内容，返回 Markdown 格式文本

调用链路：
  1. 获取 Tenant Access Token
  2. CreateSession（创建会话）
  3. CreateMessage（发送用户问题）
  4. CreateRun（触发 Bot 执行）
  5. 轮询 GetRun + ListMessages（获取 Bot 回复）
"""
import json
import logging
import time
import uuid
import requests

logger = logging.getLogger(__name__)

# ── Aily 配置（直接写在这里方便测试，后续可移入 .env）──────
AILY_APP_ID = "cli_a92f882f35b89bd9"
AILY_APP_SECRET = "d7fECgvDkAZlwFLtjiDXfdaSniv1AXKv"
AILY_BOT_ID = "spring_49c68ad746__c"

HOST = "https://open.feishu.cn"


def _get_tenant_access_token() -> str:
    """获取 Aily 应用的 Tenant Access Token"""
    url = f"{HOST}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": AILY_APP_ID,
        "app_secret": AILY_APP_SECRET,
    })
    data = resp.json()
    if data.get("code") != 0:
        logger.error(f"获取 Aily TAT 失败: {data}")
        return ""
    token = data.get("tenant_access_token", "")
    logger.info(f"Aily TAT 获取成功: {token[:20]}...")
    return token


def _call_aily(question: str) -> str:
    """
    调用 Aily OpenAPI 获取 AI 回答

    :param question: 用户提问文本
    :return: AI 回答文本，失败返回空字符串
    """
    token = _get_tenant_access_token()
    if not token:
        return ""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    # ── 1. 创建会话 ──────────────────────────────────────
    resp = requests.post(
        f"{HOST}/open-apis/aily/v1/sessions",
        headers=headers,
    )
    data = resp.json()
    logger.info(f"CreateSession 响应: code={data.get('code')}")
    if data.get("code") != 0:
        logger.error(f"创建 Aily 会话失败: {data}")
        return ""
    session_id = data["data"]["session"]["id"]
    logger.info(f"Aily 会话创建成功: session_id={session_id}")

    # ── 2. 发送用户消息 ──────────────────────────────────
    resp = requests.post(
        f"{HOST}/open-apis/aily/v1/sessions/{session_id}/messages",
        headers=headers,
        json={
            "idempotent_id": str(uuid.uuid4()),
            "content_type": "MDX",
            "content": question,
        },
    )
    data = resp.json()
    logger.info(f"CreateMessage 响应: code={data.get('code')}")
    if data.get("code") != 0:
        logger.error(f"发送 Aily 消息失败: {data}")
        return ""
    message_id = data["data"]["message"]["id"]
    logger.info(f"Aily 消息发送成功: message_id={message_id}")

    # ── 3. 触发 Bot 执行 ─────────────────────────────────
    resp = requests.post(
        f"{HOST}/open-apis/aily/v1/sessions/{session_id}/runs",
        headers=headers,
        json={"app_id": AILY_BOT_ID},
    )
    data = resp.json()
    logger.info(f"CreateRun 响应: code={data.get('code')}")
    if data.get("code") != 0:
        logger.error(f"触发 Aily Run 失败: {data}")
        return ""
    run_id = data["data"]["run"]["id"]
    logger.info(f"Aily Run 创建成功: run_id={run_id}")

    # ── 4. 轮询等待执行完成（最多 120 秒）────────────────
    ai_content = ""
    for i in range(600):  # 600 * 0.2s = 120s
        time.sleep(0.2)

        # 4.1 获取 Run 状态
        resp = requests.get(
            f"{HOST}/open-apis/aily/v1/sessions/{session_id}/runs/{run_id}",
            headers=headers,
        )
        run_data = resp.json()
        if run_data.get("code") != 0:
            logger.error(f"获取 Aily Run 状态失败: {run_data}")
            break

        status = run_data["data"]["run"]["status"]

        if status == "IN_PROGRESS":
            # 4.2 获取流式消息
            resp = requests.get(
                f"{HOST}/open-apis/aily/v1/sessions/{session_id}/messages",
                headers=headers,
                params={
                    "run_id": run_id,
                    "with_partial_message": "true",
                },
            )
            msg_data = resp.json()
            if msg_data.get("code") == 0:
                messages = msg_data.get("data", {}).get("messages", [])
                for msg in messages:
                    sender = msg.get("sender", {})
                    if sender.get("sender_type") == "ASSISTANT":
                        ai_content = msg.get("content", "")

        elif status == "COMPLETED":
            # 执行完成，获取最终消息
            resp = requests.get(
                f"{HOST}/open-apis/aily/v1/sessions/{session_id}/messages",
                headers=headers,
                params={"run_id": run_id},
            )
            msg_data = resp.json()
            if msg_data.get("code") == 0:
                messages = msg_data.get("data", {}).get("messages", [])
                for msg in messages:
                    sender = msg.get("sender", {})
                    if sender.get("sender_type") == "ASSISTANT":
                        ai_content = msg.get("content", "")
            logger.info(f"Aily Run 执行完成，AI 回答长度: {len(ai_content)}")
            break

        elif status == "FAILED":
            error = run_data["data"]["run"].get("error", {})
            logger.error(
                f"Aily Run 执行失败: code={error.get('code')}, "
                f"msg={error.get('message')}"
            )
            break

    return ai_content


def generate_answer(question: str, msg_type: str = "text") -> str:
    """
    调用 Aily 获取 AI 回答，直接返回真实内容

    :param question:  用户提问的原始文本
    :param msg_type:  消息类型（text/image/post 等）
    :return: Markdown 格式的 AI 回答文本，失败时返回友好提示
    """
    if msg_type == "image":
        question_text = "用户发送了一张图片，请问如何处理？"
    else:
        question_text = question

    try:
        aily_result = _call_aily(question_text)
        if aily_result:
            logger.info(f"✅ Aily 调用成功，回答内容: {aily_result[:100]}...")
            return aily_result
        else:
            logger.warning("⚠️ Aily 调用返回空内容，使用兜底提示")
    except Exception as e:
        logger.exception(f"❌ Aily 调用异常: {e}")

    # Aily 失败时的兜底提示
    return (
        "抱歉，AI 智能回答暂时无法正常服务，请稍后再试。\n\n"
        "如需立即协助，请点击下方按钮联系对应团队的人工客服。"
    )
