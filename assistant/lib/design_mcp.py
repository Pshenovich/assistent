"""MCP-сервер макета (Streamable HTTP без потоков): агент по ссылке разработчика читает фреймы,
спецификацию, HTML/CSS, превью и картинки одного листа-макета. Только чтение.

Транспорт — в usage_server (POST /mcp/design/<токен>); здесь — разбор JSON-RPC 2.0 и инструменты.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from assistant.lib import board_doc
from assistant.lib import design_spec

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "orbita-design"
SERVER_VERSION = "1.0.0"
MAX_TEXT = 200_000  # ответ инструмента: длиннее — режем с пометкой

INSTRUCTIONS = (
    "Макет из Орбиты (аналог Figma): экраны-фреймы с вложенными фреймами, автолэйаутом, текстами, векторами и "
    "картинками. Порядок работы: list_frames → get_frame_image (как выглядит) → get_frame_spec (точные размеры, "
    "отступы, цвета, шрифты) → get_frame_code (готовая HTML/CSS-основа) → get_asset для картинок. "
    "Размеры — в px. Автолэйаут: layout.mode row|column|grid ≈ flex/grid, sizing w/h: fixed | fill | hug. "
    "Повторяющиеся цвета и стили текста — в tokens: заведите из них переменные проекта, а не копируйте значения."
)


@dataclass
class DesignSource:
    """Что видит агент: тело листа, имя заметки/листа и превью фреймов (PNG от клиента)."""

    title: str
    load_body: Callable[[], str]
    load_preview: Callable[[str], Optional[bytes]]


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_FRAME_ARG = {"type": "string", "description": "id фрейма из list_frames или его имя"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_frames",
        "title": "Фреймы макета",
        "description": "Экраны (верхние фреймы) макета: id, имя, размер, число элементов.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_frame_image",
        "title": "Картинка фрейма",
        "description": "Как выглядит фрейм: PNG (если макет открывали после выдачи ссылки) или SVG-набросок.",
        "inputSchema": {"type": "object", "properties": {"frame": _FRAME_ARG}, "required": ["frame"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_frame_spec",
        "title": "Спецификация фрейма",
        "description": (
            "Дерево узлов фрейма: тип, имя, x/y от родителя, w/h, sizing (fixed|fill|hug), layout (автолэйаут: "
            "mode, gap, padding [t r b l], align, justify), fill, stroke, radius, effects, text (шрифт, размер, "
            "вес, межстрочный, цвет), картинки; плюс tokens (цвета и стили текста) и краткое дерево outline."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame": _FRAME_ARG,
                "node": {"type": "string", "description": "id вложенного фрейма — только его поддерево (необязательно)"},
            },
            "required": ["frame"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_frame_code",
        "title": "HTML/CSS фрейма",
        "description": (
            "Готовая основа вёрстки: HTML и CSS (flex/grid для автолэйаута, абсолютные позиции для остального), "
            "ссылка на Google Fonts. Картинки — assets/<id>.<ext>, сами файлы — get_asset. "
            "Переведите в компоненты своего стека; имена классов — из имён слоёв."
        ),
        "inputSchema": {"type": "object", "properties": {"frame": _FRAME_ARG}, "required": ["frame"], "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_asset",
        "title": "Картинка из макета",
        "description": "Файл картинки по id из assets (get_frame_spec / get_frame_code).",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "id картинки"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
]


def _text(s: str) -> dict[str, Any]:
    if len(s) > MAX_TEXT:
        s = s[:MAX_TEXT] + "\n… (обрезано: запросите вложенный фрейм через node)"
    return {"type": "text", "text": s}


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _frame_or_fail(body: str, ref: str) -> str:
    fr = design_spec.find_frame(body, ref)
    if not fr:
        names = ", ".join(f"{f['name']} ({f['id']})" for f in design_spec.list_frames(body)[:30]) or "фреймов нет"
        raise ValueError(f"Фрейм «{ref}» не найден. Есть: {names}")
    return fr["id"]


def call_tool(src: DesignSource, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """tools/call → {content, isError?}; ошибки инструмента — isError, а не ошибка протокола."""
    args = args if isinstance(args, dict) else {}
    try:
        body = src.load_body()
        if name == "list_frames":
            frames = design_spec.list_frames(body)
            return {"content": [_text(_json({"title": src.title, "frames": frames}))]}
        if name == "get_frame_image":
            fid = _frame_or_fail(body, str(args.get("frame") or ""))
            png = src.load_preview(fid)
            if png:
                return {"content": [{"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"}]}
            svg = design_spec.frame_svg(body, fid) or ""
            note = "PNG-превью ещё нет (появится, когда макет откроют в Орбите). Упрощённый SVG:\n"
            return {"content": [_text(note + svg)]}
        if name == "get_frame_spec":
            fid = _frame_or_fail(body, str(args.get("frame") or ""))
            node = str(args.get("node") or "").strip()
            spec = design_spec.frame_spec(body, node or fid)
            if spec is None:
                raise ValueError("Вложенный фрейм не найден")
            return {"content": [_text(design_spec.spec_outline(spec)), _text(_json(spec))]}
        if name == "get_frame_code":
            fid = _frame_or_fail(body, str(args.get("frame") or ""))
            code = design_spec.frame_code(body, fid) or {}
            out = (f"<!-- {code.get('name')} -->\n{code.get('font_link') or ''}\n\n```html\n{code.get('html')}\n```\n\n"
                   f"```css\n{code.get('css')}\n```")
            if code.get("assets"):
                out += "\n\nКартинки: " + _json(code["assets"])
            return {"content": [_text(out)]}
        if name == "get_asset":
            aid = str(args.get("id") or "").split(".", 1)[0]
            el = board_doc.parse_board(body)["elements"].get(aid)
            src_uri = str((el or {}).get("src") or "")
            if not el or el.get("type") != "image" or "," not in src_uri:
                raise ValueError("Картинка не найдена")
            head, b64 = src_uri.split(",", 1)
            mime = head[5:].split(";", 1)[0] or "image/png"
            return {"content": [{"type": "image", "data": b64, "mimeType": mime}]}
        raise RpcError(-32602, f"Нет инструмента {name}")
    except RpcError:
        raise
    except ValueError as e:
        return {"content": [_text(str(e))], "isError": True}


def handle(src: DesignSource, msg: Any) -> Optional[dict[str, Any]]:
    """Одно сообщение JSON-RPC → ответ (None — для уведомлений и ответов клиента)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method = msg.get("method")
    mid = msg.get("id")
    if method is None or "id" not in msg:
        return None  # уведомление (notifications/initialized и т. п.) или ответ клиента
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    try:
        if method == "initialize":
            want = str(params.get("protocolVersion") or "")
            result: Any = {
                "protocolVersion": want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": f"Макет: {src.title}", "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = call_tool(src, str(params.get("name") or ""), params.get("arguments") or {})
        elif method in ("resources/list", "prompts/list"):
            result = {"resources": []} if method == "resources/list" else {"prompts": []}
        else:
            raise RpcError(-32601, f"Method not found: {method}")
    except RpcError as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": e.message}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def handle_payload(src: DesignSource, payload: Any) -> Any:
    """Тело POST: одно сообщение или пакет. None — ответа нет (только уведомления) → 202."""
    if isinstance(payload, list):
        out = [r for r in (handle(src, m) for m in payload) if r is not None]
        return out or None
    return handle(src, payload)
