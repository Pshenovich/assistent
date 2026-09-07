"""HTML дашборда решений Executive Board."""

from __future__ import annotations

import html
from typing import Any

from assistant.board import store


def _e(text: Any) -> str:
    return html.escape(str(text or ""))


def render_index(*, board_base: str, qs: str = "") -> str:
    store.init_db()
    stats = store.dashboard_stats()
    decisions = store.list_all_decisions(limit=80)
    rows = []
    for d in decisions:
        did = _e(d.get("id"))
        conf = int(round(float(d.get("confidence") or 0) * 100))
        payload = d.get("payload") or {}
        sev = _e((payload.get("severity") if isinstance(payload, dict) else None) or "—")
        meeting = store.get_meeting(str(d.get("meeting_id") or "")) or {}
        analysis = meeting.get("analysis") if isinstance(meeting.get("analysis"), dict) else {}
        if analysis.get("severity"):
            sev = _e(analysis.get("severity"))
        follow = store.due_followups()
        follow_n = sum(1 for f in follow if f.get("decision_id") == d.get("id"))
        reviews = store.list_reviews(str(d.get("id") or ""))
        scores = ""
        rounds = store.list_rounds(str(d.get("meeting_id") or ""))
        if rounds:
            last = rounds[-1].get("scores") or {}
            if last:
                scores = ", ".join(f"{k}={last[k]}" for k in list(last)[:3])
        rows.append(
            "<tr>"
            f"<td>{_e(str(d.get('created_at') or '')[:16])}</td>"
            f"<td>{sev}</td>"
            f"<td><a href=\"{board_base}/{did}{qs}\">{_e((d.get('problem') or '')[:90])}</a></td>"
            f"<td>{_e((d.get('decision') or '')[:90])}</td>"
            f"<td>{conf}%</td>"
            f"<td>{len(reviews)}</td>"
            f"<td>{follow_n}</td>"
            f"<td class=\"muted\">{_e(scores)}</td>"
            "</tr>"
        )
    table = "\n".join(rows) or "<tr><td colspan=8>Решений пока нет.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Executive Board</title>
<style>
:root {{
  --bg:#09090b; --surface:#141417; --border:#2a2a32; --text:#f4f4f5;
  --muted:#a1a1aa; --accent:#22d3ee;
}}
body {{ margin:0; font-family: system-ui, sans-serif; background:var(--bg); color:var(--text); }}
.app {{ max-width:1100px; margin:0 auto; padding:2rem 1.25rem; }}
h1 {{ margin:0 0 .4rem; }}
.sub {{ color:var(--muted); margin:0 0 1.5rem; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:1rem; margin-bottom:1.5rem; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:1rem; }}
.card strong {{ font-size:1.4rem; display:block; }}
table {{ width:100%; border-collapse:collapse; background:var(--surface); border-radius:12px; overflow:hidden; }}
th, td {{ text-align:left; padding:.7rem .8rem; border-bottom:1px solid var(--border); vertical-align:top; }}
th {{ color:var(--muted); font-size:.8rem; text-transform:uppercase; letter-spacing:.04em; }}
a {{ color:var(--accent); text-decoration:none; }}
.muted {{ color:var(--muted); font-size:.85rem; }}
</style>
</head>
<body>
<div class="app">
  <h1>AI Executive Board</h1>
  <p class="sub">Решения, KPI и качество дискуссии</p>
  <div class="metrics">
    <div class="card"><span class="muted">Совещания</span><strong>{stats['meetings']}</strong></div>
    <div class="card"><span class="muted">Решения</span><strong>{stats['decisions']}</strong></div>
    <div class="card"><span class="muted">Открытые действия</span><strong>{stats['open_actions']}</strong></div>
    <div class="card"><span class="muted">Follow-up</span><strong>{stats['active_followups']}</strong></div>
  </div>
  <table>
    <thead><tr>
      <th>Дата</th><th>Severity</th><th>Проблема</th><th>Решение</th>
      <th>Conf</th><th>Review</th><th>Due</th><th>Scores</th>
    </tr></thead>
    <tbody>{table}</tbody>
  </table>
</div>
</body>
</html>"""


def render_decision(decision_id: str, *, board_base: str, qs: str = "") -> str | None:
    store.init_db()
    d = store.get_decision(decision_id)
    if not d:
        return None
    meeting = store.get_meeting(str(d.get("meeting_id") or "")) or {}
    actions = store.list_actions(decision_id)
    reviews = store.list_reviews(decision_id)
    rounds = store.list_rounds(str(d.get("meeting_id") or ""))
    messages = store.list_messages(str(d.get("meeting_id") or ""))
    analysis = meeting.get("analysis") if isinstance(meeting.get("analysis"), dict) else {}

    def lis(items: list[Any]) -> str:
        if not items:
            return "<p class='muted'>—</p>"
        return "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in items) + "</ul>"

    act_html = "".join(
        "<tr>"
        f"<td>{_e(a.get('action'))}</td>"
        f"<td>{_e(a.get('owner'))}</td>"
        f"<td>{_e(a.get('deadline'))}</td>"
        f"<td>{_e(a.get('success_metric'))}</td>"
        f"<td>{_e(a.get('status'))}</td>"
        "</tr>"
        for a in actions
    ) or "<tr><td colspan=5>—</td></tr>"

    rev_html = "".join(
        f"<div class='card'><strong>{_e(r.get('verdict') or 'review')}</strong>"
        f"<p class='muted'>{_e(str(r.get('created_at') or '')[:19])}</p>"
        f"<pre>{_e(r.get('user_results'))}</pre></div>"
        for r in reviews
    ) or "<p class='muted'>Review ещё не было.</p>"

    score_html = "".join(
        f"<li>Раунд {int(r.get('round') or 0)} ({_e(r.get('mode'))}): "
        f"{_e(r.get('scores'))}</li>"
        for r in rounds
    ) or "<li>—</li>"

    debate = "".join(
        f"<p><b>{_e(m.get('agent'))}</b> · r{int(m.get('round') or 0)}<br>{_e(m.get('content'))}</p>"
        for m in messages
        if m.get("agent") != "CHAIR"
    )
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Решение</title>
<style>
:root {{ --bg:#09090b; --surface:#141417; --border:#2a2a32; --text:#f4f4f5; --muted:#a1a1aa; --accent:#22d3ee; }}
body {{ margin:0; font-family:system-ui,sans-serif; background:var(--bg); color:var(--text); }}
.app {{ max-width:900px; margin:0 auto; padding:2rem 1.25rem; }}
a {{ color:var(--accent); text-decoration:none; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:1rem 1.1rem; margin:1rem 0; }}
.muted {{ color:var(--muted); }}
pre {{ white-space:pre-wrap; }}
table {{ width:100%; border-collapse:collapse; }}
th,td {{ text-align:left; padding:.5rem; border-bottom:1px solid var(--border); }}
</style>
</head>
<body>
<div class="app">
  <p><a href="{board_base}{qs}">← К списку</a></p>
  <h1>{_e(d.get('problem') or 'Решение')}</h1>
  <p class="muted">{_e(str(d.get('created_at') or '')[:19])} · severity {_e(analysis.get('severity') or '—')} · confidence {int(round(float(d.get('confidence') or 0)*100))}%</p>
  <div class="card"><h2>Решение</h2><p>{_e(d.get('decision'))}</p></div>
  <div class="card"><h2>Почему</h2>{lis(d.get('why') or [])}</div>
  <div class="card"><h2>KPI</h2>{lis(d.get('kpis') or [])}</div>
  <div class="card"><h2>Риски</h2>{lis(d.get('risks') or [])}</div>
  <div class="card"><h2>Действия</h2>
    <table><thead><tr><th>Action</th><th>Owner</th><th>Deadline</th><th>Metric</th><th>Status</th></tr></thead>
    <tbody>{act_html}</tbody></table>
  </div>
  <div class="card"><h2>Quality scores</h2><ul>{score_html}</ul></div>
  <div class="card"><h2>Review</h2>{rev_html}</div>
  <div class="card"><h2>Дискуссия</h2>{debate or "<p class='muted'>—</p>"}</div>
</div>
</body>
</html>"""
