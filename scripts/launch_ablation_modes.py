"""Start isolated local UI instances for the six predeclared experiment modes.

Set TEACHER_EVAL_EXPERIMENT_PROFILE_ID in the environment before invoking.
The bound model credential stays in the existing local model configuration.
No business question is submitted by this launcher.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import site
import socket
import subprocess
import sys
import time
import urllib.request
from uuid import uuid4


MODES = (
    "g0_rule", "g1_llm_unconstrained", "g2_llm_constrained",
    "a1_no_execution_verification", "a2_no_output_verification_or_repair",
    "a3_no_answer_repair",
)
ROOT = Path(__file__).resolve().parents[1]


def _free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _status(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-db", required=True, type=Path)
    parser.add_argument("--runs-root", required=True, type=Path)
    parser.add_argument("--model-config", type=Path, help="existing local llm_config.json; copied into each isolated model run")
    parser.add_argument("--base-port", required=True, type=int)
    parser.add_argument("--mode", choices=MODES, action="append")
    args = parser.parse_args()
    modes = tuple(args.mode or MODES)
    db = args.data_db.resolve(strict=True)
    if not db.is_file():
        parser.error("data-db must be a SQLite file")
    if any(mode != "g0_rule" for mode in modes) and not os.getenv("TEACHER_EVAL_EXPERIMENT_PROFILE_ID"):
        parser.error("TEACHER_EVAL_EXPERIMENT_PROFILE_ID is required for model modes")
    if any(mode != "g0_rule" for mode in modes) and (not args.model_config or not args.model_config.is_file()):
        parser.error("--model-config must point to an existing llm_config.json for model modes")
    expected_profile = None
    config_hash = None
    if any(mode != "g0_rule" for mode in modes):
        config_bytes = args.model_config.read_bytes()
        config_hash = hashlib.sha256(config_bytes).hexdigest().upper()
        config = json.loads(config_bytes)
        expected_id = os.environ["TEACHER_EVAL_EXPERIMENT_PROFILE_ID"]
        if config.get("active") != expected_id:
            parser.error("model-config active profile does not match the bound profile ID")
        expected_profile = next((item for item in config.get("profiles", []) if item.get("id") == expected_id), None)
        if not expected_profile or not expected_profile.get("model") or not expected_profile.get("api_key"):
            parser.error("model-config active profile is incomplete")
    if not 1024 <= args.base_port <= 65535 - len(modes):
        parser.error("base-port is out of range")
    ports = [args.base_port + index for index in range(len(modes))]
    if any(not _free(port) for port in ports):
        parser.error("one or more requested ports are occupied; no mode was started")
    root = args.runs_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    batch = root / f"ablation_{time.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    batch.mkdir()
    db_hash = hashlib.sha256(db.read_bytes()).hexdigest().upper()
    manifest = {"candidate": "0.13.84-product-reverse-negation-20260927", "data_db_sha256": db_hash,
                "model_config_sha256": config_hash, "runs": []}
    user_site = site.getusersitepackages()
    for mode, port in zip(modes, ports):
        run_dir = batch / mode
        run_dir.mkdir()
        trace_dir = run_dir / "trace"
        trace_dir.mkdir()
        charts_dir = run_dir / "charts"
        charts_dir.mkdir()
        roaming = run_dir / "appdata" / "Roaming"
        local = run_dir / "appdata" / "Local"
        local.mkdir(parents=True)
        model_folder = roaming / "TeacherEval"
        model_folder.mkdir(parents=True)
        if mode != "g0_rule":
            config_copy = model_folder / "llm_config.json"
            config_copy.write_bytes(config_bytes)
            if hashlib.sha256(config_copy.read_bytes()).hexdigest().upper() != config_hash:
                raise RuntimeError("isolated_model_config_copy_mismatch")
        run_id = uuid4().hex
        env = os.environ.copy()
        env.update({
            "TEACHER_EVAL_EXPERIMENT_MODE": mode,
            "TEACHER_EVAL_EXPERIMENT_MODE_ENABLED": "1",
            "TEACHER_EVAL_EXPERIMENT_RUN_ID": run_id,
            "TEACHER_EVAL_EXPERIMENT_PROFILE_ID": os.environ.get("TEACHER_EVAL_EXPERIMENT_PROFILE_ID", ""),
            "TEACHER_EVAL_DATA_DB": str(db),
            "TEACHER_EVAL_CONTRACT_DB": str(run_dir / "contract.sqlite"),
            "TEACHER_EVAL_EXPERIMENT_TRACE_DIR": str(trace_dir),
            "TEACHER_EVAL_CHARTS_DIR": str(charts_dir),
            "APPDATA": str(roaming),
            "LOCALAPPDATA": str(local),
            "PYTHONPATH": os.pathsep.join(filter(None, (user_site, os.environ.get("PYTHONPATH", "")))),
        })
        output = (run_dir / "server.log").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "run_app.py"), "serve", "--host", "127.0.0.1",
             "--port", str(port), "--no-browser"], cwd=ROOT, env=env,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output.close()
        health = None
        for _ in range(40):
            if process.poll() is not None:
                break
            try:
                health = _status(port)
                break
            except (OSError, ValueError):
                time.sleep(0.5)
        identity = (health or {}).get("experiment_mode") or {}
        source = (health or {}).get("data_source") or {}
        llm = (health or {}).get("llm") or {}
        expected_profile_hash = hashlib.sha256(env["TEACHER_EVAL_EXPERIMENT_PROFILE_ID"].encode("utf-8")).hexdigest()[:12]
        if (
            (health or {}).get("service_version") != manifest["candidate"]
            or identity.get("active_mode") != mode
            or not identity.get("enabled")
            or not (health or {}).get("real_db")
            or source.get("active_source") != "environment_override"
            or source.get("active_file") != db.name
            or source.get("read_only") is not True
            or ((health or {}).get("contract_store") or {}).get("status") != "available"
            or (mode != "g0_rule" and identity.get("profile_id_hash") != expected_profile_hash)
            or (mode != "g0_rule" and (
                llm.get("id") != expected_profile.get("id")
                or llm.get("model") != expected_profile.get("model")
                or llm.get("provider") != expected_profile.get("provider")
                or not llm.get("has_api_key")
            ))
        ):
            process.terminate()
            raise RuntimeError(f"identity preflight failed for {mode}; see {run_dir / 'server.log'}")
        row = {"mode": mode, "port": port, "url": f"http://127.0.0.1:{port}/?ui=v3.28.84",
               "run_id_hash": identity.get("run_id_hash"), "pid": process.pid,
               "contract_db": str(run_dir / "contract.sqlite"), "trace_dir": str(trace_dir),
               "charts_dir": str(charts_dir),
               "health_service_version": health.get("service_version"),
               "health_active_mode": identity.get("active_mode")}
        if mode != "g0_rule":
            row["actual_model"] = llm.get("model")
            row["actual_provider"] = llm.get("provider")
        manifest["runs"].append(row)
        print(f"{mode}: {row['url']} (identity OK)")
    (batch / "launch_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Launch manifest: {batch / 'launch_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
