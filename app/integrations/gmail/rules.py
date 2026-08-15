"""User-configurable Gmail filtering and classification rules."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.integrations.gmail.client import GmailMessage
from app.llm.schemas import MessageClassification
from app.tasks.models import UserSettings

DEFAULT_CLASSIFICATION_THRESHOLD = 0.65
ALLOWED_RULE_CLASSIFICATIONS = {item.value for item in MessageClassification} | {"IGNORE"}


class GmailRuleConditions(BaseModel):
    """All populated fields must match for a rule to apply."""

    model_config = ConfigDict(extra="forbid")

    sender: str | None = None
    sender_domain: str | None = None
    subject_contains: str | list[str] | None = None
    body_contains: str | list[str] | None = None


class GmailClassificationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    priority: int = Field(default=0, ge=-10000, le=10000)
    conditions: GmailRuleConditions
    classification: str
    reason: str = Field(default="", max_length=500)

    @field_validator("classification")
    @classmethod
    def validate_classification(cls, value: str) -> str:
        normalized = value.upper().strip()
        if normalized not in ALLOWED_RULE_CLASSIFICATIONS:
            allowed = ", ".join(sorted(ALLOWED_RULE_CLASSIFICATIONS))
            raise ValueError(f"classification must be one of: {allowed}")
        return normalized


@dataclass(frozen=True)
class MatchedGmailRule:
    id: str
    classification: str
    reason: str


def gmail_config(settings: UserSettings) -> dict[str, Any]:
    config = (settings.extra or {}).get("gmail")
    return config if isinstance(config, dict) else {}


def classification_threshold(settings: UserSettings) -> float:
    value = gmail_config(settings).get(
        "classification_threshold", DEFAULT_CLASSIFICATION_THRESHOLD
    )
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return DEFAULT_CLASSIFICATION_THRESHOLD
    if not 0 <= threshold <= 1:
        return DEFAULT_CLASSIFICATION_THRESHOLD
    return threshold


def matched_classification_rule(
    message: GmailMessage, settings: UserSettings
) -> MatchedGmailRule | None:
    raw_rules = gmail_config(settings).get("classification_rules", [])
    if not isinstance(raw_rules, list):
        return None

    parsed_rules: list[GmailClassificationRule] = []
    for raw_rule in raw_rules:
        try:
            rule = GmailClassificationRule.model_validate(raw_rule)
        except Exception:
            # A malformed user rule must not stop Gmail polling.
            continue
        if rule.enabled and _matches_conditions(message, rule.conditions):
            parsed_rules.append(rule)

    if not parsed_rules:
        return None
    parsed_rules.sort(key=lambda item: item.priority, reverse=True)
    rule = parsed_rules[0]
    return MatchedGmailRule(
        id=rule.id,
        classification=rule.classification,
        reason=rule.reason,
    )


def _matches_conditions(message: GmailMessage, conditions: GmailRuleConditions) -> bool:
    populated = conditions.model_dump(exclude_none=True)
    if not populated:
        return False

    sender = (message.sender_email or "").casefold()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    subject = (message.subject or "").casefold()
    body = (message.text or "").casefold()

    if conditions.sender is not None and not fnmatch.fnmatch(
        sender, conditions.sender.casefold().strip()
    ):
        return False
    if conditions.sender_domain is not None and not fnmatch.fnmatch(
        domain, conditions.sender_domain.casefold().lstrip("@").strip()
    ):
        return False
    if conditions.subject_contains is not None and not _contains_any(
        subject, conditions.subject_contains
    ):
        return False
    if conditions.body_contains is not None and not _contains_any(body, conditions.body_contains):
        return False
    return True


def _contains_any(value: str, expected: str | list[str]) -> bool:
    values = [expected] if isinstance(expected, str) else expected
    return any(str(item).strip().casefold() in value for item in values if str(item).strip())
