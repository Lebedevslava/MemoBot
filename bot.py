#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MemoBot v2.5.2-stdlib — напоминания, только стандартная библиотека."""
import os, re, json, time, threading, logging, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

VERSION = "v2.5.2-stdlib"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TZ_NAME = os.environ.get("TZ", "UTC")
STT_PROVIDER = os.environ.get("STT_PROVIDER", "yandex")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY", "")
FOLDER_ID = os.environ.get("FOLDER_ID", "")
API = "https://api.telegram.org"
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
DATA_FILE = os.path.join(DATA_DIR, "tasks.json")
EDIT_TTL = 15 * 60  # секунд жизни режима правки

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

def get_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TZ_NAME)
    except Exception:
        return timezone.utc

TZ = get_tz()

def load_tasks():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_tasks(tasks):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)

def tg(method, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(f"{API}/bot{BOT_TOKEN}/{method}", data=data,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read().decode())
            if resp.get("ok"):
                return resp
            log.warning("tg %s: %s", method, resp)
            return None
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode(errors="replace")[:150]
            except Exception: pass
            if e.code == 409:
                log.warning("getUpdates: 409 Conflict — переповтор через 3 c")
                time.sleep(3); continue
            log.warning("tg %s: HTTP %s %s", method, e.code, body)
            return None
        except Exception as e:
            log.warning("tg %s: %s (попытка %s)", method, e, attempt + 1)
            time.sleep(2)
    return None

def send(chat_id, text, task_id=None):
    params = {"chat_id": chat_id, "text": text[:4000]}
    if task_id is not None:
        rows = [[{"text": "✅ Подтвердить", "callback_data": "confirm:%d" % task_id},
                 {"text": "🕒 Изменить", "callback_data": "edit:%d" % task_id},
                 {"text": "❌ Отмена", "callback_data": "cancel:%d" % task_id}]]
        params["reply_markup"] = json.dumps({"inline_keyboard": rows})
    return tg("sendMessage", **params)

def replace_msg(chat_id, message_id, text):
    r = tg("editMessageText", chat_id=chat_id, message_id=message_id, text=text[:4000],
           reply_markup=json.dumps({"inline_keyboard": []}))
    if not r:
        send(chat_id, text)

def find_task(tid):
    for t in TASKS:
        if t["id"] == tid:
            return t
    return None

NUMW = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
}
WEEKDAYS = {"понедельник": 0, "вторник": 1, "среду": 2, "среда": 2, "четверг": 3,
            "пятницу": 4, "пятница": 4, "субботу": 5, "суббота": 5,
            "воскресенье": 6}
DAY_WORDS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
DAYPART = {"дня": 12, "день": 12, "вечера": 12, "вечером": 12, "ночи": 0,
           "ночь": 0, "утра": 0, "утром": 0}
STOPWORDS = {"напомни", "напомнить", "поставь", "поставить", "напоминание",
             "о", "про", "в", "во", "на", "сегодня"}
SEMI = {"полпервого": (0, 30), "полвторого": (1, 30), "полтретьего": (2, 30),
        "полчетвертого": (3, 30), "полпятого": (4, 30), "полшестого": (5, 30),
        "полседьмого": (6, 30), "полвосьмого": (7, 30), "полдевятого": (8, 30),
        "полдесятого": (9, 30), "полодиннадцатого": (10, 30),
        "полдвенадцатого": (11, 30)}

def _clean_head(tokens):
    while tokens and (tokens[0] in STOPWORDS or tokens[0] in DAY_WORDS):
        tokens.pop(0)
    return " ".join(tokens)

def _clean_tail_rest(tokens, start, end):
    return _clean_head(tokens[:start] + tokens[end:])

def _compose_hour_minute(values):
    if not values:
        return None
    if len(values) == 1:
        return (values[0], 0) if 0 <= values[0] <= 23 else None
    hour = values[0]
    i = 1
    while i < len(values) and hour + values[i] <= 23 and values[i] < 10:
        hour += values[i]; i += 1
    if i >= len(values):
        return (hour, 0) if hour <= 23 else None
    minutes = sum(values[i:])
    if hour <= 23 and 0 <= minutes <= 59:
        return (hour, minutes)
    return None

def _match_time(tokens, i):
    n = len(tokens)
    if i >= n: return None
    tk = tokens[i]
    if tk == "полдень": return (12, 0, i + 1)
    if tk == "полночь": return (0, 0, i + 1)
    if tk in SEMI:
        return (SEMI[tk][0], SEMI[tk][1], i + 1)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", tk)
    if m and int(m.group(1)) <= 23:
        return (int(m.group(1)), int(m.group(2)), i + 1)
    digits = []
    j = i
    while j < n and tokens[j].isdigit() and len(tokens[j]) <= 2:
        digits.append(tokens[j]); j += 1
        if len(digits) >= 3: break
    if digits:
        if len(digits) >= 2:
            hh = int(digits[0])
            mm = "".join(digits[1:])
            if mm.isdigit() and 0 <= hh <= 23 and 0 <= int(mm) <= 59:
                return (hh, int(mm), j)
        joined = "".join(digits)
        if len(joined) <= 2 and int(joined) <= 23:
            return (int(joined), 0, j)
        for split in range(1, len(joined)):
            hh, mm = int(joined[:split]), int(joined[split:])
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return (hh, mm, j)
    values = []
    j = i
    while j < n and tokens[j] in NUMW and len(values) < 4:
        values.append(NUMW[tokens[j]]); j += 1
    if values:
        hm = _compose_hour_minute(values)
        if hm:
            return (hm[0], hm[1], j)
    return None

def parse_task(text, now):
    t = " ".join((text or "").lower().replace("ё", "е").split())
    tokens = t.split()
    n = len(tokens)

    m = re.search(r"\b(\d{4}-\d{2}-\d{2})(?:[ t]+(\d{1,2}):(\d{2}))?", t)
    if m:
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            hh, mm = (int(m.group(2)), int(m.group(3))) if m.group(2) else (0, 0)
            when = datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ)
            rest = (t[:m.start()] + " " + t[m.end():]).strip(" ,.-")
            if when <= now:
                when += timedelta(days=1)
            return when.astimezone(timezone.utc), rest or "задача"
        except ValueError:
            pass

    for i in range(n):
        tk = tokens[i]

        if tk == "через":
            rest_after = tokens[i + 1:]
            amount, used, implicit_hours = None, 0, False
            if rest_after and rest_after[0] == "полчаса":
                amount, used = 30, 1
            elif rest_after and rest_after[0] in ("час", "часа", "часов"):
                amount, used, implicit_hours = 1, 1, True
            elif rest_after:
                v = rest_after[0]
                if v.isdigit():
                    amount, used = int(v), 1
                elif v in NUMW:
                    amount, used = NUMW[v], 1
                    if used < len(rest_after) and rest_after[used] in NUMW and NUMW[rest_after[used]] < 10:
                        amount += NUMW[rest_after[used]]; used += 1
            if amount is not None:
                unit = rest_after[used] if used < len(rest_after) else ""
                if unit.startswith("минут"): used += 1; mult = 1
                elif unit.startswith("час"): used += 1; mult = 60
                elif unit.startswith("день") or unit.startswith("дн"): used += 1; mult = 1440
                elif implicit_hours: mult = 60
                else: mult = 1
                when = (now + timedelta(minutes=amount * mult)).astimezone(timezone.utc)
                rest = _clean_tail_rest(tokens, i, i + 1 + used)
                return when, rest or "задача"

        base_day = None
        j = i
        if tk in DAY_WORDS:
            base_day = now + timedelta(days=DAY_WORDS[tk]); j = i + 1
        elif tk in WEEKDAYS:
            delta = (WEEKDAYS[tk] - now.weekday()) % 7
            if delta == 0: delta = 7
            base_day = now + timedelta(days=delta); j = i + 1
        if base_day is not None:
            k = j
            if k < n and tokens[k] in ("в", "во"):
                k += 1
            tm = _match_time(tokens, k)
            if tm:
                hh, mm, end = tm
                if end < n and tokens[end] in DAYPART:
                    part = DAYPART[tokens[end]]; end += 1
                    hh = (hh % 12) + part if part else hh % 12
                when = datetime(base_day.year, base_day.month, base_day.day, hh, mm, tzinfo=TZ)
                rest = _clean_tail_rest(tokens, i, end)
                if when <= now:
                    when += timedelta(days=1)
                return when.astimezone(timezone.utc), rest or "задача"

        tm = _match_time(tokens, i)
        if tm:
            hh, mm, end = tm
            if end < n and tokens[end] in DAYPART:
                part = DAYPART[tokens[end]]; end += 1
                hh = (hh % 12) + part if part else hh % 12
            if 0 <= hh <= 23:
                when = datetime(now.year, now.month, now.day, hh, mm, tzinfo=TZ)
                rest = _clean_tail_rest(tokens, i, end)
                if when <= now:
                    when += timedelta(days=1)
                return when.astimezone(timezone.utc), rest or "задача"
    return None

def parse_new_time_only(text, now):
    t = " ".join((text or "").lower().replace("ё", "е").split())
    t = re.sub(r"^(измени|поменяй|исправь|перенеси|сдвинь)\s+(время\s+)?(на\s+)?", " ", t).strip()
    if not t:
        return None
    return parse_task(t, now)

def recognize_ogg(data):
    magic = data[:4]
    log.info("voice: %d байт, магия=%s", len(data), data[:8].hex())
    if magic != b"OggS":
        head = data[:16].decode(errors="replace") if len(data) > 0 else "(пусто)"
        return None, (f"файл не похож на OGG: {len(data)} байт, "
                      f"первые байты: {data[:8].hex()} ({head!r})")
    url = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?format=oggopus&lang=ru-RU"
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Api-Key {YANDEX_API_KEY}", "x-folder-id": FOLDER_ID,
        "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode(errors="replace")[:200]
        except Exception: pass
        return None, f"SpeechKit HTTP {e.code}: {body}"
    except Exception as e:
        return None, f"SpeechKit: {e}"
    try:
        resp = json.loads(raw)
    except Exception:
        return None, f"SpeechKit вернул не-JSON: {raw[:120]}"
    if not isinstance(resp, dict):
        return None, f"SpeechKit вернул не словарь ({type(resp).__name__})"
    result = resp.get("result")
    if not isinstance(result, str) or not result:
        return None, f"SpeechKit без результата: {str(resp)[:150]}"
    return result, None

def tg_download(file_id):
    r = tg("getFile", file_id=file_id)
    if not r or "result" not in r or "file_path" not in r.get("result", {}):
        return None
    path = r["result"]["file_path"]
    url = f"{API}/file/bot{BOT_TOKEN}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()
    except Exception as e:
        log.warning("download: %s", e)
        return None

TASKS = load_tasks()
_lock = threading.Lock()
_next_id = (max((t["id"] for t in TASKS), default=0)) + 1

EDIT_PENDING = {}

def add_task(chat_id, when, text):
    global _next_id
    with _lock:
        task = {"id": _next_id, "chat_id": chat_id, "when": when.isoformat(),
                "text": text, "delivered": False, "attempts": 0}
        _next_id += 1
        TASKS.append(task)
        save_tasks(TASKS)
        return task["id"]

def update_task_time(task, when):
    with _lock:
        task["when"] = when.isoformat()
        save_tasks(TASKS)

def remove_task(task):
    with _lock:
        if task in TASKS:
            TASKS.remove(task)
            save_tasks(TASKS)

def fmt_local(when):
    return when.astimezone(TZ).strftime("%d.%m.%Y %H:%M")

def confirm_text(task):
    return f"Задача #{task['id']}: «{task['text']}» — напомню {fmt_local(datetime.fromisoformat(task['when']))}"

def list_text(chat_id):
    rows = [t for t in TASKS if t["chat_id"] == chat_id and not t["delivered"]]
    rows.sort(key=lambda t: t["when"])
    if not rows:
        return "Задач нет."
    lines = [f"#{t['id']} — {fmt_local(datetime.fromisoformat(t['when']))} — {t['text']}\n"
             f"  изменить: /edit {t['id']} <время>" for t in rows]
    return "📋 Ваши напоминания:\n" + "\n".join(lines)

def handle_text(chat_id, text):
    pend = EDIT_PENDING.get(chat_id)
    if pend and not text.startswith("/"):
        task = find_task(pend["task_id"])
        if not task or task["delivered"] or time.time() - pend["ts"] > EDIT_TTL:
            EDIT_PENDING.pop(chat_id, None)
            if task and not task["delivered"]:
                send(chat_id, "Время правки истекло. Используйте: /edit %d <время>" % task["id"])
            return
        parsed = parse_new_time_only(text, datetime.now(timezone.utc))
        if not parsed:
            send(chat_id, "Не понял время. Примеры: «завтра в 9:00», «в 18 45», «через полчаса». Либо нажмите «Подтвердить» или «Отмена» на кнопках.")
            return
        when, _ = parsed
        update_task_time(task, when)
        EDIT_PENDING.pop(chat_id, None)
        send(chat_id, f"Задача #{task['id']} изменена: «{task['text']}» — теперь напомню {fmt_local(when)}", task["id"])
        return

    handle_command(chat_id, text)

def handle_command(chat_id, text):
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower()
    if cmd == "/start" or cmd == "/help":
        send(chat_id, VERSION + "\n"
             "Добавить: /add завтра в 10:00 текст — или просто напишите/скажите фразой.\n"
             "Понимаю: «через два часа», «завтра в 10:00», «на 16 0 9», «полдевятого», «двадцать один сорок».\n"
             "После создания пришлю кнопки: подтвердить, изменить время, отмена.\n"
             "Команды: /list, /edit <номер> <время>, /del <номер>, /env")
    elif cmd == "/add":
        parsed = parse_task(arg, datetime.now(timezone.utc))
        if not parsed:
            send(chat_id, "Не понял время. Примеры: /add завтра в 9:00 текст, /add через час текст")
        else:
            when, rest = parsed
            tid = add_task(chat_id, when, rest)
            task = find_task(tid)
            send(chat_id, confirm_text(task) + "\nОшиблись? Нажмите «Изменить» или «Отмена».", tid)
    elif cmd == "/list":
        send(chat_id, list_text(chat_id))
    elif cmd == "/edit":
        m = re.match(r"(\d+)\s+(.*)", arg.strip())
        if not m:
            send(chat_id, "Формат: /edit <номер> <время>. Например: /edit 3 завтра в 9:00")
            return
        task = find_task(int(m.group(1)))
        if not task or task["chat_id"] != chat_id or task["delivered"]:
            send(chat_id, f"Задача #{m.group(1)} не найдена или уже доставлена.")
            return
        parsed = parse_task(m.group(2), datetime.now(timezone.utc))
        if not parsed:
            send(chat_id, "Не понял время. Примеры: «завтра в 9:00», «в 18 45», «через полчаса».")
            return
        when, _ = parsed
        update_task_time(task, when)
        send(chat_id, f"Задача #{task['id']} изменена: «{task['text']}» — теперь напомню {fmt_local(when)}", task["id"])
    elif cmd == "/del":
        tid = arg.strip().lstrip("#")
        with _lock:
            before = len(TASKS)
            TASKS[:] = [t for t in TASKS if not (t["chat_id"] == chat_id and str(t["id"]) == tid)]
            removed = before - len(TASKS)
            save_tasks(TASKS)
        send(chat_id, f"Удалено: {removed}." if removed else f"Задача #{tid} не найдена.")
    elif cmd == "/env":
        def mask(v, n=4):
            return "задан (" + v[:n] + "…)" if v else "НЕ задан"
        send(chat_id, VERSION +
             "\nBOT_TOKEN: " + mask(BOT_TOKEN) +
             "\nSTT_PROVIDER: " + (STT_PROVIDER or "—") +
             "\nYANDEX_API_KEY: " + mask(YANDEX_API_KEY) +
             "\nFOLDER_ID: " + mask(FOLDER_ID) +
             "\nTZ: " + TZ_NAME + " (сейчас " + datetime.now(TZ).strftime("%H:%M") + ")")
    else:
        parsed = parse_task(text, datetime.now(timezone.utc))
        if parsed:
            when, rest = parsed
            tid = add_task(chat_id, when, rest)
            task = find_task(tid)
            send(chat_id, confirm_text(task) + "\nОшиблись? Нажмите «Изменить» или «Отмена».", tid)
        else:
            send(chat_id, "Не понял время. Напишите: /add завтра в 10:00 текст")

def handle_callback(chat_id, message_id, data):
    log.info("callback: chat=%s data=%s", chat_id, data)
    action, _, num = data.partition(":")
    try:
        tid = int(num)
    except ValueError:
        send(chat_id, "Некорректная кнопка.")
        return
    task = find_task(tid)
    if not task or task["chat_id"] != chat_id:
        send(chat_id, f"Задача #{num} не найдена.")
        return
    if action == "confirm":
        replace_msg(chat_id, message_id, confirm_text(task) + " ✅ подтверждено")
        send(chat_id, list_text(chat_id))
    elif action == "cancel":
        remove_task(task)
        EDIT_PENDING.pop(chat_id, None)
        replace_msg(chat_id, message_id, f"Задача #{tid} отменена. ❌")
    elif action == "edit":
        EDIT_PENDING[chat_id] = {"task_id": tid, "ts": time.time()}
        send(chat_id, f"Напишите новое время для задачи #{tid}. Примеры: «завтра в 9:00», «в 18 45», «через полчаса». Текст напоминания сохранится.")
    else:
        send(chat_id, "Неизвестное действие.")

def reminder_worker():
    while True:
        try:
            now = datetime.now(timezone.utc)
            with _lock:
                due = [t for t in TASKS if not t["delivered"] and t["attempts"] < 5
                       and datetime.fromisoformat(t["when"]) <= now]
            for t in due:
                ok = tg("sendMessage", chat_id=t["chat_id"],
                        text=f"🔔 Напоминание: {t['text']}") is not None
                with _lock:
                    if ok:
                        t["delivered"] = True
                    else:
                        t["attempts"] += 1
                save_tasks(TASKS)
        except Exception as e:
            log.warning("worker: %s", e)
        time.sleep(5)

def main():
    log.info("BUILD %s | провайдер=%s | TZ=%s | ключ=%s",
             VERSION, STT_PROVIDER, TZ_NAME, "задан" if YANDEX_API_KEY else "отсутствует")
    threading.Thread(target=reminder_worker, daemon=True).start()
    offset = 0
    while True:
        if not BOT_TOKEN:
            log.error("BOT_TOKEN не задан"); time.sleep(10); continue
        resp = tg("getUpdates", offset=offset, timeout=30)
        if resp:
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if upd.get("callback_query"):
                        cq = upd["callback_query"]
                        chat_id = cq.get("message", {}).get("chat", {}).get("id")
                        message_id = cq.get("message", {}).get("message_id")
                        tg("answerCallbackQuery", callback_query_id=cq.get("id"))
                        if chat_id and message_id:
                            handle_callback(chat_id, message_id, cq.get("data", ""))
                        continue
                    msg = upd.get("message") or {}
                    chat_id = msg.get("chat", {}).get("id")
                    if not chat_id: continue
                    if msg.get("voice"):
                        fid = msg["voice"]["file_id"]
                        data = tg_download(fid)
                        if data is None:
                            send(chat_id, "Не смог скачать файл из Telegram.")
                            continue
                        if STT_PROVIDER == "yandex":
                            text, err = recognize_ogg(data)
                            if err:
                                send(chat_id, f"Не получилось обработать голосовое: {err}")
                                continue
                        else:
                            text, err = None, "неизвестный провайдер"
                        send(chat_id, f"Слышу: «{text}».")
                        handle_text(chat_id, text)
                    elif msg.get("text"):
                        handle_text(chat_id, msg["text"].strip())
                except Exception as e:
                    log.warning("update: %s", e)

if __name__ == "__main__":
    main()
