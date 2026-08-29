from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from docalign_core.domain.formatting_spec import FormattingSpec, default_cleanup_spec
from docalign_core.domain.rule_pack import (
    RulePackArtifact,
    canonical_formatting_spec_json,
    formatting_spec_sha256,
)
from pydantic import ValidationError


def test_rule_pack_digest_is_stable_across_set_and_json_round_trips() -> None:
    spec = default_cleanup_spec()
    rendered = canonical_formatting_spec_json(spec)
    payload = json.loads(rendered)

    assert payload["baseline"]["force"]["properties"] == sorted(
        payload["baseline"]["force"]["properties"]
    )
    restored = FormattingSpec.model_validate_json(rendered)
    assert canonical_formatting_spec_json(restored) == rendered
    assert formatting_spec_sha256(restored) == formatting_spec_sha256(spec)


def test_rule_pack_artifact_rejects_digest_mismatch_and_unexplained_approval() -> None:
    spec = default_cleanup_spec()
    common = {
        "pack_id": "pack_test",
        "request_id": "request-test-1",
        "name": "测试规则",
        "scope_label": "测试文档",
        "revision": 1,
        "change_note": "初始修订",
        "created_at": datetime.now(UTC),
        "spec": spec,
    }
    with pytest.raises(ValidationError, match="spec_sha256"):
        RulePackArtifact(**common, spec_sha256="0" * 64)
    with pytest.raises(ValidationError, match="approval note"):
        RulePackArtifact(
            **common,
            spec_sha256=formatting_spec_sha256(spec),
            approval_status="locally_approved",
        )
