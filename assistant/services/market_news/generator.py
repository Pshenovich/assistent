"""Выпуск «Рынок» под организацию — перенос генератора /opt/obuchat-news (scripts/generate_news.py).

Общий пул источников (default_sources.json: RSS и запросы Google News) скачивается раз в день;
у организации добавляются свои запросы Google News по её конкурентам и темам и её RSS.
Оценка — по конкурентам и темам организации; «Главное», «Идеи для <компании>», «Сигналы» пишет Лео
одним вызовом модели (если модель недоступна — короткие нейтральные заготовки). Вёрстка — прежний шаблон,
поэтому «Дайджест → Рынок» в приложении показывает выпуск без доработок.
"""

from __future__ import annotations

import email.utils
import html
import json
import re
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCES = HERE / "default_sources.json"
TEMPLATE_FILE = HERE / "news.html"
USER_AGENT = "OrbitaMarketNews/1.0 (+https://assistent.networ.ru/)"
MAX_AGE_HOURS = 96
MAX_PER_FEED = 40

_POOL_LOCK = threading.Lock()
_POOL: dict[str, tuple[list["Article"], list[str]]] = {}  # день → (материалы общего пула, проверенные источники)


@dataclass
class Article:
    title: str
    url: str
    source: str
    category: str
    language: str
    summary: str = ""
    published: Optional[datetime] = None
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    def published_label(self, tz) -> str:
        if not self.published:
            return "дата не указана"
        return self.published.astimezone(tz).strftime("%d.%m.%Y %H:%M")


# ---------------------------------------------------------------- источники


def load_default_sources() -> dict[str, Any]:
    return json.loads(DEFAULT_SOURCES.read_text(encoding="utf-8"))


def fetch_url(url: str, timeout: int = 20) -> bytes:
    class _Redirect308(urllib.request.HTTPRedirectHandler):
        def http_error_308(self, request, fp, code, msg, headers):  # noqa: ANN001
            return self.http_error_301(request, fp, code, msg, headers)

    opener = urllib.request.build_opener(_Redirect308)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def text_from_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?</\1>", " ", value or "")
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _child(node: ET.Element, name: str) -> str:
    c = node.find(name)
    return "" if c is None or c.text is None else c.text.strip()


def parse_rss(payload: bytes, source: dict[str, Any]) -> list[Article]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall(".//item") or root.findall(f".//{atom}entry")
    out: list[Article] = []
    for item in items[:MAX_PER_FEED]:
        title = _child(item, "title") or _child(item, f"{atom}title")
        url = _child(item, "link")
        if not url:
            link = item.find(f"{atom}link")
            url = "" if link is None else link.attrib.get("href", "")
        desc = _child(item, "description") or _child(item, "summary") or _child(item, f"{atom}summary")
        pub = (_child(item, "pubDate") or _child(item, "published") or _child(item, "updated")
               or _child(item, f"{atom}published") or _child(item, f"{atom}updated"))
        if title and url:
            out.append(Article(
                title=text_from_html(title), url=url.strip(), source=source["name"],
                category=source.get("category", "general"), language=source.get("language", "unknown"),
                summary=text_from_html(desc)[:600], published=parse_date(pub),
            ))
    return out


def google_news_url(query: str, language: str) -> str:
    q = urllib.parse.quote_plus(query)
    if language == "ru":
        return f"https://news.google.com/rss/search?q={q}&hl=ru&gl=RU&ceid=RU:ru"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _feed_list(config: dict[str, Any]) -> list[dict[str, Any]]:
    feeds = list(config.get("rss_sources") or [])
    for q in config.get("google_news_queries") or []:
        feeds.append({"name": f"Google News: {q['query']}", "url": google_news_url(q["query"], q.get("language", "en")),
                      "language": q.get("language", "en"), "category": q.get("category", "search")})
    return feeds


def collect(feeds: list[dict[str, Any]]) -> tuple[list[Article], list[str]]:
    articles: list[Article] = []
    checked: list[str] = []
    for f in feeds:
        checked.append(f["name"])
        try:
            articles.extend(parse_rss(fetch_url(f["url"]), f))
        except Exception as e:
            print(f"[market_news] source failed {f['name']}: {e!r}")
    return articles, checked


def shared_pool(day: str) -> tuple[list[Article], list[str]]:
    """Общий пул на день — скачивается один раз на все организации."""
    with _POOL_LOCK:
        hit = _POOL.get(day)
        if hit is not None:
            return hit
    pool = collect(_feed_list(load_default_sources()))
    with _POOL_LOCK:
        _POOL.clear()
        _POOL[day] = pool
    return pool


def org_feeds(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Свои источники организации: Google News по конкурентам и темам (по 5 в запросе) и её RSS."""
    feeds: list[dict[str, Any]] = []

    def chunks(items: list[str], n: int = 5):
        for i in range(0, len(items), n):
            yield items[i:i + n]

    for group, category in ((settings.get("competitors") or [], "competitors"), (settings.get("topics") or [], "topics")):
        for part in chunks(list(group)):
            query = " OR ".join(f'"{x}"' if " " in x else x for x in part)
            for lang in ("ru", "en"):
                # названия компаний бывают обычными словами (Gong — чай, Cresta — река): ищем вместе с деловыми словами
                q = f"({query}) ({_BIZ_QUERY[lang]})" if category == "competitors" else query
                feeds.append({"name": f"Google News: {query}", "url": google_news_url(q, lang),
                              "language": lang, "category": category})
    for i, url in enumerate(settings.get("sources") or []):
        host = urllib.parse.urlsplit(url).netloc or f"Источник {i + 1}"
        feeds.append({"name": host, "url": url, "language": "unknown", "category": "own"})
    return feeds


# ---------------------------------------------------------------- отбор

_BIZ_QUERY = {"en": "AI OR sales OR CRM OR software OR startup OR platform",
              "ru": "ИИ OR продажи OR CRM OR сервис OR стартап OR платформа"}
# деловой контекст: без него упоминание конкурента не засчитываем
BIZ_WORDS = ["ai", "ии", "artificial intelligence", "искусственн", "sales", "продаж", "crm", "software", "saas",
             "startup", "стартап", "platform", "платформ", "customer", "клиент", "revenue", "выручк", "funding",
             "инвестиц", "acquir", "покупк", "launch", "запуск", "contact center", "контакт-центр", "call", "звонк",
             "agent", "агент", "analytics", "аналитик", "b2b", "enterprise", "product", "продукт"]


def _hits(keyword: str, hay: str) -> bool:
    """Короткие слова (≤5 букв) — только целиком: «gong» не совпадёт с «gongcha», «ии» — с «линии»."""
    k = keyword.lower()
    if len(k) <= 5:
        return re.search(r"(?<![\w])" + re.escape(k) + r"(?![\w])", hay) is not None
    return k in hay


def normalize_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(("utm_", "yclid", "fbclid", "gclid"))]
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), urllib.parse.urlencode(q), ""))


def dedupe(articles: list[Article]) -> list[Article]:
    seen_u: set[str] = set()
    seen_t: set[str] = set()
    out = []
    for a in articles:
        uk, tk = normalize_url(a.url), re.sub(r"\W+", "", a.title.lower())
        if uk in seen_u or tk in seen_t:
            continue
        seen_u.add(uk)
        seen_t.add(tk)
        out.append(a)
    return out


def score(articles: list[Article], *, high: list[str], medium: list[str], now: Optional[datetime] = None,
          competitors: Optional[list[str]] = None) -> None:
    """Как в исходном генераторе: ключевые слова, категория, язык, свежесть.

    Конкурент засчитывается, только если рядом есть деловой контекст (темы организации или BIZ_WORDS),
    иначе «Gong cha открылся в ОАЭ» попадал бы в выпуск про продажи."""
    comp = {k.lower() for k in (competitors or []) if k.strip()}
    hi = [k.lower() for k in high if k.strip()]
    md = [k.lower() for k in medium if k.strip()]
    ctx = [k for k in hi if k not in comp] + md + BIZ_WORDS
    now = now or datetime.now(timezone.utc)
    for a in articles:
        a.score, a.reasons = 0, []
        hay = f"{a.title} {a.summary}".lower()
        business = any(_hits(k, hay) for k in ctx)
        for k in hi:
            if _hits(k, hay) and (k not in comp or business):
                a.score += 5
                a.reasons.append(k)
        for k in md:
            if _hits(k, hay):
                a.score += 2
                a.reasons.append(k)
        if not a.reasons:
            continue  # ни конкурентов, ни тем — материал не про нас, бонусы за категорию и свежесть не даём
        if a.category in {"competitors", "conversation_intelligence", "russian_speech_analytics", "own"}:
            a.score += 4
        if a.category in {"topics", "contact_center", "crm_ai", "russian_crm"}:
            a.score += 2
        if a.language == "ru":
            a.score += 1
        if a.published:
            age = (now - a.published.astimezone(timezone.utc)).total_seconds() / 3600
            a.score += 3 if age <= 36 else (1 if age <= 72 else (-3 if age > 168 else 0))


def short_summary(a: Article) -> str:
    text = re.sub(r"\s+", " ", a.summary or a.title).strip()
    return text[:257].rsplit(" ", 1)[0] + "..." if len(text) > 260 else text


def evidence_for(takeaway: str, articles: list[Article]) -> list[Article]:
    stop = {"для", "что", "это", "как", "или", "при", "над", "уже", "если", "рынок", "важно", "стоит", "может",
            "становится", "the", "and", "with", "from"}
    words = {w.lower() for w in re.findall(r"[A-Za-zА-Яа-я0-9.\-]+", takeaway) if len(w) > 3 and w.lower() not in stop}
    scored = [(sum(1 for w in words if w in f"{a.title} {a.summary} {a.source} {a.category}".lower()) + a.score, a)
              for a in articles]
    scored = [x for x in scored if x[0] > x[1].score] or [(a.score, a) for a in articles[:5]]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in scored[:3]]


# ---------------------------------------------------------------- редакция (Лео)

EDITOR_SYSTEM = """Ты — редактор ежедневного отраслевого дайджеста для руководителя компании.
Тебе дают описание компании (чем занимается, конкуренты, темы) и список материалов дня.
Верни JSON строго с ключами summary, ideas, signals:
summary — 3–5 стратегических выводов для этой компании;
ideas — 3–5 применимых идей для её продукта, продаж или маркетинга;
signals — 2–4 гипотезы или сигнала, которые стоит проверить.
Пиши по-русски, коротко (одна-две фразы на пункт), опирайся только на материалы, не выдумывай фактов.
Не пиши, что материалов про саму компанию нет, — делай выводы для неё из того, что происходит на рынке и у конкурентов.
Пропускай материалы, которые явно не про её рынок (совпадение названий, спорт, погода и т. п.)."""


def editorial(org_name: str, settings: dict[str, Any], top: list[Article]) -> Optional[dict[str, list[str]]]:
    if not top:
        return None
    from assistant.nlu import llm

    company = [f"Компания: {org_name}"]
    if settings.get("about"):
        company.append(f"Чем занимается: {settings['about']}")
    if settings.get("competitors"):
        company.append("Конкуренты: " + ", ".join(settings["competitors"]))
    if settings.get("topics"):
        company.append("Темы: " + ", ".join(settings["topics"]))
    items = [{"title": a.title, "source": a.source, "summary": short_summary(a)} for a in top[:12]]
    try:
        raw = llm._chat(EDITOR_SYSTEM, "\n".join(company) + "\n\nМатериалы:\n" + json.dumps(items, ensure_ascii=False),
                        operation="market_digest", timeout=120, temperature=0.2, max_tokens=1200)
        text = str(raw or "").strip()
        text = text[text.find("{"): text.rfind("}") + 1] if "{" in text else text
        data = json.loads(text)
        if all(isinstance(data.get(k), list) and data[k] for k in ("summary", "ideas", "signals")):
            return {k: [str(x).strip() for x in data[k] if str(x).strip()][:6] for k in ("summary", "ideas", "signals")}
    except Exception as e:
        print(f"[market_news] editorial org={org_name!r} err={e!r}")
    return None


FALLBACK = {
    "summary": ["Главное дня — в материалах ниже: Лео не смог сегодня написать выводы, откройте топ материалов."],
    "ideas": ["Посмотрите, что из материалов про конкурентов стоит проверить у себя в продукте или продажах."],
    "signals": ["Если тема повторяется несколько дней подряд — переносите её из наблюдения в гипотезу."],
}


# ---------------------------------------------------------------- вёрстка


def _render_article(a: Article, tz) -> str:
    reasons = ", ".join(dict.fromkeys(a.reasons[:4])) or "общая релевантность"
    return f"""
    <article class="card">
      <div class="meta">{html.escape(a.source)} · {html.escape(a.category)} · {html.escape(a.published_label(tz))} · score {a.score}</div>
      <h3><a href="{html.escape(a.url)}" target="_blank" rel="noopener noreferrer">{html.escape(a.title)}</a></h3>
      <p>{html.escape(short_summary(a))}</p>
      <p class="why">Почему важно: {html.escape(reasons)}</p>
    </article>
    """


def _render_strategy(items: list[str], articles: list[Article], tz) -> str:
    out = []
    for i, item in enumerate(items):
        links = "\n".join(
            f'<li><a href="{html.escape(a.url)}" target="_blank" rel="noopener noreferrer">{html.escape(a.title)}</a>'
            f"<span>{html.escape(a.source)} · {html.escape(a.published_label(tz))}</span></li>"
            for a in evidence_for(item, articles)
        )
        out.append(
            f'<div class="strategy-item"><button class="strategy-trigger" type="button" aria-expanded="false" '
            f'aria-controls="strategy-tip-{i}"><span>{html.escape(item)}</span><span class="strategy-hint">На основе чего?</span>'
            f'</button><div class="strategy-tooltip" id="strategy-tip-{i}" role="tooltip" hidden>'
            f"<p>Вывод основан на этих материалах:</p><ul>{links}</ul></div></div>"
        )
    return "\n".join(out)


def _li(items: list[str]) -> str:
    return "\n".join(f"<li>{html.escape(x)}</li>" for x in items)


def _lead(org_name: str, settings: dict[str, Any]) -> str:
    parts = list(settings.get("topics") or []) + list(settings.get("competitors") or [])
    what = ", ".join(parts[:8])
    return f"Собрано для {org_name}" + (f": {what}." if what else ".") + " Выводы и идеи — Лео по материалам дня."


def build_issue(org: dict[str, Any], settings: dict[str, Any], *, now: Optional[datetime] = None,
                tz=None) -> dict[str, Any]:
    """Собрать выпуск организации: {date, html, articles}."""
    from zoneinfo import ZoneInfo

    tz = tz or ZoneInfo("Europe/Moscow")
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(tz).date().isoformat()
    defaults = load_default_sources()
    pool, checked = shared_pool(day)
    own, own_checked = collect(org_feeds(settings))
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    fresh = [a for a in (own + list(pool)) if a.published is None or a.published >= cutoff]
    # копии, чтобы оценка одной организации не задевала общий пул других
    unique = [Article(**{**a.__dict__, "reasons": []}) for a in dedupe(fresh)]
    high = list(settings.get("competitors") or []) + list(settings.get("topics") or [])
    score(unique, high=high, medium=list((defaults.get("scoring_keywords") or {}).get("medium") or []), now=now,
          competitors=list(settings.get("competitors") or []))
    relevant = [a for a in unique if a.score > 0 and a.reasons]
    if not relevant:  # по конкурентам и темам сегодня пусто — свежее из отраслевых лент общего пула
        pool_urls = {normalize_url(a.url) for a in pool}
        relevant = sorted((a for a in unique if normalize_url(a.url) in pool_urls),
                          key=lambda a: a.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:10]
    top = sorted(relevant, key=lambda a: (a.score, a.published or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)[:20]
    must = top[:10]
    comp = [a for a in top if a.category in {"competitors", "own"}][:6] or top[:4]
    ed = editorial(org["name"], settings, must) or FALLBACK
    title = f"{org['name']}: отраслевой дайджест за {now.astimezone(tz).strftime('%d.%m.%Y')}"
    page = Template(TEMPLATE_FILE.read_text(encoding="utf-8")).safe_substitute(
        title=html.escape(title),
        org_name=html.escape(org["name"]),
        lead=html.escape(_lead(org["name"], settings)),
        generated_at=now.astimezone(tz).strftime("%d.%m.%Y %H:%M"),
        summary_items=_render_strategy(ed["summary"][:6], must, tz),
        top_articles="\n".join(_render_article(a, tz) for a in must),
        competitor_articles="\n".join(_render_article(a, tz) for a in comp),
        product_ideas=_li(ed["ideas"][:6]),
        signals=_li(ed["signals"][:5]),
        checked_sources=", ".join(html.escape(s) for s in own_checked + checked),
        article_count=str(len(relevant)),
        watchlist=", ".join(html.escape(x) for x in (settings.get("competitors") or [])) or "—",
    )
    return {"date": day, "html": page, "articles": len(relevant)}
