"""Кто скрывается за «Спикер N»: догадки по тексту встречи, отдельно от саммари.

LLM предлагает (самопредставления, обращения по имени, участники встречи),
код проверяет: метка есть в записи, имя сводится к контакту, если совпало однозначно.
В текст догадки не попадают — только предвыбор в «Кто есть кто».
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

MAX_CHARS = 14000

SPEAKER_GUESS_SYSTEM = """Ты определяешь, кто из людей скрывается за метками «Спикер N» в транскрипте встречи.
Опирайся только на текст:
- самопредставления («это Ира из продаж», «я Петя») → этот спикер;
- обращения по имени и ответ следующего спикера («Саша, что скажешь?» → отвечающий, вероятно, Саша);
- кто кого упоминает в третьем лице («Дима обещал…» — значит, говорящий не Дима);
- роли («как руководитель отдела…»), участники встречи (attendees) и владелец записи (owner).
Владелец записи обычно один из спикеров, но не утверждай без признаков.
Диаризация может ошибаться — не завышай confidence: 0.9+ только при самопредставлении или прямом ответе на обращение.
Нет догадки — name "" и confidence 0. Не выдумывай имена, которых нет ни в тексте, ни в участниках.
Верни ТОЛЬКО JSON:
{"speaker_map":{"Спикер 1":{"name":"","confidence":0.0,"evidence":"короткая цитата или причина"}}}"""


def _transcript_excerpt(segments: list[dict[str, Any]]) -> str:
    """Начало встречи (там представляются) + самые длинные реплики каждого спикера."""
    from assistant.integrations.transcribe import formatted_from_segments
    from assistant.lib.transcript_render import samples

    full = formatted_from_segments(segments)
    if len(full) <= MAX_CHARS:
        return full
    head = full[: MAX_CHARS * 2 // 3]
    cut = head.rfind("\n\n")
    head = head[:cut] if cut > 0 else head
    extra = []
    for sp, items in samples(segments, per_speaker=4).items():
        extra.extend(f"{sp}:\n{x['text']}" for x in items)
    return head + "\n\n[…далее отдельные реплики…]\n\n" + "\n\n".join(extra)[: MAX_CHARS // 3]


def guess(
    segments: list[dict[str, Any]],
    *,
    context_payload: Optional[dict[str, Any]],
    people: list[dict[str, Any]],
    chat: Callable[..., str],
    model: str,
) -> dict[str, dict[str, Any]]:
    from assistant.lib import summary_normalize as sn
    from assistant.lib.llm_json import strip_json_from_markdown

    labels = sorted({str(s.get("speaker") or "") for s in segments} - {""})
    if len(labels) < 2:
        return {}
    text = _transcript_excerpt(segments)
    payload = {"context": context_payload or {}, "speakers": labels, "transcript": text}
    raw = chat(
        SPEAKER_GUESS_SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        operation="speaker_guess",
        model=model,
        timeout=90,
        temperature=0.0,
    )
    try:
        obj = json.loads(strip_json_from_markdown(raw or ""))
    except (json.JSONDecodeError, TypeError):
        return {}
    smap = sn.clean_speaker_map((obj or {}).get("speaker_map") if isinstance(obj, dict) else None, text)
    out: dict[str, dict[str, Any]] = {}
    taken: set[str] = set()
    # от уверенных к неуверенным: одно имя — одному спикеру
    for label, v in sorted(smap.items(), key=lambda kv: -float(kv[1].get("confidence") or 0)):
        name = str(v.get("name") or "").strip()
        if label not in labels or not name:
            continue
        person = sn.match_person(name, people)
        if person:
            name = str(person.get("name") or name)
        if name.lower() in taken:
            continue
        taken.add(name.lower())
        out[label] = {**v, "name": name, "confirmed": False, "source": "guess"}
    return out
