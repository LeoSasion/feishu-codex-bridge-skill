"""Configuration and bounded defaults for the local bridge runtime."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Mapping


BRIDGE_VERSION = "4.2.0-alpha.64"

BOOLEAN_ENV_DEFAULTS = {
    "CODEX_BRIDGE_DOWNLOAD_RESOURCES": True,
}
ENUM_ENV_SPECS = {
    "CODEX_BRIDGE_ACCESS_MODE": ("locked", frozenset({"locked", "compat"})),
    "CODEX_BRIDGE_LIFECYCLE_MODE": ("hooks", frozenset({"hooks", "manual"})),
    # Plain text is the only default that can preserve the authoritative final
    # byte-for-character at the Bridge -> lark-cli argument boundary.
    # Markdown remains an explicit opt-in presentation transform.
    "CODEX_BRIDGE_REPLY_FORMAT": ("text", frozenset({"text", "markdown"})),
}
INTEGER_ENV_SPECS: dict[str, tuple[int, int, int | None]] = {
    "CODEX_BRIDGE_EVENT_READY_TIMEOUT": (15, 5, 120),
    "CODEX_BRIDGE_BEEPER_TIMEOUT": (3600, 30, 86400),
    "CODEX_BRIDGE_BEEPER_CLAIM_TTL": (7200, 60, 86400),
    "CODEX_BRIDGE_BEEPER_RETENTION_HOURS": (168, 1, 8760),
    "CODEX_BRIDGE_BEEPER_DIAL_TTL": (180, 60, 900),
    "CODEX_BRIDGE_BEEPER_GRACE_MAX_SECONDS": (30, 0, 60),
    "CODEX_BRIDGE_MAX_REPLY_CHARS": (2800, 500, 12000),
    "CODEX_BRIDGE_MAX_CONCURRENT_TURNS": (2, 1, 4),
    "CODEX_BRIDGE_RECONNECT_MAX_SECONDS": (30, 5, 300),
    "CODEX_BRIDGE_MAX_MESSAGE_RESOURCES": (9, 1, 20),
    "CODEX_BRIDGE_MAX_IMAGE_BYTES": (10 * 1024 * 1024, 1024, 2**63 - 1),
    "CODEX_BRIDGE_MAX_FILE_BYTES": (30 * 1024 * 1024, 1024, 2**63 - 1),
    "CODEX_BRIDGE_MAX_TOTAL_RESOURCE_BYTES": (100 * 1024 * 1024, 1024, 2**63 - 1),
    "CODEX_BRIDGE_RESOURCE_DOWNLOAD_TIMEOUT": (120, 10, 1800),
    "CODEX_BRIDGE_RESOURCE_TTL_HOURS": (168, 1, 8760),
    "CODEX_BRIDGE_LIFECYCLE_GRACE_SECONDS": (120, 15, 3600),
}

TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})


def _bool_env(name: str) -> bool:
    default = BOOLEAN_ENV_DEFAULTS[name]
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in TRUE_ENV_VALUES:
        return True
    if value in FALSE_ENV_VALUES:
        return False
    # load_config() performs strict central validation first. This private
    # coercer retains the safety-oriented default only as defense in depth.
    return default


def _int_env(name: str) -> int:
    default, minimum, maximum = INTEGER_ENV_SPECS[name]
    raw = os.environ.get(name, "").strip()
    if raw and re.fullmatch(r"-?[0-9]+", raw) is None:
        return default
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def _enum_env(name: str) -> str:
    default, choices = ENUM_ENV_SPECS[name]
    value = os.environ.get(name, default).strip().lower()
    return value if value in choices else default


def validate_bridge_env_values(values: Mapping[str, str]) -> tuple[str, ...]:
    """Return semantic errors for recognized values from one bridge.env file."""

    issues: list[str] = []
    for name in BOOLEAN_ENV_DEFAULTS:
        if name not in values:
            continue
        value = values[name].strip().lower()
        if value not in TRUE_ENV_VALUES | FALSE_ENV_VALUES:
            issues.append(f"Bridge environment value for {name} is not an explicit boolean")
    for name, (_, choices) in ENUM_ENV_SPECS.items():
        if name not in values:
            continue
        if values[name].strip().lower() not in choices:
            issues.append(f"Bridge environment value for {name} is not a supported choice")
    for name, (_, minimum, maximum) in INTEGER_ENV_SPECS.items():
        if name not in values:
            continue
        raw = values[name].strip()
        if re.fullmatch(r"-?[0-9]+", raw) is None:
            issues.append(f"Bridge environment value for {name} is not an integer")
            continue
        try:
            parsed = int(raw)
        except ValueError:
            issues.append(f"Bridge environment value for {name} is not an integer")
            continue
        if parsed < minimum or (maximum is not None and parsed > maximum):
            upper = str(maximum) if maximum is not None else "unbounded"
            issues.append(
                f"Bridge environment value for {name} is outside {minimum}..{upper}"
            )
    return tuple(issues)


def _csv_env(name: str) -> frozenset[str]:
    values = os.environ.get(name, "")
    return frozenset(part.strip() for part in values.split(",") if part.strip())


@dataclass(frozen=True)
class BridgeConfig:
    project_root: Path
    runtime_dir: Path
    event_key: str
    event_ready_timeout_seconds: int
    beeper_timeout_seconds: int
    beeper_claim_ttl_seconds: int
    beeper_retention_hours: int
    beeper_dial_ttl_seconds: int
    beeper_grace_max_seconds: int
    max_reply_chars: int
    max_concurrent_turns: int
    reconnect_max_seconds: int
    access_mode: str
    owner_open_id: str
    admin_open_ids: frozenset[str]
    allowed_user_open_ids: frozenset[str]
    allowed_chat_ids: frozenset[str]
    download_resources: bool
    max_message_resources: int
    max_image_bytes: int
    max_file_bytes: int
    max_total_resource_bytes: int
    resource_download_timeout_seconds: int
    resource_ttl_hours: int
    lifecycle_grace_seconds: int
    lifecycle_mode: str
    reply_format: str

    @property
    def state_db(self) -> Path:
        return self.runtime_dir / "state.sqlite3"

    @property
    def session_file(self) -> Path:
        return self.runtime_dir / "sessions.json"

    @property
    def health_file(self) -> Path:
        return self.runtime_dir / "health.json"

    @property
    def lease_dir(self) -> Path:
        return self.runtime_dir / "leases"

    @property
    def inbox_dir(self) -> Path:
        return self.runtime_dir / "inbox"

def load_config() -> BridgeConfig:
    semantic_issues = validate_bridge_env_values(os.environ)
    if semantic_issues:
        raise ValueError(semantic_issues[0])
    project_value = os.environ.get("CODEX_BRIDGE_PROJECT_ROOT", "").strip()
    project_root = Path(project_value).resolve() if project_value else Path.cwd().resolve()
    runtime_value = os.environ.get("CODEX_BRIDGE_RUNTIME_DIR", "").strip()
    runtime_dir = (
        Path(runtime_value).resolve()
        if runtime_value
        else project_root / ".codex" / "feishu-codex-bridge-runtime"
    )

    access_mode = _enum_env("CODEX_BRIDGE_ACCESS_MODE")
    lifecycle_mode = _enum_env("CODEX_BRIDGE_LIFECYCLE_MODE")
    reply_format = _enum_env("CODEX_BRIDGE_REPLY_FORMAT")

    return BridgeConfig(
        project_root=project_root,
        runtime_dir=runtime_dir,
        event_key=os.environ.get("CODEX_BRIDGE_EVENT_KEY", "im.message.receive_v1").strip(),
        event_ready_timeout_seconds=_int_env("CODEX_BRIDGE_EVENT_READY_TIMEOUT"),
        beeper_timeout_seconds=_int_env("CODEX_BRIDGE_BEEPER_TIMEOUT"),
        beeper_claim_ttl_seconds=_int_env("CODEX_BRIDGE_BEEPER_CLAIM_TTL"),
        beeper_retention_hours=_int_env("CODEX_BRIDGE_BEEPER_RETENTION_HOURS"),
        beeper_dial_ttl_seconds=_int_env("CODEX_BRIDGE_BEEPER_DIAL_TTL"),
        beeper_grace_max_seconds=_int_env("CODEX_BRIDGE_BEEPER_GRACE_MAX_SECONDS"),
        max_reply_chars=_int_env("CODEX_BRIDGE_MAX_REPLY_CHARS"),
        max_concurrent_turns=_int_env("CODEX_BRIDGE_MAX_CONCURRENT_TURNS"),
        reconnect_max_seconds=_int_env("CODEX_BRIDGE_RECONNECT_MAX_SECONDS"),
        access_mode=access_mode,
        owner_open_id=os.environ.get("CODEX_BRIDGE_OWNER_OPEN_ID", "").strip(),
        admin_open_ids=_csv_env("CODEX_BRIDGE_ADMIN_OPEN_IDS"),
        allowed_user_open_ids=_csv_env("CODEX_BRIDGE_ALLOWED_USER_OPEN_IDS"),
        allowed_chat_ids=_csv_env("CODEX_BRIDGE_ALLOWED_CHAT_IDS"),
        download_resources=_bool_env("CODEX_BRIDGE_DOWNLOAD_RESOURCES"),
        max_message_resources=_int_env("CODEX_BRIDGE_MAX_MESSAGE_RESOURCES"),
        max_image_bytes=_int_env("CODEX_BRIDGE_MAX_IMAGE_BYTES"),
        max_file_bytes=_int_env("CODEX_BRIDGE_MAX_FILE_BYTES"),
        max_total_resource_bytes=_int_env("CODEX_BRIDGE_MAX_TOTAL_RESOURCE_BYTES"),
        resource_download_timeout_seconds=_int_env("CODEX_BRIDGE_RESOURCE_DOWNLOAD_TIMEOUT"),
        resource_ttl_hours=_int_env("CODEX_BRIDGE_RESOURCE_TTL_HOURS"),
        lifecycle_grace_seconds=_int_env("CODEX_BRIDGE_LIFECYCLE_GRACE_SECONDS"),
        lifecycle_mode=lifecycle_mode,
        reply_format=reply_format,
    )
