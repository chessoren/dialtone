"""Place a DIALTONE-wrapped CALL-E call, then classify and attest the result.

Default is a preview: the compiled task and result schema are printed and
nothing is dialled. A real call needs ``--live`` and an explicit confirmation.

Raw CALL-E responses (with transcripts) go to data/real/raw/, which is
git-ignored. Only derived results - features, detection, verdict, timing,
masked numbers, no transcript text - go to data/real/results/.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import attestation, features, protocol
from .classifier import Classifier
from .models import Transcript

RAW_DIR = Path("data/real/raw")
RESULTS_DIR = Path("data/real/results")


def mask(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"+{digits[:-4][:2]}******{digits[-2:]}" if len(digits) > 6 else "***"


def build_request(to_phone: str, goal: str, principal: str, mode: str, nonce: str, region: str = "US") -> dict[str, Any]:
    if not re.fullmatch(r"\+[1-9]\d{7,14}", to_phone):
        raise SystemExit("destination must be E.164, e.g. +14155550123")
    if mode == "dialtone":
        task = protocol.compile_task(goal, principal, nonce)
    else:
        task = (f"GOAL: {goal}\nYou are an AI assistant calling on behalf of {principal}. Disclose that you are an AI "
                "assistant, be polite, and report exactly what the other party confirmed and any confirmation code.")
    return {
        "task": task,
        "recipients": [{"phones": [to_phone], "region": region, "locale": "en-US"}],
        "result_schema": protocol.RESULT_SCHEMA,
        "metadata": {"app": "dialtone", "mode": mode, "protocol_version": protocol.VERSION},
    }


def analyse(call: dict, name: str, label: str | None, mode: str, nonce: str | None, clf: Classifier | None = None) -> dict:
    clf = clf or Classifier.load()
    t = Transcript.from_calle(call, label)
    t.meta["task"] = call.get("task") or t.meta.get("task")
    hs = protocol.inspect(t, nonce if mode == "dialtone" else None)
    points, ttd = clf.timeline(t, nonce=nonce)
    det = clf.classify(t, nonce=nonce)
    behaviour_only = clf.classify(t, use_protocol=False)
    structured = call.get("structured_result") or {}
    commitment = attestation.infer_commitment(t.meta.get("task"))
    verdict = attestation.evaluate(t, det, call.get("task_completed"), commitment, hs)
    return {
        "name": name,
        "label": label,
        "mode": mode,
        "calle_call_id": call.get("id"),
        "status": call.get("status"),
        "duration": round(t.duration, 1),
        "n_turns": len(t.turns),
        "handshake": {"offered": hs.offered, "acknowledged": hs.acknowledged, "nonce_ok": hs.nonce_ok,
                      "acknowledged_at": hs.acknowledged_at, "human_review": hs.human_review, "notes": hs.notes},
        "detection": det.to_dict(),
        "detection_behaviour_only": behaviour_only.to_dict(),
        "time_to_detection": ttd,
        "timeline": [{"t": w, "label": d.label, "conf": round(d.confidence, 3), "via": d.via} for w, d in points],
        "features": {k: round(v, 3) for k, v in features.extract(t).items()},
        "calle_reported": {"task_completed": call.get("task_completed"),
                           "completion_confidence": call.get("completion_confidence"),
                           "counterpart_type": structured.get("counterpart_type"),
                           "outcome": structured.get("outcome"),
                           "human_review": structured.get("human_review")},
        "verdict": verdict.to_dict(),
    }


def place(request: dict, name: str, timeout: float = 900) -> dict:
    from calle import CalleClient

    key = os.environ.get("CALLE_API_KEY")
    if not key:
        raise SystemExit("CALLE_API_KEY is not set (add it to .env)")
    with CalleClient(api_key=key) as client:
        created = client.calls.create(**request, idempotency_key=f"dialtone-{name}")
        print(f"created {created['id']} - waiting for the terminal result (Ctrl+C stops waiting, not the call)")
        started = time.monotonic()
        call = client.calls.wait_for_result(created["id"], interval_seconds=5, timeout_seconds=timeout)
        call["_dialtone_wall_seconds"] = round(time.monotonic() - started, 1)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{name}.json").write_text(json.dumps(call, indent=1))
    return call


def fetch(call_id: str, name: str) -> dict:
    from calle import CalleClient

    with CalleClient(api_key=os.environ["CALLE_API_KEY"]) as client:
        call = client.calls.get(call_id)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{name}.json").write_text(json.dumps(call, indent=1))
    return call


def save_result(result: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{result['name']}.json"
    path.write_text(json.dumps(result, indent=1))
    return path
