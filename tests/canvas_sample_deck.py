"""Sample template deck covering every catalog variant (used by tests and previews)."""

from __future__ import annotations

from typing import Any

SAMPLE_ACTION: dict[str, Any] = {
    "action": "create",
    "title": "Онбординг клиентов · Q3 2026",
    "slides": [
        {"role": "cover", "variant": "cover.goals", "slots": {
            "eyebrow": "Customer Success · Q3 2026",
            "title": "Онбординг стал быстрее, но клиенты уходят на второй неделе",
            "lede": "Итоги квартала по 412 новым B2B-клиентам: что сработало, где теряем и что меняем в Q4.",
            "goals": [
                "Сократили время до первой ценности с 14 до 8,7 дня",
                "Нашли точку оттока: настройка интеграций на 2-й неделе",
                "Предлагаем 3 изменения и пилот на 60 клиентах",
            ],
        }},
        {"role": "cover", "variant": "cover.split-stat", "slots": {
            "eyebrow": "Customer Success · Q3 2026",
            "title": "Онбординг стал быстрее почти вдвое",
            "lede": "Итоги квартала по 412 новым B2B-клиентам и план изменений на Q4.",
            "hero_stat": {"value": "8,7", "label": "Время до первой ценности", "unit": "дня в среднем", "note": "было 14 дней в Q2 · −38%"},
        }},
        {"role": "cover", "variant": "cover.minimal", "slots": {
            "title": "Итоги онбординга за Q3",
            "lede": "Что изменилось для новых клиентов и где мы их теряем",
            "meta": [
                {"label": "Подготовила", "value": "команда Customer Success"},
                {"label": "Дата", "value": "13 октября 2026"},
            ],
        }},
        {"role": "context", "variant": "context.stats", "slots": {
            "eyebrow": "Данные", "title": "Что мы анализировали",
            "stats": [
                {"value": "412", "label": "новых клиентов, подключённых с июля по сентябрь"},
                {"value": "3", "label": "источника: CRM, логи продукта, 24 интервью"},
                {"value": "14 дней", "label": "окно наблюдения после старта каждого клиента"},
            ],
            "body": "Сравниваем с Q2 по тем же метрикам.",
        }},
        {"role": "context", "variant": "context.agenda", "slots": {
            "title": "О чём эта презентация",
            "agenda": [
                {"title": "Скорость онбординга", "note": "как изменилось время до первой ценности"},
                {"title": "Где теряем клиентов", "note": "воронка и причины оттока"},
                {"title": "План на Q4", "note": "три изменения и пилот"},
            ],
        }},
        {"role": "insight", "variant": "insight.callout", "slots": {
            "eyebrow": "Ключевой вывод", "label": "Главное",
            "claim": "Клиенты уходят не из-за сложности продукта, а из-за интеграций",
            "note": "68% отказавшихся застряли на подключении CRM.",
        }},
        {"role": "insight", "variant": "insight.quote", "slots": {
            "quote": "Продукт мы поняли за день. Неделю потом ждали, пока айтишники дадут доступ к CRM.",
            "who": "Операционный директор, логистическая компания",
        }},
        {"role": "insight", "variant": "insight.evidence", "slots": {
            "claim": "Интеграции — главная точка оттока",
            "evidence": [
                {"fact": "68%", "text": "ушедших клиентов не подключили CRM за 14 дней"},
                {"fact": "3×", "text": "ниже отток у тех, кто подключил интеграцию в первую неделю"},
            ],
        }},
        {"role": "metrics", "variant": "metrics.kpi", "slots": {
            "eyebrow": "Метрики квартала", "title": "Скорость выросла, удержание пока нет",
            "kpis": [
                {"label": "Время до ценности", "value": "8,7", "unit": "дня", "delta": "−38%", "delta_note": "к Q2", "delta_good": "down", "series": [14, 13.1, 12.4, 11, 10.2, 9.1, 8.7]},
                {"label": "Активация за 14 дней", "value": "61", "unit": "%", "delta": "+9 п.п.", "delta_good": "up", "series": [52, 53, 55, 54, 57, 60, 61]},
                {"label": "Отток в 1-й месяц", "value": "18", "unit": "%", "delta": "+2 п.п.", "delta_good": "down"},
            ],
        }},
        {"role": "metrics", "variant": "metrics.hero", "slots": {
            "kpis": [{"label": "Активация за первые 14 дней", "value": "61", "unit": "%"}],
            "note": "клиентов загрузили первые данные и пригласили коллегу",
            "compare": [{"label": "Q2", "value": "52%"}, {"label": "Цель Q4", "value": "70%"}],
        }},
        {"role": "chart", "slots": {
            "eyebrow": "Скорость по сегментам", "title": "Малый бизнес проходит онбординг быстрее всех",
            "sub": "Среднее время до первой ценности, дней",
            "chart": {"intent": "compare", "dec": 1, "x": ["Микро", "Малый", "Средний", "Крупный", "Гос."], "series": [{"values": [7.9, 6.2, 9.4, 12.8, 15.1]}], "highlight": 1},
            "source": "CRM, клиенты Q3",
        }},
        {"role": "chart", "slots": {
            "title": "Интеграции — причина №1",
            "chart": {"intent": "rank", "x": ["Не подключили CRM", "Нет времени у команды", "Дорого для масштаба", "Не хватило обучения"], "series": [{"values": [51, 22, 17, 14]}]},
        }},
        {"role": "chart", "slots": {
            "title": "Активация растёт во всех каналах",
            "chart": {"intent": "trend", "unit": "%", "x": ["Апр", "Май", "Июн", "Июл", "Авг", "Сен"], "series": [
                {"name": "Самостоятельно", "values": [44, 47, 50, 55, 61, 66]},
                {"name": "С менеджером", "values": [58, 57, 60, 61, 63, 64]},
            ]},
            "notes": [{"label": "Самостоятельно", "text": "+22 п.п. за полгода после запуска чек-листа"}],
        }},
        {"role": "chart", "slots": {
            "title": "Обращений стало меньше",
            "chart": {"intent": "trend", "dec": 1, "x": [f"Н{i}" for i in range(1, 11)], "series": [{"values": [4.4, 4.3, 4.5, 4.2, 4.1, 3.9, 4, 3.7, 3.6, 3.4]}]},
        }},
        {"role": "chart", "slots": {
            "title": "Новых клиентов больше почти везде",
            "chart": {"intent": "compare", "x": ["Микро", "Малый", "Средний"], "series": [{"name": "Q2", "values": [88, 104, 71]}, {"name": "Q3", "values": [112, 131, 92]}]},
        }},
        {"role": "chart", "slots": {
            "title": "Доля самостоятельных подключений выросла",
            "chart": {"intent": "structure", "series_names": ["Самостоятельно", "Менеджер", "Партнёр"], "rows": [{"label": "Q2", "values": [31, 49, 20]}, {"label": "Q3", "values": [41, 41, 18]}]},
        }},
        {"role": "chart", "slots": {
            "title": "Почти половина времени — на настройку",
            "chart": {"intent": "share", "unit": "ч", "x": ["Интеграции", "Обучение", "Встречи"], "series": [{"values": [46, 27, 18]}], "center": {"value": "38", "label": "клиентов на менеджера"}},
            "notes": [{"label": "Вывод", "text": "Снимем настройку — менеджеры поведут вдвое больше клиентов"}],
        }},
    ],
}

SAMPLE_ACTION_2: dict[str, Any] = {
    "action": "create",
    "title": "Часть 2",
    "slides": [
        {"role": "chart", "slots": {"title": "Водопад", "chart": {"intent": "bridge", "unit": "дн.", "dec": 1, "steps": [
            {"label": "Q2", "value": 14, "total": True}, {"label": "Чек-лист", "value": -3.1}, {"label": "Интеграции", "value": 1.3}, {"label": "Q3", "value": 12.2, "total": True}]}}},
        {"role": "chart", "slots": {"title": "Воронка", "chart": {"intent": "conversion", "warn_below": 75, "x": ["Договор", "Вход", "CRM", "Данные"], "series": [{"values": [412, 389, 241, 226]}]}}},
        {"role": "chart", "slots": {"title": "План/факт", "chart": {"intent": "target", "rows": [{"label": "Новые", "fact": 412, "plan": 380}, {"label": "Остались", "fact": 338, "plan": 360}]}}},
        {"role": "chart", "slots": {"title": "До/после", "chart": {"intent": "change", "a_name": "Q2", "b_name": "Q3", "unit": " ч", "rows": [{"label": "Вход", "a": 20, "b": 6}, {"label": "CRM", "a": 70, "b": 89}, {"label": "Отчёт", "a": 46, "b": 25}]}}},
        {"role": "chart", "slots": {"title": "Матрица", "chart": {"intent": "matrix", "unit": "%", "rows": ["Микро", "Крупный"], "cols": ["Н1", "Н2", "Н3"], "values": [[96, 81, 74], [91, 64, 52]]}}},
        {"role": "comparison", "variant": "comparison.two-col", "slots": {"title": "Кто настраивает интеграции", "options": [
            {"name": "Своя команда", "tag": "рекомендуем", "points": ["2 инженера", "Контроль качества у нас"]},
            {"name": "Партнёр", "points": ["Старт через 2 недели", "Качество зависит от партнёра"]}], "verdict": "Своя команда окупается при 200+ клиентах."}},
        {"role": "comparison", "variant": "comparison.table", "slots": {"title": "Три способа", "cols": ["Своя команда", "Партнёр", "Самообслуживание"], "rows": [
            {"criterion": "Запуск за 2 недели", "cells": ["no", "yes", "partial"]},
            {"criterion": "Стоимость", "cells": ["1,4 млн ₽", "1,1 млн ₽", "0,6 млн ₽"]}]}},
        {"role": "comparison", "variant": "comparison.before-after", "slots": {"title": "Старт клиента в Q4",
            "before": {"label": "Сейчас", "points": ["Инструкция по CRM письмом", "Ждём доступ от IT"]},
            "after": {"label": "В Q4", "points": ["Инженер подключает на созвоне", "Доступ запрашиваем при договоре"]}}},
        {"role": "content", "variant": "content.cards", "slots": {"title": "Три изменения на Q4", "items": [
            {"title": "Инженер внедрения", "text": "Подключает CRM на первом созвоне."},
            {"title": "Доступы заранее", "text": "Запрос уходит вместе с договором."},
            {"title": "Алерт на 3-й день", "text": "Если интеграции нет, менеджер звонит клиенту."}]}},
        {"role": "content", "variant": "content.profile", "slots": {"title": "Крупные клиенты", "tag": "зона риска", "items": [
            {"title": "Профиль", "text": "250+ сотрудников"}, {"title": "Где теряем", "text": "Неделя 2"}]}},
        {"role": "content", "variant": "content.text-media", "slots": {"title": "Первый созвон", "body": "Инженер проводит сессию с IT клиента.", "points": ["Доступы заранее"], "image_prompt": "abstract teal shapes"}},
        {"role": "plan", "variant": "plan.timeline", "slots": {"title": "Пилот за 8 недель", "periods": [
            {"label": "Нед. 1–2", "title": "Подготовка", "items": ["Найм инженера"]},
            {"label": "Нед. 3–4", "title": "Запуск", "items": ["Первые 20 клиентов", "Алерт"]},
            {"label": "Нед. 5–8", "title": "Итоги", "items": ["Решение о раскатке"]}]}},
        {"role": "plan", "variant": "plan.steps", "slots": {"title": "Кто отвечает", "steps": [
            {"title": "Утвердить бюджет", "owner": "Руководитель CS", "due": "13 окт"},
            {"title": "Пакет для службы безопасности", "owner": "Юрист", "due": "24 окт"}]}},
        {"role": "closing", "variant": "closing.next-steps", "slots": {"title": "Следующие шаги", "actions": [{"text": "Утвердить инженера", "due": "сегодня"}]}},
        {"role": "closing", "variant": "closing.ask", "slots": {"ask": "Выделить 1,4 млн ₽ на команду внедрения", "why": "Без неё пилот не стартует.", "options": [{"name": "Да", "text": "Пилот 3 ноября"}, {"name": "Отложить", "text": "Через партнёра"}]}},
        {"role": "appendix", "slots": {"caveats": ["Отток — 14 дней без активности"], "sources": [{"name": "CRM", "detail": "выгрузка 1 октября"}]}},
    ],
}

# Слайды с изображениями (1–3 картинки с подписями).
SAMPLE_ACTION_3 = {
    "action": "create",
    "title": "Часть 3",
    "slides": [
        {"role": "content", "variant": "content.gallery", "slots": {"title": "Как выглядит первый созвон", "images": [
            {"caption": "Инженер подключает CRM вместе с IT клиента", "image_prompt": "engineer and client at laptop, office"}]}},
        {"role": "content", "variant": "content.gallery", "slots": {"title": "Три шага подключения", "images": [
            {"caption": "Доступы запрашиваем вместе с договором", "image_prompt": "signing contract"},
            {"caption": "Интеграция на первом созвоне", "image_prompt": "video call screen"},
            {"caption": "Алерт, если данных нет на 3-й день", "image_prompt": "notification on phone"}]}},
    ],
}
