#!/usr/bin/env python3
"""Record the existing native all/qc commands for a NEW isolated opening project.

MIT, 2026 Framivra contributors. Default is a read-only plan. --run is for the
root-coordinated, already qualified local environment only. No fallback/retry,
queue, environment overrides, backend changes, or custom render/mux operation.
Logs are ordinary execution evidence, not cryptographic producer attestation.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
import prepare_preview as P
import inspect_preview as I


def native_run(project, manifest, engine_repo, engine_commit, backend_sha=None, run=False):
    project, engine_repo = Path(project).absolute(), Path(engine_repo).absolute()
    inputs = I.check_project(project, manifest)
    infra = I.checked_engine(engine_repo, engine_commit)
    context = infra.Ctx(project / "timeline.json")  # no stage invocation
    before = I.engine_snapshot(engine_repo, engine_commit)
    backend = context.gfx_backend
    digest = I.backend_contract(backend)  # unknown/incomplete environment is not qualified
    if backend_sha is not None:
        P.require(digest == backend_sha, "Environment differs from selected backend identity")
    prefix = [sys.executable, "-m", "vtx", "-t", str(project / "timeline.json")]
    commands = [prefix + ["all"], prefix + ["qc", "--strict"]]
    record = {"format": "framivra-opening-native-run/1", "completed": False,
              "project": str(project), "engine_repo": str(engine_repo), "python": sys.executable,
              "source_manifest_sha256": manifest, "inputs_before": inputs,
              "engine_before": before, "backend_before": backend, "backend_sha256": digest,
              "commands": [], "started_utc": datetime.now(timezone.utc).isoformat(),
              "evidence_scope": "fixed native CLI logs; not a runtime backend attestation or art/listening approval"}
    P.require(not any((project / n).exists() for n in ("cache", "out", "native-run")), "Requires fresh prepared project, no existing run outputs")
    if not run:
        return {"status": "READ_ONLY_NATIVE_PLAN_NOT_EXECUTED", "commands": commands, "record": record}
    P.require(backend_sha is not None, "--run requires preselected --expected-backend-sha256")
    evidence = project / "native-run"
    with P.directory(project) as fd:
        import os
        os.mkdir("native-run", dir_fd=fd)  # exclusive; never overwrite/retry a prior run
    failure = None
    try:
        for index, command in enumerate(commands):
            row = {"argv": command, "cwd": str(engine_repo), "exit_code": None}
            record["commands"].append(row)
            with (evidence / f"{index}.log").open("xb") as log:
                log.write((json.dumps({"argv": command, "cwd": str(engine_repo)}) + "\n").encode()); log.flush()
                try:
                    result = subprocess.run(command, cwd=engine_repo, stdout=log, stderr=subprocess.STDOUT)
                    row["exit_code"] = result.returncode
                except (OSError, KeyboardInterrupt) as error:
                    row["error"] = type(error).__name__ + ": " + str(error)
                    raise
            with P.directory(evidence) as fd:
                row["log_sha256"] = P.sha(P.read_file(fd, f"{index}.log", 32 * 1024 * 1024))
            P.require(row["exit_code"] == 0, "Native command failed; preserved, no retry: " + str(index))
        record["inputs_after"] = I.check_project(project, manifest)
        record["engine_after"] = I.engine_snapshot(engine_repo, engine_commit)
        record["backend_after"] = infra.Ctx(project / "timeline.json").gfx_backend
        P.require(record["inputs_after"] == inputs and record["backend_after"] == backend, "Inputs/environment changed during native run")
        I.backend_contract(record["backend_after"])
        with P.directory(project / "out") as fd:
            record["outputs"] = {name: P.sha(P.read_file(fd, name))
                                 for name in ("receipt.json", "qc_report.json", "audio.wav", "VERSIONS.json", "final.mp4")}
        record["completed"] = True  # commands completed; inspector still checks their real contracts
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        failure = error
        record["failure"] = type(error).__name__ + ": " + str(error)
    finally:
        record["ended_utc"] = datetime.now(timezone.utc).isoformat()
        with (evidence / "execution.json").open("xb") as stream:
            stream.write(P.json_bytes(record))
    if failure is not None:
        raise failure
    return {"status": "NATIVE_COMMANDS_COMPLETED_REQUIRES_INSPECTION", "record": record}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--expected-source-manifest-sha256", required=True)
    ap.add_argument("--engine-repo", type=Path, required=True)
    ap.add_argument("--expected-engine-commit", required=True)
    ap.add_argument("--expected-backend-sha256")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    try:
        result = native_run(args.project, args.expected_source_manifest_sha256, args.engine_repo,
                            args.expected_engine_commit, args.expected_backend_sha256, args.run)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print("Native preview not completed: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
