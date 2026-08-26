"""Feishu CLI adapter: normalized events, safe media, and bounded replies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any

from .config import BridgeConfig


logger = logging.getLogger("feishu-codex-bridge")
SUPPORTED_MESSAGE_TYPES = {"text", "post", "image", "file", "audio", "video", "media"}
RESOURCE_KEY_PATTERN = re.compile(r"\b(?:img|file)_[A-Za-z0-9_-]{3,}\b")
OUTBOUND_MARKER = re.compile(
    r"\[\[feishu-(image|file|audio|video):([^\]\r\n]+)\]\]", re.IGNORECASE
)
CMD_META_JSON_ESCAPES = {
    ord(character): f"\\u{ord(character):04x}" for character in "&<>^|%!()"
}
BLOCKED_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".com",
    ".cpl",
    ".dll",
    ".exe",
    ".hta",
    ".jar",
    ".js",
    ".jse",
    ".lnk",
    ".msi",
    ".msp",
    ".ps1",
    ".reg",
    ".scr",
    ".sys",
    ".url",
    ".vbe",
    ".vbs",
    ".wsf",
}
IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
VIDEO_EXTENSIONS = {".avi", ".mkv", ".mov", ".mp4", ".mpeg", ".webm"}
TERMINAL_REPLY_API_CODES = {"230011"}


@dataclass(frozen=True)
class MessageResource:
    kind: str
    path: Path
    size_bytes: int
    display_name: str


@dataclass(frozen=True)
class SessionMetadata:
    name: str
    chat_type: str
    chat_id: str
    user_open_id: str


@dataclass(frozen=True)
class ReplyResult:
    delivered: bool
    retryable: bool = True
    error_code: str = ""

    def __bool__(self) -> bool:
        return self.delivered


def command_for(executable: str, args: list[str]) -> list[str]:
    """Make Windows CLI script shims callable without shell interpolation."""

    if executable.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", executable, *args]
    if executable.lower().endswith(".ps1"):
        powershell = os.environ.get("FEISHU_BRIDGE_POWERSHELL", "powershell.exe")
        return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", executable, *args]
    return [executable, *args]


def run_command(
    executable: str,
    args: list[str],
    *,
    cwd: Path,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command_for(executable, args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def find_lark_cli() -> str | None:
    configured = os.environ.get("LARK_CLI", "").strip()
    candidates = [configured] if configured else []
    npm_root = Path(os.environ.get("APPDATA", "")) / "npm"
    candidates.extend(
        [
            shutil.which("lark-cli") or "",
            shutil.which("lark-cli.cmd") or "",
            str(npm_root / "lark-cli.cmd"),
            str(npm_root / "lark-cli.ps1"),
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def get_bot_open_id(lark_cli: str, config: BridgeConfig) -> str:
    result = run_command(
        lark_cli,
        ["auth", "status", "--json", "--verify"],
        cwd=config.project_root,
        timeout=20,
    )
    if result.returncode == 0:
        payload = _parse_json(result.stdout) or {}
        identities = payload.get("identities")
        if not isinstance(identities, dict) and isinstance(payload.get("data"), dict):
            identities = payload["data"].get("identities")
        bot = identities.get("bot") if isinstance(identities, dict) else None
        if not isinstance(bot, dict):
            bot = payload.get("bot")
        if not isinstance(bot, dict) and isinstance(payload.get("data"), dict):
            bot = payload["data"].get("bot")
        if isinstance(bot, dict):
            value = bot.get("openId") or bot.get("open_id") or bot.get("open_bot_id")
            if isinstance(value, str):
                return value
    logger.warning("could not verify Feishu bot identity")
    return os.environ.get("FEISHU_BOT_OPEN_ID", "").strip()


def _first_text(mapping: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept both lark-cli's flat event and Feishu's native event envelope."""

    normalized = dict(raw)
    header = raw.get("header")
    event = raw.get("event")
    if isinstance(header, dict):
        normalized.setdefault("event_id", header.get("event_id") or header.get("eventId"))
    if not isinstance(event, dict):
        return normalized
    message = event.get("message")
    if isinstance(message, dict):
        for key in (
            "message_id",
            "root_id",
            "parent_id",
            "thread_id",
            "chat_id",
            "chat_type",
            "message_type",
            "content",
            "mentions",
        ):
            if key in message:
                normalized[key] = message[key]
        # Native Feishu envelopes keep message content as a JSON string, while
        # lark-cli's flat event stream has already rendered content to the exact
        # user-visible text. Decode only the native form here so flat values such
        # as "456", "true", or literal JSON are never reinterpreted as JSON.
        native_content = normalized.get("content")
        if isinstance(native_content, str):
            try:
                decoded_content = json.loads(native_content)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(decoded_content, (dict, list)):
                    normalized["content"] = decoded_content
    sender = event.get("sender")
    if isinstance(sender, dict):
        sender_id = sender.get("sender_id")
        normalized["sender"] = sender
        sender_name = _first_text(sender, ("sender_name", "name", "display_name"))
        if sender_name:
            normalized["sender_name"] = sender_name
        if not sender_name:
            i18n_names = sender.get("sender_i18n_names")
            sender_name = _first_text(i18n_names, ("zh_cn", "en_us", "ja_jp"))
            if sender_name:
                normalized["sender_name"] = sender_name
        if isinstance(sender_id, dict):
            normalized["sender_id"] = (
                sender_id.get("open_id") or sender_id.get("user_id") or sender_id.get("union_id")
            )
    return normalized


def extract_sender_open_id(event: dict[str, Any]) -> str:
    for candidate in (
        event.get("sender_id"),
        event.get("sender_open_id"),
        event.get("open_id"),
        event.get("sender"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, dict):
            nested = candidate.get("sender_id")
            if isinstance(nested, dict):
                candidate = nested
            elif isinstance(nested, str) and nested.strip():
                return nested.strip()
            value = _first_text(candidate, ("open_id", "openId", "user_id", "userId", "id"))
            if value:
                return value
    return ""


def should_process(event: dict[str, Any], bot_open_id: str) -> bool:
    message_type = str(event.get("message_type") or "").lower()
    if message_type not in SUPPORTED_MESSAGE_TYPES:
        return False
    sender = extract_sender_open_id(event)
    if bot_open_id and sender == bot_open_id:
        return False
    chat_type = str(event.get("chat_type") or "")
    if chat_type == "p2p":
        return True
    if chat_type != "group":
        return False
    mentions = event.get("mentions")
    if not isinstance(mentions, list):
        return False
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        mention_id = _first_text(mention, ("id", "open_id", "openId", "user_id"))
        if bot_open_id and mention_id == bot_open_id:
            return True
    return False


def extract_message_text(event: dict[str, Any], bot_open_id: str = "") -> str:
    raw_content: Any = event.get("content")
    if isinstance(raw_content, str):
        # lark-cli pre-renders flat receive events. Parsing this text again loses
        # valid user messages that happen to be JSON scalars (for example 456).
        return raw_content.strip()
    parts: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            text = value.strip()
            if text:
                parts.append(text)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        tag = str(value.get("tag") or "").lower()
        if tag == "at":
            user_id = str(value.get("user_id") or value.get("open_id") or "")
            if bot_open_id and user_id == bot_open_id:
                return
        direct = value.get("text")
        if isinstance(direct, str) and direct.strip():
            parts.append(direct.strip())
        href = value.get("href")
        if tag == "a" and isinstance(href, str) and href.strip():
            parts.append(href.strip())
        for key in ("title", "content", "elements", "post", "zh_cn", "en_us", "ja_jp"):
            if key in value and value[key] is not direct:
                collect(value[key])

    collect(raw_content)
    return "\n".join(dict.fromkeys(parts)).strip()


def conversation_scope(event: dict[str, Any]) -> str:
    chat_type = str(event.get("chat_type") or "unknown")
    chat_id = str(event.get("chat_id") or "").strip()
    sender_id = extract_sender_open_id(event)
    if chat_type == "p2p":
        # Keep compatibility with the v1 session key while still falling back
        # to the sender when a client omits chat_id.
        return f"p2p:{chat_id or sender_id or 'unknown'}"
    if chat_type == "group":
        topic_id = str(event.get("thread_id") or event.get("root_id") or "").strip()
        if topic_id:
            return f"group:{chat_id or 'unknown'}:topic:{topic_id}"
        return f"group:{chat_id or 'unknown'}"
    message_id = str(event.get("message_id") or event.get("id") or "unknown")
    return f"message:{message_id}"


def event_identity(event: dict[str, Any]) -> tuple[str, str]:
    message_id = str(event.get("message_id") or event.get("id") or "").strip()
    event_id = str(event.get("event_id") or message_id).strip()
    return event_id, message_id


def resolve_session_metadata(
    lark_cli: str, event: dict[str, Any], config: BridgeConfig
) -> SessionMetadata:
    chat_type = str(event.get("chat_type") or "unknown")
    chat_id = str(event.get("chat_id") or "").strip()
    sender_id = extract_sender_open_id(event)
    if chat_type == "p2p":
        name = _first_text(event, ("sender_name", "user_name", "name"))
        message_id = str(event.get("message_id") or "")
        if message_id and not name:
            result = run_command(
                lark_cli,
                [
                    "im",
                    "+messages-mget",
                    "--message-ids",
                    message_id,
                    "--as",
                    "bot",
                    "--json",
                    "--no-reactions",
                ],
                cwd=config.project_root,
                timeout=20,
            )
            payload = _parse_json(result.stdout) if result.returncode == 0 else None
            messages = ((payload or {}).get("data") or {}).get("messages", [])
            if isinstance(messages, list) and messages and isinstance(messages[0], dict):
                sender = messages[0].get("sender")
                if isinstance(sender, dict):
                    sender_id = _first_text(sender, ("id", "open_id", "openId")) or sender_id
                    name = _first_text(sender, ("name", "display_name", "sender_name"))
                    if not name:
                        name = _first_text(
                            sender.get("sender_i18n_names"), ("zh_cn", "en_us", "ja_jp")
                        )
        return SessionMetadata(name or sender_id or "飞书用户", chat_type, chat_id, sender_id)

    group_name = _first_text(event, ("chat_name", "group_name"))
    if chat_id and not group_name:
        result = run_command(
            lark_cli,
            ["im", "chats", "get", "--chat-id", chat_id, "--as", "bot", "--json"],
            cwd=config.project_root,
            timeout=20,
        )
        payload = _parse_json(result.stdout) if result.returncode == 0 else None
        data = (payload or {}).get("data")
        group_name = _first_text(data, ("name", "chat_name"))
    topic_suffix = "·话题" if event.get("thread_id") or event.get("root_id") else ""
    name = f"群聊·{group_name or chat_id or '未知群聊'}{topic_suffix}"
    return SessionMetadata(name, chat_type, chat_id, sender_id)


def _resource_refs(event: dict[str, Any], limit: int) -> list[tuple[str, str]]:
    raw = event.get("content")
    searchable = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    message_type = str(event.get("message_type") or "file").lower()
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in RESOURCE_KEY_PATTERN.findall(searchable):
        if key in seen:
            continue
        seen.add(key)
        kind = "image" if key.startswith("img_") else message_type
        if kind not in {"image", "audio", "video"}:
            kind = "file"
        refs.append((key, kind))
    return refs[:limit]


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:80] or "resource"


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _inbox_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def cleanup_inbox(config: BridgeConfig) -> None:
    cutoff = time.time() - config.resource_ttl_hours * 3600
    root = config.inbox_dir
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            continue


def _allowed_resource(path: Path, kind: str, size: int, config: BridgeConfig) -> bool:
    extension = path.suffix.lower()
    if extension in BLOCKED_EXTENSIONS:
        return False
    limit = config.max_image_bytes if kind == "image" else config.max_file_bytes
    if size <= 0 or size > limit:
        return False
    if kind == "image" and extension not in IMAGE_EXTENSIONS:
        return False
    if kind == "audio" and extension not in AUDIO_EXTENSIONS:
        return False
    if kind == "video" and extension not in VIDEO_EXTENSIONS:
        return False
    return True


def download_message_resources(
    lark_cli: str,
    event: dict[str, Any],
    scope: str,
    config: BridgeConfig,
) -> list[MessageResource]:
    if not config.download_resources:
        return []
    event_id, message_id = event_identity(event)
    if not message_id:
        return []
    refs = _resource_refs(event, config.max_message_resources)
    if not refs:
        return []
    cleanup_inbox(config)
    inbox_root = config.inbox_dir
    if not _within(inbox_root, config.project_root):
        logger.error("resource inbox must be inside the project root")
        return []
    scope_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target_dir = inbox_root / scope_hash / day
    target_dir.mkdir(parents=True, exist_ok=True)
    retained_bytes = _inbox_bytes(inbox_root)
    resources: list[MessageResource] = []

    for index, (file_key, kind) in enumerate(refs):
        stem = target_dir / f"{_safe_component(message_id)}-{index}-{hashlib.sha256(file_key.encode()).hexdigest()[:12]}"
        before = set(target_dir.glob(stem.name + "*"))
        try:
            relative_output = stem.relative_to(config.project_root).as_posix()
        except ValueError:
            continue
        download_type = "image" if kind == "image" else "file"
        result = run_command(
            lark_cli,
            [
                "im",
                "+messages-resources-download",
                "--message-id",
                message_id,
                "--file-key",
                file_key,
                "--type",
                download_type,
                "--output",
                relative_output,
                "--as",
                "bot",
            ],
            cwd=config.project_root,
            timeout=config.resource_download_timeout_seconds,
        )
        if result.returncode != 0:
            logger.warning("Feishu resource download failed event=%s", event_id[:24])
            continue
        candidates = [
            path
            for path in target_dir.glob(stem.name + "*")
            if path not in before and path.is_file()
        ]
        if not candidates and stem.exists():
            candidates = [stem]
        if not candidates:
            logger.warning("Feishu resource download returned no local file")
            continue
        try:
            candidate = max(candidates, key=lambda path: path.stat().st_mtime)
        except OSError:
            logger.warning("Feishu resource download returned an unreadable local file")
            continue
        try:
            resolved = candidate.resolve(strict=True)
            size = resolved.stat().st_size
        except OSError:
            continue
        if not _within(resolved, target_dir) or not _allowed_resource(resolved, kind, size, config):
            try:
                resolved.unlink()
            except OSError:
                pass
            logger.warning("discarded unsafe or oversized Feishu resource")
            continue
        if retained_bytes + size > config.max_total_resource_bytes:
            try:
                resolved.unlink()
            except OSError:
                pass
            logger.warning("Feishu resource inbox quota reached")
            continue
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()[:24]
        extension = resolved.suffix.lower()
        final_path = resolved.with_name(f"{digest}{extension}")
        try:
            if final_path.exists() and final_path != resolved:
                resolved.unlink()
            elif final_path != resolved:
                resolved.replace(final_path)
        except OSError:
            final_path = resolved
        retained_bytes += size
        resources.append(MessageResource(kind, final_path, size, final_path.name))
    if resources:
        logger.info("downloaded Feishu resources count=%s", len(resources))
    return resources


def build_turn_material(
    event: dict[str, Any], resources: list[MessageResource], bot_open_id: str
) -> tuple[str, list[Path], list[Path], str]:
    text = extract_message_text(event, bot_open_id)
    if not text:
        text = "（飞书用户本轮仅发送了附件。）"
    images = [resource.path for resource in resources if resource.kind == "image"]
    audio = [resource.path for resource in resources if resource.kind == "audio"]
    file_lines: list[str] = []
    for resource in resources:
        if resource.kind in {"image", "audio"}:
            continue
        file_lines.append(
            f"类型：{resource.kind}\n文件名：{resource.display_name}\n本地只读路径：{resource.path}"
        )
    return text, images, audio, "\n\n---\n\n".join(file_lines)


def split_reply(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n{2,}", text):
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > max_chars:
            split_at = paragraph.rfind("\n", 0, max_chars)
            if split_at < max_chars // 3:
                split_at = paragraph.rfind("。", 0, max_chars)
            if split_at < max_chars // 3:
                split_at = max_chars
            else:
                split_at += 1
            chunks.append(paragraph[:split_at].rstrip())
            paragraph = paragraph[split_at:].lstrip()
        current = paragraph
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _safe_outbound_path(raw_path: str, kind: str, config: BridgeConfig) -> Path | None:
    candidate = Path(raw_path.strip().strip('"\''))
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    try:
        resolved = (config.project_root / candidate).resolve(strict=True)
    except OSError:
        return None
    if not _within(resolved, config.project_root) or not resolved.is_file():
        return None
    try:
        size = resolved.stat().st_size
    except OSError:
        return None
    if not _allowed_resource(resolved, kind, size, config):
        return None
    return resolved


def _reply_ok(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode != 0:
        return False
    payload = _parse_json(result.stdout)
    if payload is None:
        # Some lark-cli versions return an empty stdout on a successful write.
        # The process exit code is the authoritative signal in that case.
        return True
    if "ok" in payload:
        return bool(payload.get("ok"))
    code = payload.get("code")
    if code is not None:
        return code in {0, "0"}
    if payload.get("success") is not None:
        return bool(payload.get("success"))
    return True


def _reply_api_error_code(result: subprocess.CompletedProcess[str]) -> str:
    for raw in (result.stdout, result.stderr):
        payload = _parse_json(str(raw or "").strip())
        if payload is None:
            continue
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else payload.get("code")
        if code is not None:
            return str(code).strip()
    return ""


def _markdown_post_content(text: str) -> str:
    """Build a multi-row Feishu post without relying on CLI newline conversion.

    lark-cli 1.0.80 wraps all Markdown lines in one ``md`` node. Some Feishu
    clients/tenants persist only the first line of that node. One post row per
    source line keeps every line visible while retaining inline Markdown where
    Feishu supports it. Fenced-code lines use plain text so unmatched fences in
    separate rows cannot hide their contents. JSON-escape Windows CMD control
    characters because npm's ``lark-cli.CMD`` shim otherwise interprets reply
    text such as ``<project>`` or Markdown tables as shell syntax. Feishu's JSON
    parser restores the original characters before rendering.
    """

    rows: list[list[dict[str, str]]] = []
    in_code_fence = False
    previous_blank = False
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for raw_line in normalized.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            if rows and not previous_blank:
                rows.append([{"tag": "text", "text": " "}])
            previous_blank = True
            continue

        previous_blank = False
        if stripped.startswith("```"):
            rows.append([{"tag": "text", "text": raw_line}])
            in_code_fence = not in_code_fence
            continue
        tag = "text" if in_code_fence else "md"
        rows.append([{"tag": tag, "text": raw_line}])

    while rows and rows[-1] == [{"tag": "text", "text": " "}]:
        rows.pop()
    if not rows:
        rows = [[{"tag": "text", "text": "本轮已完成，但没有可发送的正文。"}]]
    payload = json.dumps(
        {"zh_cn": {"content": rows}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return payload.translate(CMD_META_JSON_ESCAPES)


def reply_to_message(
    lark_cli: str,
    event: dict[str, Any],
    answer: str,
    config: BridgeConfig,
    *,
    idempotency_namespace: str = "final",
) -> ReplyResult:
    event_id, message_id = event_identity(event)
    if not message_id:
        return ReplyResult(False)
    attachments: list[tuple[str, Path, Path | None]] = []

    def remove_marker(match: re.Match[str]) -> str:
        kind = match.group(1).lower()
        raw_value = match.group(2).strip()
        raw_path = raw_value
        raw_cover = ""
        if kind == "video" and "|cover=" in raw_value:
            raw_path, raw_cover = raw_value.split("|cover=", 1)
        path = _safe_outbound_path(raw_path, kind, config)
        if path is None:
            logger.warning("ignored unsafe outbound attachment marker")
            return ""
        cover: Path | None = None
        if kind == "video":
            if not raw_cover:
                logger.warning("video marker has no cover; sending it as a file attachment")
                kind = "file"
            else:
                cover = _safe_outbound_path(raw_cover, "image", config)
                if cover is None:
                    logger.warning("video marker has an unsafe or missing cover; sending it as a file attachment")
                    kind = "file"
        if kind == "audio" and path.suffix.lower() not in {".opus", ".ogg"}:
            logger.info("non-Opus audio marker is sent as a file attachment")
            kind = "file"
        attachments.append((kind, path, cover if kind == "video" else None))
        return ""

    visible = OUTBOUND_MARKER.sub(remove_marker, answer).strip()
    pieces: list[tuple[str, str, Path | None]] = []
    for chunk in split_reply(visible, config.max_reply_chars):
        pieces.append((config.reply_format, chunk, None))
    for kind, path, cover in attachments:
        pieces.append((kind, path.relative_to(config.project_root).as_posix(), cover))
    if not pieces:
        pieces = [(config.reply_format, "本轮已完成，但没有可发送的正文。", None)]

    for index, (kind, value, cover) in enumerate(pieces):
        idempotency = hashlib.sha256(
            f"{event_id}:{idempotency_namespace}:{index}".encode("utf-8")
        ).hexdigest()[:40]
        reply_args = [
            "im",
            "+messages-reply",
            "--message-id",
            message_id,
        ]
        if kind == "markdown":
            reply_args.extend(
                ["--msg-type", "post", "--content", _markdown_post_content(value)]
            )
        else:
            flag = "--text" if kind == "text" else f"--{kind}"
            reply_args.extend([flag, value])
        if cover is not None:
            reply_args.extend([
                "--video-cover",
                cover.relative_to(config.project_root).as_posix(),
            ])
        reply_args.extend(["--as", "bot", "--idempotency-key", idempotency])
        if event.get("thread_id") or event.get("root_id"):
            reply_args.append("--reply-in-thread")
        result = run_command(
            lark_cli,
            reply_args,
            cwd=config.project_root,
            timeout=60,
        )
        if not _reply_ok(result):
            error_code = _reply_api_error_code(result)
            retryable = error_code not in TERMINAL_REPLY_API_CODES
            logger.error(
                "Feishu reply failed part=%s code=%s stderr=%s",
                index + 1,
                error_code or "unknown",
                result.stderr[-800:],
            )
            return ReplyResult(False, retryable=retryable, error_code=error_code)
    logger.info("replied to Feishu message parts=%s", len(pieces))
    return ReplyResult(True, retryable=False)


class LarkEventConsumer:
    """One lark-cli NDJSON consumer process; callers own reconnect policy."""

    def __init__(self, lark_cli: str, config: BridgeConfig) -> None:
        self.lark_cli = lark_cli
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.output: Queue[str | None] = Queue()
        self._ready_event = threading.Event()

    def start(self) -> None:
        self.close()
        output: Queue[str | None] = Queue()
        startup: dict[str, Any] = {
            "ready": threading.Event(),
            "error": "",
        }
        process = subprocess.Popen(
            command_for(
                self.lark_cli,
                ["event", "consume", self.config.event_key, "--as", "bot"],
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(self.config.project_root),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.output = output
        self.process = process
        self._ready_event = startup["ready"]
        threading.Thread(
            target=self._read_stdout,
            args=(process, output, startup),
            name="feishu-event-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(process, startup),
            name="feishu-event-stderr",
            daemon=True,
        ).start()

        ready = startup["ready"].wait(self.config.event_ready_timeout_seconds)
        if not ready or startup["error"]:
            detail = startup["error"] or (
                "event consumer did not emit the ready marker within "
                f"{self.config.event_ready_timeout_seconds}s"
            )
            startup["error"] = detail
            startup["ready"].set()
            self.close()
            raise OSError(detail)
        logger.info("Feishu event consumer ready event_key=%s", self.config.event_key)

    def _read_stdout(
        self,
        process: subprocess.Popen[str],
        output: Queue[str | None],
        startup: dict[str, Any],
    ) -> None:
        if not startup["ready"].wait(self.config.event_ready_timeout_seconds + 1):
            return
        if startup["error"]:
            output.put(None)
            return
        stream = process.stdout
        if stream is None:
            output.put(None)
            return
        try:
            for line in iter(stream.readline, ""):
                output.put(line)
        finally:
            output.put(None)

    def _read_stderr(
        self,
        process: subprocess.Popen[str],
        startup: dict[str, Any],
    ) -> None:
        stream = process.stderr
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                text = line.rstrip()
                if text:
                    if text.startswith("[event] ready "):
                        startup["ready"].set()
                    elif text.startswith("{"):
                        try:
                            payload = json.loads(text)
                        except json.JSONDecodeError:
                            payload = None
                        error = payload.get("error") if isinstance(payload, dict) else None
                        if isinstance(error, dict):
                            detail = str(
                                error.get("message") or error.get("hint") or "event consumer error"
                            )
                            missing_scopes = error.get("missing_scopes")
                            if isinstance(missing_scopes, list) and missing_scopes:
                                detail += "; missing_scopes=" + ",".join(
                                    str(scope) for scope in missing_scopes[:20]
                                )
                            startup["error"] = detail
                            startup["ready"].set()
                    logger.info("event-consumer %s", text)
        except (OSError, ValueError):
            pass

    def get(self, timeout: float) -> dict[str, Any] | None:
        try:
            line = self.output.get(timeout=timeout)
        except Empty:
            return None
        if line is None:
            raise EOFError("Feishu event consumer closed")
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return None
        return normalize_event(raw) if isinstance(raw, dict) else None

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def is_ready(self) -> bool:
        return self.is_alive() and self._ready_event.is_set()

    def close(self) -> None:
        process = self.process
        self.process = None
        if not process:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
