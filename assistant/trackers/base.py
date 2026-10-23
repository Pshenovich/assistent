"""Общий интерфейс трекера: доски, стадии, создание задачи."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


class TrackerError(RuntimeError):
    """Ошибка с текстом для пользователя."""


class TrackerNotConnected(TrackerError):
    pass


@dataclass
class Board:
    id: str
    name: str
    kind: str = "group"  # group / scrum / personal

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Stage:
    id: str
    name: str
    sort: int = 0
    color: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskDraft:
    title: str
    description: str = ""
    board_id: str = ""
    stage_id: str = ""
    responsible: str = ""
    deadline: str = ""  # YYYY-MM-DD


@dataclass
class CreatedTask:
    external_id: str
    url: str
    title: str
    extra: dict[str, Any] = field(default_factory=dict)


class TrackerProvider(Protocol):
    id: str
    title: str
    short_title: str

    def is_connected(self, uid: int) -> bool: ...

    def connection(self, uid: int) -> dict[str, Any]: ...

    def list_boards(self, uid: int) -> list[Board]: ...

    def list_stages(self, uid: int, board_id: str) -> list[Stage]: ...

    def create_task(self, uid: int, draft: TaskDraft) -> CreatedTask: ...
