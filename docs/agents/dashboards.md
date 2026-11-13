# Дашборды Орбиты — инструкция для ИИ-агента

Дашборд — страница в Орбите (раздел «Дашборды») с цифрами, графиками и таблицами, которая **сама обновляется**: данные присылает сервер компании по расписанию. Ты (агент) делаешь две вещи:

1. Через MCP `orbita-dashboards` создаёшь дашборд, источники данных и виджеты.
2. На сервере компании ставишь небольшой скрипт по расписанию, который отправляет данные в источник.

Люди смотрят дашборд в Орбите, а Лео (ассистент Орбиты) отвечает им на вопросы по этим цифрам.

## Что можно и чего нельзя

- Можно: смотреть дашборды владельца токена; создавать дашборды; создавать и менять источники; добавлять, менять, переставлять и убирать виджеты; менять ключ источника; делать пробную отправку.
- Нельзя: удалять дашборды и источники и менять доступ — это делает человек в Орбите.
- Виды виджетов — **только из каталога** (`widget_types`, ниже). Своих цветов, HTML и CSS нет: неизвестные опции отбрасываются, неизвестный тип — ошибка.
- Права — как у владельца токена: создавать в организации можно, только если он в ней состоит; менять — только там, где он «Редактирует» или «Владелец».

## Подключение

Токен выпускает человек: Орбита → Профиль → Аккаунт → «Доступ для ИИ» → «Дашборды: чтение и запись». Токен показывается один раз.

```bash
claude mcp add --transport http orbita-dashboards https://getorbita.me/mcp/dashboards --header "Authorization: Bearer orb_…"
```

Этот текст доступен без токена: `GET https://getorbita.me/mcp/dashboards/guide.md`.

## Порядок работы

1. `list_dashboards` — проверь, нет ли уже подходящего дашборда; в ответе есть `my_orgs`, там `org_id` организаций.
2. `create_dashboard` (`title`, `org_id` — для дашборда компании; без него дашборд будет личным).
3. `create_dataset` на каждый поток данных. В ответе `ingest_url`, `ingest_key` (**показывается один раз**) и готовые примеры `curl` и Python.
4. `add_widget` — виджеты из каталога, привязанные к источникам.
5. Скрипт на сервере компании: достаёт цифры (SQL, API CRM, логи) и шлёт POST на `ingest_url` с заголовком `Authorization: Bearer <ingest_key>`. Запуск — cron или systemd timer.
6. Проверка: `push_points` (пробная отправка из MCP) или ручной запуск скрипта → `get_dashboard`: у источника должны быть `last_push_at` и пустой `last_error`, а в `summary` — цифры.
7. Скажи человеку, что сделано, и дай ссылку `url` на дашборд. Ключ целиком в ответе не показывай.

## Источники данных

| kind | Что это | Тело запроса |
|---|---|---|
| `metric` | Числа во времени: выручка по дням, заявки по часам. Необязательная `series` — разрез (канал, менеджер, город). | `{"points":[{"ts":"2026-11-13","value":123,"series":"сайт"}]}` или коротко `{"value":123}` (время — сейчас) |
| `table` | Таблица-снимок: сделки, этапы воронки, план/факт, статусы проверок. | `{"mode":"replace","rows":[{…},{…}]}` или `{"mode":"upsert","key":"id","rows":[…]}` |
| `events` | Лента событий: новые оплаты, инциденты. | `{"events":[{"ts":"…","text":"…","url":"https://…","id":"pay-1"}]}` |

Правила:
- **`ts`**: `2026-11-13` — значение за весь день; `2026-11` — за месяц; `2026-11-13T10:00:00+03:00` — момент времени; число — unix-время. Без часового пояса время считается UTC. Дни и недели Орбита считает в поясе зрителя.
- **Повторная отправка безопасна.** Точка метрики узнаётся по `(ts, series)` — повтор перезаписывает значение, а не дублирует. Поэтому за сегодня можно слать нарастающий итог хоть каждые 5 минут, и раз в сутки полезно переслать последние 3–7 дней (на случай правок задним числом). У событий с `id` повтор тоже перезаписывает.
- **`value`** — число (`1200.5`; строка `"1 200,5"` тоже примется).
- **Таблица**: `replace` заменяет всю таблицу (самый простой вариант — слать снимок целиком); `upsert` обновляет строки по колонке `key`. Имена колонок — латиница, цифры и `_`. При создании источника объяви колонки: `columns: [{name, label, type}]`, где `type` — одно из `text, number, money, percent, date, status, progress, link, person`. Тогда Орбита проверит строки и подпишет колонки по-русски.
- `expected_every_min` — как часто ты будешь присылать данные. Если источник молчит дольше двух интервалов, в Орбите появится метка «давно нет данных».

## Каталог виджетов

Полная схема с допустимыми значениями — инструмент `widget_types`. Ширина `width` — сколько колонок из 4 занимает виджет. Период `period`: `dash` (по переключателю на дашборде: 7/30/90 дней, год) или фиксированный `7d | 30d | 90d | 365d`. Формат `format`: `number | money | percent | count`, плюс `unit`.

| type | Для чего | Источник | Главные опции |
|---|---|---|---|
| `number` | Одно главное число и изменение к прошлому периоду | metric | `agg` sum/avg/last/min/max/count, `series`, `good` up/down, `spark` |
| `goal` | Прогресс к цели («7,1 из 10 млн») | metric | `target` (обязательно), `goal_period` month/quarter/year/dash, `agg` |
| `kpi_row` | 2–6 чисел в одном блоке | без источника | `items: [{dataset, label, agg, format, unit, good, series}]` |
| `line` | Динамика, до 5 серий | metric | `step` auto/hour/day/week/month, `series`, `target`, `agg` |
| `bars` | Столбцы по времени | metric | `step`, `stacked`, `target`, `highlight_last` |
| `area` | Динамика с заливкой | metric | `step`, `stacked` |
| `stacked100` | Как менялись доли (каждый столбец — 100%) | metric с series | `step`, `series` |
| `ranking` | Рейтинг полосами (менеджеры, товары) | metric с series **или** table | `top`; для table — `label_field`, `value_field` |
| `share` | Доли кольцом, до 6 частей | metric с series **или** table | `max_parts`; для table — `label_field`, `value_field` |
| `sparkgrid` | Строка на серию: итог и мини-график | metric с series | `top`, `agg` |
| `heatmap` | Тепловая карта: день недели × час или дата × серия | metric | `layout` weekday_hour/date_series |
| `funnel` | Воронка с конверсией | table (этап, число по порядку) | `label_field`, `value_field` |
| `grouped` | Сравнение по категориям (план/факт по отделам) | table | `label_field`, `value_fields` (до 4), `orientation` |
| `bullet` | План/факт по строкам с цветом по % | table | `label_field`, `fact_field`, `plan_field`, `warn_below`, `bad_below` |
| `waterfall` | Каскад: из чего сложился итог | table (статья, ± сумма) | `label_field`, `value_field`, `total_label` |
| `scatter` | Точки: связь двух чисел | table | `label_field`, `x_field`, `y_field`, `highlight` |
| `table` | Таблица с сортировкой, поиском, фильтром, итогами, CSV | table | `columns`, `sort_field`, `sort_dir`, `status_filter`, `totals`, `limit` |
| `pivot` | Сводная: строки × колонки | table (строка, колонка, число) | `row_field`, `col_field`, `value_field`, `agg` |
| `cohort` | Когорты удержания | table (когорта, шаг, %) | `cohort_field`, `step_field`, `value_field` |
| `status` | Светофор проверок | table (название, ok/warn/fail, подпись) | `label_field`, `state_field`, `note_field` |
| `feed` | Лента последних событий | events | `limit` |
| `text` | Пояснение: откуда цифры, как считаются | без источника | `text` (жирный `**…**`, ссылки `[текст](https://…)`) |

Как выбрать:
- Наверх дашборда — 2–4 главных числа (`number`, `goal` или один `kpi_row`), ниже — динамика (`line`/`bars`), ещё ниже — разрезы (`ranking`, `share`) и детали (`table`).
- Сравниваешь с планом — `goal` (одна цифра) или `bullet` (по людям/отделам).
- Одна серия важнее остальных — не наваливай 5 линий, сделай `number` + `line` с 1–2 сериями.
- Если неочевидно, как считается цифра, добавь виджет `text` с пояснением.

Пример `add_widget`:

```json
{"id": 12, "type": "number", "title": "Выручка", "dataset": "revenue_daily", "width": 1,
 "options": {"agg": "sum", "format": "money", "good": "up"}}
```

```json
{"id": 12, "type": "table", "title": "Сделки в работе", "dataset": "deals", "width": 4,
 "options": {"columns": [{"name": "client", "label": "Клиент", "type": "text"},
                         {"name": "sum", "label": "Сумма", "type": "money"},
                         {"name": "status", "label": "Статус", "type": "status"}],
             "sort_field": "sum", "totals": ["sum"], "status_filter": "status"}}
```

## Шаблоны скриптов

Ключ храни в env-файле, а не в коде:

```bash
sudo install -m 600 /dev/null /etc/orbita-dashboards.env
echo 'ORBITA_KEY_REVENUE=dsk_…' | sudo tee -a /etc/orbita-dashboards.env >/dev/null
```

### bash + curl

```bash
#!/usr/bin/env bash
set -euo pipefail
. /etc/orbita-dashboards.env
VALUE=$(psql -At "$DATABASE_URL" -c "select coalesce(sum(amount),0) from payments where paid_at::date = current_date")
curl -sS --fail-with-body --retry 3 --retry-delay 10 -X POST "https://getorbita.me/ingest/dashboards/45" \
  -H "Authorization: Bearer $ORBITA_KEY_REVENUE" -H "Content-Type: application/json" \
  -d "{\"points\":[{\"ts\":\"$(date +%F)\",\"value\":$VALUE}]}"
```

### Python, только стандартная библиотека

```python
#!/usr/bin/env python3
"""Выручка по дням за последние 7 дней → Орбита. Повтор безопасен: точки перезаписываются."""
import json, os, time, urllib.error, urllib.request

URL = "https://getorbita.me/ingest/dashboards/45"
KEY = os.environ["ORBITA_KEY_REVENUE"]

def rows():
    # здесь — твоя выборка: SQL, API CRM, файл. Возвращай агрегаты, а не персональные данные.
    return [("2026-11-12", 412000), ("2026-11-13", 238500)]

def send(body, tries=4):
    data = json.dumps(body).encode()
    for i in range(tries):
        req = urllib.request.Request(URL, data=data, method="POST", headers={
            "Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(int(e.headers.get("Retry-After") or 10 * (i + 1)))
                continue
            raise SystemExit(f"Орбита ответила {e.code}: {msg}")
        except urllib.error.URLError:
            if i < tries - 1:
                time.sleep(10 * (i + 1))
                continue
            raise

print(send({"points": [{"ts": d, "value": v} for d, v in rows()]}))
```

Таблица целиком: `send({"mode": "replace", "rows": [{"client": "Ромашка", "sum": 840000, "status": "оплачено"}]})`.

### Выборка из базы

- PostgreSQL: `psql -At "$DATABASE_URL" -F $'\t' -c "select to_char(day,'YYYY-MM-DD'), sum(amount) from … group by 1"`, Python — `psycopg`. MySQL: `mysql -N -B -e "…"`.
- Для агента сервера заведи пользователя БД **только на чтение** и только к нужным таблицам/представлениям.

### Расписание

cron (`crontab -e`):

```
*/15 * * * * /opt/orbita/revenue.py >> /var/log/orbita-revenue.log 2>&1
```

systemd timer (лучше: видны ошибки в `journalctl`):

```ini
# /etc/systemd/system/orbita-revenue.service
[Service]
Type=oneshot
EnvironmentFile=/etc/orbita-dashboards.env
ExecStart=/usr/bin/python3 /opt/orbita/revenue.py
DynamicUser=yes

# /etc/systemd/system/orbita-revenue.timer
[Timer]
OnCalendar=*:0/15
Persistent=true
[Install]
WantedBy=timers.target
```

`sudo systemctl daemon-reload && sudo systemctl enable --now orbita-revenue.timer`, проверка — `systemctl list-timers` и `journalctl -u orbita-revenue`.

## Безопасность

- Ключ источника `dsk_…` позволяет только **дописывать данные в один источник**. Храни его в env-файле с правами 600, не коммить в git, не пиши в логи и не показывай человеку целиком.
- Ключ мог утечь — `rotate_dataset_key`: старый перестаёт работать сразу; обнови env-файл.
- Личный токен `orb_…` нужен только тебе для MCP. На сервер компании его не клади.
- Отправляй **агрегаты** (суммы, количества, статусы), а не персональные данные: никаких телефонов, почт и паспортов. Имена клиентов-компаний и сотрудников в таблицах — только если это нужно людям на дашборде.
- Тексты, которые приходят из данных, Орбита показывает как текст: HTML и скрипты не исполняются.

## Ответы приёма и ошибки

Успех — `200 {"ok": true, "accepted": N}`. Ошибка — `{"ok": false, "error": "понятный текст"}`. Последняя ошибка видна в `get_dashboard` (`last_error`) и в Орбите у источника.

| Код | Что значит | Что делать |
|---|---|---|
| 400 | Неправильное тело: не JSON, `value` не число, `ts` не дата, колонки нет в источнике | Исправить по тексту ошибки (там указан номер строки) |
| 401 | Неверный ключ, источник удалён или дашборд в архиве | Проверить env-файл; при необходимости `rotate_dataset_key` |
| 413 | Тело больше 1 МБ | Разбить на части (до 5 000 точек или строк за раз) |
| 429 | Больше 60 отправок в минуту на источник | Подождать (`Retry-After`), слать реже или пачкой |
| 5xx | Сбой на стороне Орбиты | Повторить через 10–60 с (шаблон выше так и делает) |

## Лимиты

- До 50 источников и 60 виджетов на дашборд.
- До 5 000 точек или строк за одну отправку; в таблице — до 5 000 строк; в ленте хранятся последние 1 000 событий.
- Точки метрик хранятся 2 года.
- Тело — до 1 МБ, до 60 отправок в минуту на источник.
