"""
tests/sandbox/detection/test_sensitivity.py

Comprehensive pytest suite for SensitivityScanner.

Covers:
 - Layer 1 (_scan_file_patterns) — file name / glob matching
 - Layer 2 (_scan_content)       — regex content scanning
 - Combined scan()               — merged results, recommendations, explain()

All async tests use @pytest.mark.asyncio (via pytestmark at module level).
All filesystem operations use the tmp_path fixture so tests are hermetic.
"""

import pytest
from pathlib import Path

from src.sandbox.detection.sensitivity import (
    DetectionLayer,
    Recommendation,
    SensitivityScanner,
)

# Apply asyncio mark to every test in this module automatically
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_file(base: Path, name: str, content: str = "") -> Path:
    """Create a file inside base with the given content and return its path."""
    p = base / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Layer 1 — file pattern tests
# ---------------------------------------------------------------------------


async def test_layer1_detects_env_file(tmp_path: Path) -> None:
    make_file(tmp_path, ".env", "SECRET=abc")
    scanner = SensitivityScanner()
    findings = await scanner._scan_file_patterns(tmp_path)
    assert len(findings) >= 1
    assert all(f.layer == DetectionLayer.FILE_PATTERN for f in findings)
    matched_names = [f.file.name for f in findings]
    assert ".env" in matched_names


async def test_layer1_detects_pem_file(tmp_path: Path) -> None:
    make_file(tmp_path, "server.pem", "")
    scanner = SensitivityScanner()
    findings = await scanner._scan_file_patterns(tmp_path)
    assert len(findings) >= 1
    assert any(f.file.name == "server.pem" for f in findings)


async def test_layer1_detects_key_file(tmp_path: Path) -> None:
    make_file(tmp_path, "id_rsa.key", "")
    scanner = SensitivityScanner()
    findings = await scanner._scan_file_patterns(tmp_path)
    assert len(findings) >= 1
    assert any(f.file.name == "id_rsa.key" for f in findings)


async def test_layer1_detects_secret_in_name(tmp_path: Path) -> None:
    make_file(tmp_path, "my_secret_config.yaml", "")
    scanner = SensitivityScanner()
    findings = await scanner._scan_file_patterns(tmp_path)
    assert len(findings) >= 1
    assert any(f.file.name == "my_secret_config.yaml" for f in findings)


async def test_layer1_detects_token_in_name(tmp_path: Path) -> None:
    make_file(tmp_path, "auth_token.json", "")
    scanner = SensitivityScanner()
    findings = await scanner._scan_file_patterns(tmp_path)
    assert len(findings) >= 1
    assert any(f.file.name == "auth_token.json" for f in findings)


async def test_layer1_no_findings_for_safe_file(tmp_path: Path) -> None:
    make_file(tmp_path, "main.py", "x = 1")
    scanner = SensitivityScanner()
    findings = await scanner._scan_file_patterns(tmp_path)
    assert findings == []


async def test_layer1_empty_directory(tmp_path: Path) -> None:
    scanner = SensitivityScanner()
    findings = await scanner._scan_file_patterns(tmp_path)
    assert findings == []


async def test_layer1_nested_env_file(tmp_path: Path) -> None:
    nested = tmp_path / "config"
    nested.mkdir()
    (nested / ".env").write_text("DB_PASS=secret", encoding="utf-8")
    scanner = SensitivityScanner()
    findings = await scanner._scan_file_patterns(tmp_path)
    assert len(findings) >= 1
    assert any(f.file.name == ".env" for f in findings)


# ---------------------------------------------------------------------------
# Layer 2 — content scan tests
# ---------------------------------------------------------------------------


async def test_layer2_detects_aws_key(tmp_path: Path) -> None:
    # AKIA + 16 uppercase alphanumeric = 20 chars total
    make_file(tmp_path, "config.txt", "key = AKIAIOSFODNN7EXAMPLE")
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert len(findings) >= 1
    assert any(f.layer == DetectionLayer.CONTENT_SCAN for f in findings)
    assert any("AWS" in f.reason for f in findings)


async def test_layer2_detects_private_key_header(tmp_path: Path) -> None:
    make_file(tmp_path, "key.txt", "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA")
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert len(findings) >= 1
    assert any("PEM private key" in f.reason for f in findings)


async def test_layer2_detects_hardcoded_password(tmp_path: Path) -> None:
    make_file(tmp_path, "settings.cfg", "password=supersecret")
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert len(findings) >= 1
    assert any("password" in f.reason.lower() for f in findings)


async def test_layer2_detects_hardcoded_secret(tmp_path: Path) -> None:
    make_file(tmp_path, "app.cfg", "secret=abc123")
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert len(findings) >= 1
    assert any("secret" in f.reason.lower() for f in findings)


async def test_layer2_detects_internal_ip_10x(tmp_path: Path) -> None:
    make_file(tmp_path, "hosts.txt", "host = 10.0.0.1")
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert len(findings) >= 1
    assert any("10.x.x.x" in f.reason for f in findings)


async def test_layer2_detects_internal_ip_192x(tmp_path: Path) -> None:
    make_file(tmp_path, "hosts.txt", "host = 192.168.1.1")
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert len(findings) >= 1
    assert any("192.168.x.x" in f.reason for f in findings)


async def test_layer2_skips_binary_file(tmp_path: Path) -> None:
    bin_file = tmp_path / "data.bin"
    bin_file.write_bytes(bytes(range(256)) * 20)
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert findings == []


async def test_layer2_skips_large_file(tmp_path: Path) -> None:
    big_file = tmp_path / "big.txt"
    big_file.write_text("x" * (1_048_576 + 1), encoding="utf-8")
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert findings == []


async def test_layer2_match_value_is_redacted(tmp_path: Path) -> None:
    aws_key = "AKIAIOSFODNN7EXAMPLE"
    make_file(tmp_path, "config.txt", f"key = {aws_key}")
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    aws_findings = [f for f in findings if "AWS" in f.reason]
    assert len(aws_findings) >= 1
    finding = aws_findings[0]
    assert finding.match_value.endswith("***")
    assert finding.match_value != aws_key


async def test_layer2_no_findings_for_clean_file(tmp_path: Path) -> None:
    make_file(tmp_path, "clean.py", 'x = 1\nprint("hello")\n')
    scanner = SensitivityScanner()
    findings = await scanner._scan_content(tmp_path)
    assert findings == []


# ---------------------------------------------------------------------------
# Combined SensitivityScanner.scan() tests
# ---------------------------------------------------------------------------


async def test_scan_returns_force_local_on_env_file(tmp_path: Path) -> None:
    make_file(tmp_path, ".env", "DB_PASSWORD=hunter2")
    scanner = SensitivityScanner()
    result = await scanner.scan(tmp_path)
    assert result.is_sensitive is True
    assert result.recommendation == Recommendation.FORCE_LOCAL


async def test_scan_returns_allow_cloud_on_clean_workspace(tmp_path: Path) -> None:
    make_file(tmp_path, "main.py", "x = 1")
    scanner = SensitivityScanner()
    result = await scanner.scan(tmp_path)
    assert result.is_sensitive is False
    assert result.recommendation == Recommendation.ALLOW_CLOUD


async def test_scan_merges_findings_from_both_layers(tmp_path: Path) -> None:
    # .env triggers Layer 1; AKIAIOSFODNN7EXAMPLE inside triggers Layer 2
    make_file(tmp_path, ".env", "AWS_KEY=AKIAIOSFODNN7EXAMPLE")
    scanner = SensitivityScanner()
    result = await scanner.scan(tmp_path)
    layers = {f.layer for f in result.findings}
    assert DetectionLayer.FILE_PATTERN in layers
    assert DetectionLayer.CONTENT_SCAN in layers


async def test_scan_explain_returns_strings(tmp_path: Path) -> None:
    make_file(tmp_path, ".env", "SECRET=abc")
    scanner = SensitivityScanner()
    result = await scanner.scan(tmp_path)
    explanation = result.explain()
    assert isinstance(explanation, list)
    assert len(explanation) >= 1
    assert all(isinstance(s, str) for s in explanation)


async def test_scan_empty_workspace(tmp_path: Path) -> None:
    scanner = SensitivityScanner()
    result = await scanner.scan(tmp_path)
    assert result.is_sensitive is False
    assert result.findings == []
    assert result.recommendation == Recommendation.ALLOW_CLOUD


async def test_scan_invalid_workspace_raises(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist"
    scanner = SensitivityScanner()
    with pytest.raises(ValueError):
        await scanner.scan(non_existent)


async def test_scan_multiple_findings(tmp_path: Path) -> None:
    make_file(tmp_path, ".env", "DB_PASS=secret")
    make_file(tmp_path, "server.pem", "")
    make_file(tmp_path, "id_rsa.key", "")
    scanner = SensitivityScanner()
    result = await scanner.scan(tmp_path)
    assert len(result.findings) >= 3


async def test_scan_policy_param_ignored(tmp_path: Path) -> None:
    make_file(tmp_path, "main.py", "x = 1")
    scanner = SensitivityScanner()
    dummy_policy = {"allow": ["pypi.org"], "block_all_others": True}
    result = await scanner.scan(tmp_path, policy=dummy_policy)
    assert result is not None
    assert isinstance(result.is_sensitive, bool)
