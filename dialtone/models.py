"""Normalized call transcripts.

Speaker labels follow CALL-E's API: ``bot`` is *our* agent, ``user`` is the
party being classified (the counterpart). Transcripts pulled from the inbound
AI line are flipped so the caller becomes ``user`` - the classifier is always
asked "what is on the other end?".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LABELS = ("human", "ivr", "agent")
WORDS_PER_SECOND = 2.7


def words(text: str) -> list[str]:
    return [w for w in text.replace("—", " ").split() if any(c.isalnum() for c in w)]


def speech_seconds(text: str) -> float:
    """Rough spoken duration of a turn. CALL-E only exposes turn start offsets."""
    if text.strip().startswith("[DTMF"):
        return 0.3
    return 0.2 + len(words(text)) / WORDS_PER_SECOND


@dataclass
class Turn:
    speaker: str
    text: str
    offset_seconds: float | None = None

    @property
    def end_estimate(self) -> float | None:
        if self.offset_seconds is None:
            return None
        return self.offset_seconds + speech_seconds(self.text)


@dataclass
class Transcript:
    id: str
    turns: list[Turn]
    label: str | None = None
    source: str = "sim"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        ends = [t.end_estimate for t in self.turns if t.end_estimate is not None]
        return max(ends) if ends else 0.0

    def user_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.speaker == "user"]

    def window(self, until: float | None) -> "Transcript":
        """Only the turns that had *started* by ``until`` seconds."""
        if until is None:
            return self
        kept = [t for t in self.turns if t.offset_seconds is not None and t.offset_seconds <= until]
        return Transcript(self.id, kept, self.label, self.source, self.meta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "source": self.source,
            "meta": self.meta,
            "turns": [asdict(t) for t in self.turns],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        meta = dict(d.get("meta") or {})
        for key in ("difficulty", "scenario", "task", "outcome"):
            if key in d:
                meta[key] = d[key]
        turns = [
            Turn(
                speaker=_norm_speaker(t.get("speaker") or t.get("role")),
                text=str(t.get("text") or t.get("message") or ""),
                offset_seconds=_float(t.get("offset_seconds")),
            )
            for t in d["turns"]
        ]
        return cls(d["id"], turns, d.get("label"), d.get("source", "sim"), meta)

    @classmethod
    def load(cls, path: str | Path) -> "Transcript":
        return cls.from_dict(json.loads(Path(path).read_text()))

    @classmethod
    def from_calle(cls, call: dict[str, Any], label: str | None = None) -> "Transcript":
        """Build from a CALL-E call object (``GET /v1/calls/{id}``).

        A call can have several recipients and attempts; we keep the attempt
        with the most transcript turns (the one that actually connected).
        """
        best: list[dict[str, Any]] = []
        attempt_meta: dict[str, Any] = {}
        for recipient in call.get("recipients") or []:
            for attempt in recipient.get("attempts") or []:
                turns = attempt.get("transcript_turns") or []
                if len(turns) > len(best):
                    best = turns
                    attempt_meta = {k: attempt.get(k) for k in ("started_at", "ended_at", "status") if k in attempt}
        turns = [
            Turn(_norm_speaker(t.get("speaker")), t.get("text") or "", _float(t.get("offset_seconds")))
            for t in best
        ]
        meta = {
            "calle_call_id": call.get("id"),
            "status": call.get("status"),
            "task_completed": call.get("task_completed"),
            "completion_confidence": call.get("completion_confidence"),
            "structured_result": call.get("structured_result"),
            "evidence": call.get("evidence"),
            "task": call.get("task"),
            **attempt_meta,
        }
        return cls(call.get("id") or "calle-call", turns, label, "real-calle", meta)

    @classmethod
    def from_vapi(cls, call: dict[str, Any], label: str | None = None) -> "Transcript":
        """Build from a Vapi call object, seen from the *line's* side.

        Vapi's ``bot``/``assistant`` is our voicebot (-> ``bot``); the inbound
        caller (the CALL-E agent) becomes ``user``, the party we classify.
        """
        messages = (call.get("artifact") or {}).get("messages") or call.get("messages") or []
        turns = []
        for m in messages:
            role = m.get("role")
            if role not in ("bot", "assistant", "user"):
                continue
            offset = _float(m.get("secondsFromStart"))
            turns.append(Turn("bot" if role in ("bot", "assistant") else "user", m.get("message") or "", offset))
        meta = {
            "vapi_call_id": call.get("id"),
            "ended_reason": call.get("endedReason"),
            "started_at": call.get("startedAt"),
            "ended_at": call.get("endedAt"),
            "perspective": "inbound-line",
        }
        return cls(call.get("id") or "vapi-call", turns, label, "real-vapi", meta)


def _norm_speaker(value: Any) -> str:
    value = str(value or "").lower()
    if value in ("bot", "assistant", "agent", "calle"):
        return "bot"
    if value in ("user", "customer", "counterpart", "callee"):
        return "user"
    return "unknown"


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def load_dir(path: str | Path) -> list[Transcript]:
    return [Transcript.load(p) for p in sorted(Path(path).glob("*.json"))]
