#!/usr/bin/env python3
"""Evidence-backed model download boundary; not a legal opinion or rights grant.

The JSON inventory is the sole home of asset/license facts. No user configuration,
credentials, model directories or private environment are read by plan/audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "docs" / "model-license-inventory.json"
NOTICE = "FRAMIVRA-LICENSE-NOTICE.json"
# Conventional media/font files need individual provenance entries. This does
# not recognize arbitrary renamed binaries or classify every possible format.
MEDIA_FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2", ".png", ".jpg", ".jpeg",
                       ".webp", ".gif", ".wav", ".mp3", ".flac", ".mp4", ".mov", ".mkv"}


class LicensePolicyError(RuntimeError):
    pass


def load_inventory(path=INVENTORY):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("default_purpose") != "commercial":
        raise LicensePolicyError("unsupported inventory/purpose policy")
    if not isinstance(data.get("assets"), dict) or not isinstance(data.get("evidence"), dict):
        raise LicensePolicyError("invalid inventory")
    for ident, evidence in data["evidence"].items():
        if evidence.get("local_path"):
            relative = Path(evidence["local_path"])
            path = ROOT / relative
            if relative.is_absolute() or ".." in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                raise LicensePolicyError("unsupported local evidence home")
            text = path.read_text(encoding="utf-8")
        else:
            text = evidence.get("text")
        if not isinstance(text, str) or not text or hashlib.sha256(text.encode("utf-8")).hexdigest() != evidence.get("sha256"):
            raise LicensePolicyError("missing/changed evidence snapshot: " + ident)
        if not re.fullmatch(r"[0-9a-f]{40}", evidence.get("revision", "")):
            raise LicensePolicyError("evidence must bind immutable revision: " + ident)
    for ident, asset in data["assets"].items():
        if not asset.get("source") or not asset.get("license_status") or not isinstance(asset.get("default_payload"), bool):
            raise LicensePolicyError("asset lacks source/status/payload scope: " + ident)
        if asset.get("evidence") and asset["evidence"] not in data["evidence"]:
            raise LicensePolicyError("missing evidence reference: " + ident)
        for ref in asset.get("additional_evidence", []):
            if ref not in data["evidence"]:
                raise LicensePolicyError("missing additional evidence reference: " + ident)
        if asset["default_payload"] and (asset["license_status"] != "DECLARED" or not asset.get("evidence")):
            # Existing source/data conflicts must be visible as blockers, not
            # omitted to manufacture a clean inventory. Never permits weights.
            if not (asset.get("kind") in ("authored-code", "data")
                    and asset.get("distribution") == ["git-source"]
                    and asset.get("distribution_blocker")):
                raise LicensePolicyError("unreviewed default payload: " + ident)
    return data


def block_legacy_download(channel, *, inventory=None):
    """Never translate a mutable/mirror download into a reviewed HF snapshot.

    This also gates unreviewed constructor chains before import, writes or any
    third-party helper can silently fetch auxiliary weights. Updating a status
    string is not an authorization adapter; a new route needs explicit review.
    """
    data = inventory if inventory is not None else load_inventory()
    entry = data.get("legacy_downloads", {}).get(channel)
    if entry is None:
        raise LicensePolicyError("unregistered download/runtime chain: " + channel)
    raise LicensePolicyError(
        "BLOCKED " + channel + ": " + entry["reason"] +
        " Existing user files are preserved. See docs/third-party-licenses.md; "
        "plan a reviewed asset with scripts/policy/model_license_policy.py. "
        "UNKNOWN/restricted chains cannot be cleared by --accept-license."
    )


def authorize_snapshot(ident, purpose, acknowledgement, *, inventory=None):
    data = inventory if inventory is not None else load_inventory()
    asset = data["assets"].get(ident)
    if asset is None:
        raise LicensePolicyError("unregistered asset: " + ident)
    route = asset.get("download", {})
    if asset["license_status"] != "DECLARED" or asset.get("kind") != "model-weights":
        raise LicensePolicyError("UNKNOWN/restricted/not-a-weight asset: " + ident)
    if asset.get("license_identifier") not in ("MIT", "Apache-2.0"):
        raise LicensePolicyError("license has no reviewed snapshot adapter: " + ident)
    if purpose not in ("commercial", "noncommercial") or purpose not in route.get("allowed_purposes", []):
        raise LicensePolicyError("purpose is not reviewed for asset: " + ident)
    if route.get("provider") != "huggingface" or not re.fullmatch(r"[0-9a-f]{40}", route.get("revision", "")):
        raise LicensePolicyError("source/revision not reviewed: " + ident)
    repo = route.get("repository", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise LicensePolicyError("invalid official repository")
    evidence = data["evidence"].get(asset.get("evidence"))
    expected_url = "https://huggingface.co/" + repo + "/resolve/" + route["revision"] + "/README.md"
    if not evidence or evidence.get("url") != expected_url or evidence.get("revision") != route["revision"]:
        raise LicensePolicyError("weight evidence does not bind source/revision")
    if evidence["sha256"] != acknowledgement:
        raise LicensePolicyError("read the exact upstream evidence then explicitly acknowledge its SHA256")
    # A captured card declaration licenses this repository, not a similarly named
    # mirror, a derived asset, API output or constructor's other repositories.
    declaration = "license: " + {"MIT": "mit", "Apache-2.0": "apache-2.0"}[asset["license_identifier"]]
    text = evidence["text"]
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != evidence["sha256"]:
        raise LicensePolicyError("changed captured weight evidence")
    frontmatter = text.split("---", 2)
    if not text.startswith("---\n") or len(frontmatter) != 3 or declaration not in frontmatter[1].splitlines():
        raise LicensePolicyError("captured weight card frontmatter does not declare reviewed license")
    return asset, evidence


def download_snapshot(ident, output, purpose, acknowledgement, *, inventory=None, downloader=None):
    """Explicit opt-in only. Fixed official source, no mirror/credential fallback.

    This binds source and notice, not an independent signature or proof of output
    rights. It does not execute downloaded code. Existing destinations are never
    merged/overwritten; retries use a new empty destination.
    """
    asset, evidence = authorize_snapshot(ident, purpose, acknowledgement, inventory=inventory)
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise LicensePolicyError("destination already exists; existing user assets will not be modified")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise LicensePolicyError("choose an existing non-symlink parent directory")
    if downloader is None:
        # Import only after every policy and path check. No pip/bootstrap happens.
        from huggingface_hub import snapshot_download
        downloader = snapshot_download
    route = asset["download"]
    with tempfile.TemporaryDirectory(prefix=".framivra-model-", dir=str(output.parent)) as directory:
        stage = Path(directory) / "snapshot"
        stage.mkdir()
        downloader(repo_id=route["repository"], revision=route["revision"],
                   endpoint="https://huggingface.co", token=False, local_dir=str(stage))
        card = stage / "README.md"
        if card.is_symlink() or not card.is_file() or hashlib.sha256(card.read_bytes()).hexdigest() != evidence["sha256"]:
            raise LicensePolicyError("downloaded notice differs from reviewed evidence; snapshot not published")
        notice = dict(schema=1, asset=ident, repository=route["repository"], revision=route["revision"],
                      evidence_url=evidence["url"], evidence_sha256=evidence["sha256"],
                      purpose=purpose, license_identifier=asset["license_identifier"],
                      notice="Preserve all upstream LICENSE/NOTICE/README files. This record is not a rights grant or signed attestation.")
        (stage / NOTICE).write_text(json.dumps(notice, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if output.exists() or output.is_symlink():
            raise LicensePolicyError("destination appeared during download; existing assets preserved")
        stage.rename(output)
    return output


def audit_payload(root=ROOT, *, inventory=None, tracked=None):
    """Default payload check, not a full transitive/binary/derivative legal audit."""
    data = inventory if inventory is not None else load_inventory()
    root = Path(root)
    evidence = data["evidence"]["framivra"]
    if hashlib.sha256((root / "LICENSE").read_bytes()).hexdigest() != evidence["sha256"]:
        raise LicensePolicyError("root declared license changed; re-review inventory")
    project = (root / "pyproject.toml").read_text(encoding="utf-8").split("[project]", 1)[1].split("\n[", 1)[0]
    if not re.search(r"(?m)^dependencies\s*=\s*\[\s*\]\s*$", project):
        raise LicensePolicyError("kernel dependency payload changed; add reviewed component inventory")
    if tracked is None:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, stdout=subprocess.PIPE)
        tracked = result.stdout.decode("utf-8").split("\0")
    # New third-party model/binary payloads need an explicit inventory/packaging
    # review, not a broad file-extension whitelist with automatic legal PASS.
    banned = {".pth", ".pt", ".ckpt", ".safetensors", ".onnx", ".gguf", ".bin", ".exe", ".dll", ".so", ".dylib", ".whl"}
    unexpected = [name for name in tracked if name and Path(name).suffix.lower() in banned]
    if unexpected:
        raise LicensePolicyError("unreviewed model/binary payload: " + ", ".join(unexpected))
    registered_files = {name for asset in data["assets"].values() for name in asset.get("files_sha256", {})}
    unregistered = [name for name in tracked if Path(name).suffix.lower() in MEDIA_FONT_SUFFIXES and name not in registered_files]
    if unregistered:
        raise LicensePolicyError("unregistered media/font payload: " + ", ".join(unregistered))
    for ident, asset in data["assets"].items():
        for name, expected in asset.get("files_sha256", {}).items():
            path = root / name
            if Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise LicensePolicyError("unsafe inventoried file: " + name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise LicensePolicyError("inventoried payload changed: " + ident + ": " + name)
        if asset.get("version_home") and asset.get("lock_sha256"):
            if hashlib.sha256((root / asset["version_home"]).read_bytes()).hexdigest() != asset["lock_sha256"]:
                raise LicensePolicyError("external dependency lock changed: " + ident)
    blockers = {k: v["distribution_blocker"] for k, v in data["assets"].items() if v.get("distribution_blocker")}
    return dict(ok=True, scope="inventory integrity only; no clearance of source redistribution or optional external assets",
                release_eligible=not blockers and not data.get("provenance_warnings"),
                known_payload_blockers_absent=not blockers,
                legal_clearance=False,
                release_blockers=blockers,
                distribution_policy=data.get("distribution_policy", {}),
                default_assets=[k for k, v in data["assets"].items() if v["default_payload"]],
                provenance_warnings=data.get("provenance_warnings", []),
                unknown_or_restricted=[k for k, v in data["assets"].items() if v["license_status"] != "DECLARED"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("audit", help="zero-write/offline default payload/evidence check; not a legal certificate")
    plan = commands.add_parser("plan", help="offline asset evidence and exact opt-in notice")
    plan.add_argument("assets", nargs="*")
    legacy = commands.add_parser("legacy-download", help="fail closed for unreviewed legacy/runtime chains")
    legacy.add_argument("channel")
    download = commands.add_parser("download", help="WRITES/downloads only an explicitly reviewed fixed official snapshot")
    download.add_argument("asset")
    download.add_argument("--output", required=True)
    download.add_argument("--purpose", choices=("commercial", "noncommercial"), default="commercial")
    download.add_argument("--evidence-sha256", required=True, help="read and acknowledge the exact captured evidence, not a blanket license override")
    args = parser.parse_args(argv)
    try:
        data = load_inventory()
        if args.command == "audit":
            print(json.dumps(audit_payload(inventory=data), ensure_ascii=False, indent=2))
        elif args.command == "legacy-download":
            block_legacy_download(args.channel, inventory=data)
        elif args.command == "plan":
            if not args.assets:
                print(json.dumps({"asset_ids": list(data["assets"]), "next": "plan <asset> shows its source/state and exact evidence notice; full text home is the inventory"}, ensure_ascii=False, indent=2))
                return 0
            selected = args.assets
            rows = {}
            for ident in selected:
                if ident not in data["assets"]:
                    raise LicensePolicyError("unregistered asset: " + ident)
                asset = dict(data["assets"][ident])
                if asset.get("evidence"):
                    asset["evidence"] = {k: v for k, v in data["evidence"][asset["evidence"]].items() if k != "text"}
                    asset["evidence"]["text_home"] = "docs/model-license-inventory.json:evidence." + data["assets"][ident]["evidence"]
                rows[ident] = asset
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print(download_snapshot(args.asset, args.output, args.purpose, args.evidence_sha256, inventory=data))
    except (LicensePolicyError, OSError, ValueError, ImportError, KeyError, subprocess.CalledProcessError) as error:
        print("model-license policy: " + str(error), file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
