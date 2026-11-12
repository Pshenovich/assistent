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
class Person:
    id: str
    name: str
    position: str = ""
    photo: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskDraft:
    title: str
    description: str = ""
    board_id: str = ""
    stage_id: str = ""
    responsible: str = ""
    responsible_id: str = ""  # выбран из списка — ищем по ID, не по имени
    deadline: str = ""  # YYYY-MM-DD
    parent_id: str = ""  # задача трекера, подзадачей которой будет эта


@dataclass
class CreatedTask:
    external_id: str
    url: str
    title: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRef:
    """Задача трекера для ссылки из Орбиты (блокер): название, ссылка, стадия/статус."""
    external_id: str
    title: str
    url: str
    status: str = ""
    stage: str = ""
    closed: bool = False
    responsible: str = ""
    deadline: str = ""
    responsible_id: str = ""  # id исполнителя в трекере — сверяем с владельцем вебхука («на меня или нет»)

    def public(self) -> dict[str, Any]:
        return asdict(self)


class TrackerProvider(Protocol):
    id: str
    title: str
    short_title: str

    def is_connected(self, uid: int) -> bool: ...

    def connection(self, uid: int) -> dict[str, Any]: ...

    def list_boards(self, uid: int) -> list[Board]: ...

    def list_stages(self, uid: int, board_id: str) -> list[Stage]: ...

    def search_users(self, uid: int, query: str) -> list[Person]: ...

    def create_task(self, uid: int, draft: TaskDraft) -> CreatedTask: ...

    # Задача трекера по номеру и поиск (для блокеров). Не умеет — нет методов.
    # def get_task(self, uid: int, external_id: str) -> TaskRef: ...
    # def search_tasks(self, uid: int, query: str, limit: int = 20) -> list[TaskRef]: ...

    # Сделать задачу подзадачей другой ("" — самостоятельной). Не умеет — нет метода.
    # def set_parent(self, uid: int, external_id: str, parent_id: str) -> None: ...
