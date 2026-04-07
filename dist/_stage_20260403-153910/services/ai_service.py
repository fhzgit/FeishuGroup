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
from typing import Iterable

import requests

import config

logger = logging.getLogger(__name__)

# ── Aily 配置（直接写在这里方便测试，后续可移入 .env）──────
AILY_APP_ID = "cli_a92f882f35b89bd9"
AILY_APP_SECRET = "d7fECgvDkAZlwFLtjiDXfdaSniv1AXKv"
AILY_BOT_ID = "spring_49c68ad746__c"

HOST = "https://open.feishu.cn"
REQUEST_TIMEOUT = 20


def _get_tenant_access_token(app_id: str, app_secret: str, label: str) -> str:
    """获取指定应用的 Tenant Access Token"""
    url = f"{HOST}/open-apis/auth/v3/tenant_access_token/internal"
    try:
        resp = requests.post(
            url,
            json={
                "app_id": app_id,
                "app_secret": app_secret,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        logger.error(f"获取 {label} TAT 异常: {e}")
        return ""

    try:
        data = resp.json() if resp.content else {}
    except Exception:
        logger.error(f"获取 {label} TAT 失败：非 JSON 响应，status={resp.status_code}")
        return ""

    if data.get("code") != 0:
        logger.error(f"获取 {label} TAT 失败: {data}")
        return ""
    token = data.get("tenant_access_token", "")
    logger.info(f"{label} TAT 获取成功: {token[:20]}...")
    return token


def _get_aily_tenant_access_token() -> str:
    return _get_tenant_access_token(AILY_APP_ID, AILY_APP_SECRET, "Aily")


def _get_bot_tenant_access_token() -> str:
    return _get_tenant_access_token(config.APP_ID, config.APP_SECRET, "Bot")


def _download_image_from_message(message_id: str, image_key: str) -> bytes:
    """
    通过飞书消息资源接口下载图片二进制
    接口: GET /open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=image
    """
    token = _get_bot_tenant_access_token()
    if not token:
        return b""

    url = f"{HOST}/open-apis/im/v1/messages/{message_id}/resources/{image_key}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "image"},
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        logger.error(f"下载图片失败（请求异常）: message_id={message_id}, image_key={image_key}, err={e}")
        return b""

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text[:500]}
        logger.error(
            f"下载图片失败（JSON 响应）: message_id={message_id}, image_key={image_key}, data={data}"
        )
        return b""

    if resp.status_code >= 300:
        logger.error(
            f"下载图片失败（HTTP 状态）: message_id={message_id}, image_key={image_key}, status={resp.status_code}"
        )
        return b""

    return resp.content or b""


def _download_file_from_message(message_id: str, file_key: str) -> tuple[bytes, str]:
    """
    从飞书消息中下载文件（txt/doc/pdf 等非图片类型）
    返回: (file_bytes, file_name)，失败返回 (b"", "")
    """
    token = _get_bot_tenant_access_token()
    if not token:
        return b"", ""

    url = f"{HOST}/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "file"},
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        logger.error(f"下载文件失败: message_id={message_id}, file_key={file_key}, err={e}")
        return b"", ""

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if "application/json" in content_type:
        logger.error(f"下载文件失败（JSON响应）: file_key={file_key}, body={resp.text[:300]}")
        return b"", ""

    if resp.status_code >= 300:
        logger.error(f"下载文件失败（HTTP {resp.status_code}）: file_key={file_key}")
        return b"", ""

    # 从响应头获取文件名
    file_name = ""
    disposition = resp.headers.get("Content-Disposition", "")
    if "filename=" in disposition:
        file_name = disposition.split("filename=")[-1].strip().strip('"')
    if not file_name:
        file_name = file_key  # 备用文件名

    logger.info(f"文件下载成功: file_key={file_key}, name={file_name}, size={len(resp.content)}")
    return resp.content or b"", file_name


def _upload_aily_file(file_content: bytes, file_name: str, mime_type: str = "image/jpeg") -> tuple[str, str]:
    """
    上传文件到 Aily 专用文件接口（用于多模态对话）
    接口: POST /open-apis/aily/v1/files
    返回: (file_id, mime_type)，失败返回 ("", "")
    """
    token = _get_aily_tenant_access_token()
    if not token:
        return "", ""

    url = f"{HOST}/open-apis/aily/v1/files"
    headers = {"Authorization": f"Bearer {token}"}
    files = {"file": (file_name, file_content, mime_type)}

    try:
        resp = requests.post(url, headers=headers, files=files, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        logger.warning(f"Aily 文件上传请求失败: {e}")
        return "", ""

    try:
        data = resp.json() if resp.content else {}
    except Exception:
        logger.warning(f"Aily 文件上传 - 非 JSON 响应, status={resp.status_code}")
        return "", ""

    if data.get("code") != 0:
        logger.warning(f"Aily 文件上传失败: code={data.get('code')}, msg={data.get('msg', '')[:80]}")
        return "", ""

    file_info = ((data.get("data") or {}).get("files") or [{}])[0]
    file_id = file_info.get("id", "")
    returned_mime = file_info.get("mime_type", mime_type)
    if file_id:
        logger.info(f"Aily 文件上传成功: file_name={file_name}, file_id={file_id[:25]}...")
        return file_id, returned_mime

    logger.warning(f"Aily 文件上传成功但未返回 id: data={data}")
    return "", ""


def _prepare_aily_file_objects(
    message_id: str,
    image_keys: Iterable[str],
    file_keys: Iterable[str] | None = None,
) -> list[dict]:
    """
    下载并上传附件，产出 Aily 多模态文件对象列表。
    每个对象格式: {"id": file_id, "mime_type": "...", "is_image": True/False, "file_name": "..."}
    """
    file_objects: list[dict] = []
    seen_ids: set[str] = set()

    # 处理图片
    for idx, image_key in enumerate(image_keys, start=1):
        image_bytes = _download_image_from_message(message_id, image_key)
        if not image_bytes:
            logger.warning(f"跳过图片（下载失败）: image_key={image_key}")
            continue

        mime_type = "image/jpeg"
        if image_bytes[:4] == b"\x89PNG":
            mime_type = "image/png"
        elif image_bytes[:6] in (b"GIF87a", b"GIF89a"):
            mime_type = "image/gif"
        elif image_bytes[:4] == b"RIFF":
            mime_type = "image/webp"

        file_id, returned_mime = _upload_aily_file(image_bytes, f"image_{idx}.jpg", mime_type)
        if not file_id or file_id in seen_ids:
            logger.warning(f"跳过图片（上传失败或重复）: image_key={image_key}")
            continue

        seen_ids.add(file_id)
        file_objects.append({"id": file_id, "mime_type": returned_mime or mime_type, "is_image": True})

    # 处理文件（txt/doc/pdf 等）
    for idx, file_key in enumerate(file_keys or [], start=1):
        file_bytes, file_name = _download_file_from_message(message_id, file_key)
        if not file_bytes:
            logger.warning(f"跳过文件（下载失败）: file_key={file_key}")
            continue

        # 根据文件名判断 MIME
        ext = (file_name.rsplit(".", 1)[-1] if "." in file_name else "").lower()
        mime_map = {
            "txt": "text/plain", "csv": "text/csv", "html": "text/html",
            "pdf": "application/pdf",
            "doc": "application/msword",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "xls": "application/vnd.ms-excel",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ppt": "application/vnd.ms-powerpoint",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "json": "application/json", "md": "text/markdown",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")
        upload_name = file_name or f"file_{idx}.{ext or 'bin'}"

        file_id, returned_mime = _upload_aily_file(file_bytes, upload_name, mime_type)
        if not file_id or file_id in seen_ids:
            logger.warning(f"跳过文件（上传失败或重复）: file_key={file_key}")
            continue

        seen_ids.add(file_id)
        file_objects.append({
            "id": file_id,
            "mime_type": returned_mime or mime_type,
            "is_image": False,
            "file_name": upload_name,
        })
        logger.info(f"文件上传成功: {upload_name} -> Aily file_id={file_id[:20]}...")

    return file_objects


def _build_aily_content(question: str, file_objects: list[dict]) -> tuple[str, list[str]]:
    """
    构造 Aily CreateMessage 的 content（MDX 格式）和 file_ids 列表。

    官方文档格式（content_type 固定为 MDX）：
    - 图片: <Image imageKey="file_id"/>
    - 文件: <file fileKey="file_id"/>
    - file_ids 列表包含所有 file_id
    """
    text = (question or "").strip() or "用户未提供明确文本，请先澄清需求。"

    if not file_objects:
        return text, []

    tags = []
    for fo in file_objects:
        if fo.get("is_image"):
            tags.append(f'<Image imageKey="{fo["id"]}"/>')
        else:
            tags.append(f'<file fileKey="{fo["id"]}"/>')

    file_ids = [fo["id"] for fo in file_objects]
    return f"{text}{''.join(tags)}", file_ids


def _call_aily(question: str, file_objects: list[dict] | None = None) -> str:
    """
    调用 Aily OpenAPI 获取 AI 回答

    :param question: 用户提问文本
    :param file_objects: Aily 文件对象列表，格式 [{"id":"...","mime_type":"..."}]（可选）
    :return: AI 回答文本，失败返回空字符串
    """
    token = _get_aily_tenant_access_token()
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
        timeout=REQUEST_TIMEOUT,
    )
    data = resp.json()
    logger.info(f"CreateSession 响应: code={data.get('code')}")
    if data.get("code") != 0:
        logger.error(f"创建 Aily 会话失败: {data}")
        return ""
    session_id = data["data"]["session"]["id"]
    logger.info(f"Aily 会话创建成功: session_id={session_id}")

    # ── 2. 构造并发送消息（支持多模态 MDX 格式）────────────
    content, file_ids = _build_aily_content(question, file_objects or [])
    message_payload: dict = {
        "idempotent_id": str(uuid.uuid4()),
        "content_type": "MDX",
        "content": content,
    }
    if file_ids:
        message_payload["file_ids"] = file_ids
        logger.info(f"多模态消息: file_ids={file_ids}, content={content[:80]}")

    resp = requests.post(
        f"{HOST}/open-apis/aily/v1/sessions/{session_id}/messages",
        headers=headers,
        json=message_payload,
        timeout=REQUEST_TIMEOUT,
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
        timeout=REQUEST_TIMEOUT,
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
            timeout=REQUEST_TIMEOUT,
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
                timeout=REQUEST_TIMEOUT,
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
                timeout=REQUEST_TIMEOUT,
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


def generate_answer(
    question: str,
    msg_type: str = "text",
    message_id: str = "",
    image_keys: list[str] | None = None,
    file_keys: list[str] | None = None,
) -> str:
    """
    调用 Aily 获取 AI 回答，直接返回真实内容

    :param question:   用户提问的原始文本
    :param msg_type:   消息类型（text/image/file/post 等）
    :param message_id: 飞书消息 ID（用于下载图片/文件资源）
    :param image_keys: 飞书图片 key 列表
    :param file_keys:  飞书文件 key 列表（txt/doc/pdf 等）
    :return: Markdown 格式的 AI 回答文本，失败时返回友好提示
    """
    norm_image_keys = [k for k in (image_keys or []) if isinstance(k, str) and k]
    norm_file_keys = [k for k in (file_keys or []) if isinstance(k, str) and k]
    has_attachments = bool(norm_image_keys) or bool(norm_file_keys) or msg_type in ("image", "file")
    file_objects: list[dict] = []

    if (norm_image_keys or norm_file_keys) and message_id:
        logger.info(
            f"检测到附件输入，开始上传到 Aily: message_id={message_id}, "
            f"images={len(norm_image_keys)}, files={len(norm_file_keys)}"
        )
        try:
            file_objects = _prepare_aily_file_objects(message_id, norm_image_keys, norm_file_keys)
            logger.info(f"附件上传处理完成: uploaded={len(file_objects)}")
        except Exception as e:
            logger.exception(f"附件上传链路异常，降级为纯文本: {e}")

    # 纯文字降级提示（当有附件但上传失败时补充说明）
    question_text = (question or "").strip()
    if has_attachments and not file_objects and not question_text:
        question_text = "用户发送了附件，但系统暂未成功附带。请先提供通用排查建议，并提示用户补充文字描述。"
    elif has_attachments and not file_objects and question_text:
        question_text = f"{question_text}\n\n用户还发送了附件，但系统暂未成功附带。请先基于文字给出建议，并提示用户补充关键细节。"

    try:
        aily_result = _call_aily(question_text, file_objects=file_objects)
        if aily_result:
            # 保留 Aily 原始 Markdown（包括 ![...](url) 图片语法），
            # 由飞书端按 markdown 能力决定渲染方式。
            logger.info(f"✅ Aily 调用成功，回答内容: {aily_result[:100]}...")
            return aily_result
        else:
            return "对不起，AI 服务暂时未能返回结果，请稍后再试或联系人工客服。"
    except Exception as e:
        logger.exception(f"❌ Aily 调用异常: {e}")

    # Aily 失败时的兜底提示
    return (
        "抱歉，AI 智能回答暂时无法正常服务，请稍后再试。\n\n"
        "如需立即协助，请点击下方按钮联系对应团队的人工客服。"
    )
