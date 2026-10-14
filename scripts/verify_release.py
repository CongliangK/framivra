#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_release —— 发布身份验证模块（#16 / #41，keyless provenance 方案）。

信任锚（用户 2026-10-14 裁决：GitHub Actions keyless provenance，不新增长期
签名私钥）：
  - 预装可信 gh CLI（验证器自身的完整性由其安装渠道保证，本模块不下载 gh，
    绝不构造「下载一个未验证的验证器来验证自己」的循环）；
  - GitHub OIDC issuer https://token.actions.githubusercontent.com；
  - 固定 repo CongliangK/framivra + 精确 signer workflow（本仓
    .github/workflows/release-attest.yml）；
  - Sigstore 公共良实例 + 透明日志见证（--no-public-good 不允许）。

验证语义（fail-closed，无 hash-only 回退）：
  - gh 缺失 / 网络失败 / 任何身份不匹配 / 无证词 → ProvenanceError 非零退出；
  - 仅信 certificate 与 verifiedTimestamps（OIDC 注入、workflow 不可操纵）；
    predicate 字段可由 workflow 任意填写，绝不作为身份依据；
  - --source-ref/--source-digest 由调用方按「正在安装的确切版本」显式收紧
    （防降级：合法旧签名仍是合法签名，ref/digest 锚定本次预期的版本）。

真实正例来源：release-attest.yml 的 workflow_dispatch「仅验证模式」对固定
测试文件生成真 attestation 并跑本模块同款命令链（hosted 日志留证）；正式
发布资产的签名/验证接入 release 链是 #41 后续轮（须用户授权真实发版）。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

GITHUB_HOST = "github.com"
REPO = "CongliangK/framivra"
SIGNER_WORKFLOW = "github.com/%s/.github/workflows/release-attest.yml" % REPO
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
# 仅验证模式跑在 main；正式发布签名固定 tag ref（调用方传入）。
VERIFY_ONLY_REF = "refs/heads/main"


class ProvenanceError(Exception):
    """发布身份验证失败;消息面向用户,一行一条。"""


def find_gh():
    """定位可信 gh;缺失返回 None(fail-closed 由调用方决定指引文案)。"""
    return shutil.which(os.environ.get("VTX_GH_BIN") or "gh")


def _verify_cmd(gh, artifact, *, expect_ref=None, expect_digest=None):
    cmd = [gh, "attestation", "verify", str(artifact),
           "--repo", REPO,
           "--hostname", GITHUB_HOST,
           "--cert-oidc-issuer", OIDC_ISSUER,
           "--signer-workflow", SIGNER_WORKFLOW,
           "--deny-self-hosted-runners",
           "--format", "json"]
    if expect_ref:
        cmd += ["--source-ref", expect_ref]
    if expect_digest:
        cmd += ["--source-digest", expect_digest]
    return cmd


def verify_artifact(artifact, *, expect_ref=None, expect_digest=None, gh=None):
    """对已下载的确切 bytes 验证发布者身份;成功返回证词条数,失败抛错。

    gh 的 --repo/--hostname/--signer-workflow/--cert-oidc-issuer 已在 CLI 侧
    强制身份;本函数再独立核验 JSON 输出中「workflow 不可操纵」的两类字段:
    certificate 存在且 verifiedTimestamps 非空(透明日志/TSA 见证)。
    """
    gh = gh or find_gh()
    if gh is None:
        raise ProvenanceError(
            "缺少可信 gh 验证器:安装 GitHub CLI (Windows: winget install "
            "GitHub.cli;macOS: brew install gh;Linux: 见 gh 官方文档) 后重跑;"
            "本安装器绝不自动下载未验证的验证器")
    try:
        result = subprocess.run(
            _verify_cmd(gh, artifact, expect_ref=expect_ref,
                        expect_digest=expect_digest),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise ProvenanceError("发布身份验证执行失败(%s)" % error) from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ProvenanceError(
            "发布身份验证未通过:%s%s" % (
                str(artifact),
                (" (%s)" % detail[-1]) if detail else ""))
    try:
        verified = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProvenanceError("验证器输出不可解析(%s)" % error) from error
    if not isinstance(verified, list) or not verified:
        raise ProvenanceError("验证器未返回任何已验证证词:%s" % artifact)
    for entry in verified:
        cert = ((entry.get("verificationResult") or {})
                .get("signature", {}).get("certificate"))
        witnessed = (entry.get("verificationResult") or {}).get(
            "verifiedTimestamps")
        if not isinstance(cert, dict) or not cert:
            raise ProvenanceError("证词缺少 OIDC 证书字段(拒绝)")
        if not witnessed:
            raise ProvenanceError(
                "证词缺少透明日志/TSA 见证时间戳(拒绝):%s" % artifact)
    return len(verified)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="发布身份验证(keyless provenance;fail-closed)")
    parser.add_argument("artifact", help="待验证的已下载文件")
    parser.add_argument("--expect-ref", help="预期 source ref(如 refs/tags/v1.2.3)")
    parser.add_argument("--expect-digest", help="预期 source 仓库 commit digest")
    args = parser.parse_args(argv)
    try:
        count = verify_artifact(args.artifact, expect_ref=args.expect_ref,
                                expect_digest=args.expect_digest)
    except ProvenanceError as error:
        print("✗ %s" % error, file=sys.stderr)
        return 1
    print("✓ 发布者身份验证通过(%d 条证词;%s@%s)" % (count, REPO, SIGNER_WORKFLOW))
    return 0


if __name__ == "__main__":
    sys.exit(main())
