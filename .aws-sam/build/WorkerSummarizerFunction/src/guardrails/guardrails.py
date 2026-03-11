"""
Guardrails Module
Pre-processing filter for prompt injection detection and PII masking.
Applied at the Broker level before tasks reach expensive worker agents.
"""

import re
import os
import json
import boto3
from aws_lambda_powertools import Logger

logger = Logger(service="agentic-mesh", child=True)

bedrock_runtime = boto3.client("bedrock-runtime")

# ── PII Patterns ─────────────────────────────────────────────────────
PII_PATTERNS = {
    "ssn": {
        "pattern": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "replacement": "[SSN_REDACTED]",
        "description": "US Social Security Number",
    },
    "email": {
        "pattern": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        "replacement": "[EMAIL_REDACTED]",
        "description": "Email Address",
    },
    "credit_card": {
        "pattern": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
        "replacement": "[CC_REDACTED]",
        "description": "Credit Card Number",
    },
    "phone_us": {
        "pattern": re.compile(
            r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
        ),
        "replacement": "[PHONE_REDACTED]",
        "description": "US Phone Number",
    },
    "ip_address": {
        "pattern": re.compile(
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
        ),
        "replacement": "[IP_REDACTED]",
        "description": "IP Address",
    },
}

# ── Prompt Injection Patterns ────────────────────────────────────────
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a\s+)?(?:new|different)", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"reveal\s+(?:your\s+)?(?:instructions|system|prompt)", re.IGNORECASE),
    re.compile(r"print\s+(?:your\s+)?(?:instructions|system|prompt)", re.IGNORECASE),
    re.compile(r"what\s+(?:are|is)\s+your\s+(?:system|initial)\s+(?:prompt|instructions)", re.IGNORECASE),
    re.compile(r"<\|(?:system|im_start|im_end)\|>", re.IGNORECASE),
    re.compile(r"```\s*system", re.IGNORECASE),
    re.compile(r"act\s+as\s+(?:if\s+)?(?:you\s+have\s+)?no\s+(?:restrictions|limits|rules)", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"DAN\s+mode", re.IGNORECASE),
    re.compile(r"do\s+anything\s+now", re.IGNORECASE),
]


def check_guardrails(text: str) -> dict:
    """
    Run all guardrail checks on the input text.

    Args:
        text: Raw user input to check.

    Returns:
        {
            "safe": bool,
            "sanitized_input": str,
            "violations": [{"type": str, "description": str}],
            "pii_found": [{"type": str, "count": int}],
        }
    """
    violations = []
    pii_found = []
    sanitized = text

    # ── Step 1: Check for prompt injection ────────────────────────
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            violations.append({
                "type": "prompt_injection",
                "description": f"Detected potential prompt injection: '{pattern.pattern}'",
            })

    # ── Step 2: Detect and sanitize PII ───────────────────────────
    for pii_type, config in PII_PATTERNS.items():
        matches = config["pattern"].findall(sanitized)
        if matches:
            pii_found.append({
                "type": pii_type,
                "description": config["description"],
                "count": len(matches),
            })
            sanitized = config["pattern"].sub(config["replacement"], sanitized)

    # ── Step 3: Determine safety ──────────────────────────────────
    has_injection = any(v["type"] == "prompt_injection" for v in violations)

    result = {
        "safe": not has_injection,
        "sanitized_input": sanitized,
        "violations": violations,
        "pii_found": pii_found,
    }

    logger.info(
        "Guardrail check complete",
        extra={
            "safe": result["safe"],
            "violation_count": len(violations),
            "pii_count": len(pii_found),
        },
    )

    return result


def apply_bedrock_guardrail(text: str, guardrail_id: str, guardrail_version: str = "DRAFT") -> dict:
    """
    Apply Amazon Bedrock Guardrails for additional safety checks.

    Args:
        text: Input text to check.
        guardrail_id: Bedrock guardrail resource ID.
        guardrail_version: Guardrail version (default: DRAFT).

    Returns:
        {"safe": bool, "action": str, "outputs": list}
    """
    try:
        response = bedrock_runtime.apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=guardrail_version,
            source="INPUT",
            content=[{"text": {"text": text}}],
        )

        action = response.get("action", "NONE")
        is_safe = action == "NONE"

        logger.info(
            "Bedrock guardrail applied",
            extra={
                "action": action,
                "safe": is_safe,
                "guardrail_id": guardrail_id,
            },
        )

        return {
            "safe": is_safe,
            "action": action,
            "outputs": response.get("outputs", []),
        }

    except Exception as e:
        logger.error("Bedrock guardrail check failed", extra={"error": str(e)})
        # Fail-open: if guardrail service is unavailable, allow the request
        return {"safe": True, "action": "ERROR", "outputs": []}
