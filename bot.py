import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("amongus-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "8938894207:AAE0GNhywZiHqP03Zd8PiMq6pFQsqToWWyo")
DB_PATH = os.getenv("DB_PATH", "amongus.db")

ROOM_SPAM_INTERVAL_MIN = 5
COOL_MESSAGE_INTERVAL_MIN = 15  # как часто бот кидает "позорное сообщение"
MSK = ZoneInfo("Europe/Moscow")

DAYPHOTO_ALLOWED_USER_ID = 6271603562  # только этот пользователь может ставить /dayphoto

COLORS = [
    "Красный", "Зеленый", "Желтый", "Фиолетовый", "Бордовый",
    "Коричневый", "Белый", "Черный", "Коралловый", "Сиреневый",
    "Лаймовый", "Синий", "Бежевый", "Голубой",
]
COLORS_LOWER = {c.lower(): c for c in COLORS}

WELCOME_TEXT = (
    "Привет, я создан для этого чата\n\n"
    "Напишите /profile чтобы увидеть свой профиль\n"
    "Напишите /setcolor цвет и при заходе этот цвет будет вашим\n"
    "Напишите /createroom кодрумы чтобы сделать свою руму\n"
    "Напишите /setname имя чтобы поставить свое имя\n"
    "Напишите /warnprofile чтобы посмотреть сколько у вас предупреждений\n"
    "Ответьте на любое сообщение и напишите /coolmessage чтобы сохранить сообщение "
    "в легендарные этого чата\n"
    "Напишите /menu чтобы я написал это сообщение\n"
    "Напишите /onlineroom чтобы посмотреть какая рума активна\n"
    "Напишите /mutespam включить или /mutespam выключить чтобы управлять спамом румы\n"
    "Напишите /lastnews чтобы посмотреть последние новости\n"
    "Напишите /chatinfo чтобы узнать инфо о чате\n"
    "Напишите /FutureBot чтобы узнать что будет в следующем обновлении"
)

# ---------------- DB ----------------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            custom_name TEXT,
            color TEXT,
            warns INTEGER DEFAULT 0,
            rooms_created INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS rooms (
            chat_id INTEGER PRIMARY KEY,
            code TEXT,
            creator_id INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS cool_messages (
            chat_id INTEGER PRIMARY KEY,
            message_id INTEGER,
            set_by INTEGER,
            set_at TEXT
        );
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id INTEGER PRIMARY KEY,
            spam_muted INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS last_news (
            chat_id INTEGER PRIMARY KEY,
            text TEXT,
            set_by INTEGER,
            set_at TEXT
        );
        CREATE TABLE IF NOT EXISTS day_photos (
            chat_id INTEGER PRIMARY KEY,
            file_id TEXT,
            media_type TEXT DEFAULT 'photo',
            caption TEXT,
            set_by INTEGER,
            set_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    # миграция для уже существующих БД без колонки media_type
    conn = db()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(day_photos)").fetchall()]
    if "media_type" not in cols:
        conn.execute("ALTER TABLE day_photos ADD COLUMN media_type TEXT DEFAULT 'photo'")
        conn.commit()
    conn.close()


def get_or_create_user(chat_id, user_id, username, first_name):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users (chat_id, user_id, username, first_name) VALUES (?,?,?,?)",
            (chat_id, user_id, username, first_name),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        ).fetchone()
    else:
        conn.execute(
            "UPDATE users SET username=?, first_name=? WHERE chat_id=? AND user_id=?",
            (username, first_name, chat_id, user_id),
        )
        conn.commit()
    conn.close()
    return row


def update_user(chat_id, user_id, **fields):
    conn = db()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE users SET {cols} WHERE chat_id=? AND user_id=?",
        (*fields.values(), chat_id, user_id),
    )
    conn.commit()
    conn.close()


def find_color_owner(chat_id, color):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE chat_id=? AND color=?", (chat_id, color)
    ).fetchone()
    conn.close()
    return row


def set_room(chat_id, code, creator_id):
    conn = db()
    conn.execute(
        "INSERT INTO rooms (chat_id, code, creator_id, created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET code=excluded.code, "
        "creator_id=excluded.creator_id, created_at=excluded.created_at",
        (chat_id, code, creator_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_room(chat_id):
    conn = db()
    row = conn.execute("SELECT * FROM rooms WHERE chat_id=?", (chat_id,)).fetchone()
    conn.close()
    return row


def all_rooms():
    conn = db()
    rows = conn.execute("SELECT * FROM rooms").fetchall()
    conn.close()
    return rows


def set_cool_message(chat_id, message_id, set_by):
    conn = db()
    conn.execute(
        "INSERT INTO cool_messages (chat_id, message_id, set_by, set_at) VALUES (?,?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET message_id=excluded.message_id, "
        "set_by=excluded.set_by, set_at=excluded.set_at",
        (chat_id, message_id, set_by, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_cool_message(chat_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM cool_messages WHERE chat_id=?", (chat_id,)
    ).fetchone()
    conn.close()
    return row


def all_cool_messages():
    conn = db()
    rows = conn.execute("SELECT * FROM cool_messages").fetchall()
    conn.close()
    return rows


def set_spam_muted(chat_id, muted: bool):
    conn = db()
    conn.execute(
        "INSERT INTO chat_settings (chat_id, spam_muted) VALUES (?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET spam_muted=excluded.spam_muted",
        (chat_id, 1 if muted else 0),
    )
    conn.commit()
    conn.close()


def get_spam_muted(chat_id) -> bool:
    conn = db()
    row = conn.execute(
        "SELECT spam_muted FROM chat_settings WHERE chat_id=?", (chat_id,)
    ).fetchone()
    conn.close()
    return bool(row["spam_muted"]) if row else False


def set_last_news(chat_id, text, set_by):
    conn = db()
    conn.execute(
        "INSERT INTO last_news (chat_id, text, set_by, set_at) VALUES (?,?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET text=excluded.text, "
        "set_by=excluded.set_by, set_at=excluded.set_at",
        (chat_id, text, set_by, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_last_news(chat_id):
    conn = db()
    row = conn.execute("SELECT * FROM last_news WHERE chat_id=?", (chat_id,)).fetchone()
    conn.close()
    return row


def set_day_photo(chat_id, file_id, media_type, caption, set_by):
    conn = db()
    conn.execute(
        "INSERT INTO day_photos (chat_id, file_id, media_type, caption, set_by, set_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET file_id=excluded.file_id, "
        "media_type=excluded.media_type, caption=excluded.caption, "
        "set_by=excluded.set_by, set_at=excluded.set_at",
        (chat_id, file_id, media_type, caption, set_by, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_day_photo(chat_id):
    conn = db()
    row = conn.execute("SELECT * FROM day_photos WHERE chat_id=?", (chat_id,)).fetchone()
    conn.close()
    return row


def all_day_photos():
    conn = db()
    rows = conn.execute("SELECT * FROM day_photos").fetchall()
    conn.close()
    return rows


# ---------------- Bot ----------------

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
scheduler = AsyncIOScheduler()


def display_name(row) -> str:
    if row["custom_name"]:
        return row["custom_name"]
    return row["first_name"] or "Без имени"


def display_color(row) -> str:
    if not row["color"]:
        return "не выбран"
    if row["username"]:
        return f"{row['color']} (@{row['username']})"
    return row["color"]


async def is_chat_creator(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status == "creator"
    except Exception:
        return False


@dp.message(Command("start"))
async def cmd_start(message: Message):
    get_or_create_user(
        message.chat.id, message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    await message.answer(WELCOME_TEXT)


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer(WELCOME_TEXT)


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = message.from_user
    row = get_or_create_user(message.chat.id, user.id, user.username, user.first_name)

    rank = "Фармила"
    try:
        member = await bot.get_chat_member(message.chat.id, user.id)
        if member.status == "creator":
            rank = "Главный фармила"
    except Exception:
        pass

    caption = (
        f"★ | Имя: {display_name(row)}\n"
        f"★★ | Цвет: {display_color(row)}\n"
        f"★★★ | Звание: {rank}\n"
        f"★★★★ | Количество варнов: {row['warns']}\n"
        f"★★★★★ | Румы: {row['rooms_created']}"
    )

    try:
        photos = await bot.get_user_profile_photos(user.id, limit=1)
    except Exception:
        photos = None

    if photos and photos.total_count > 0:
        file_id = photos.photos[0][-1].file_id
        await message.answer_photo(file_id, caption=caption)
    else:
        await message.answer(caption)


@dp.message(Command("setname"))
async def cmd_setname(message: Message, command: CommandObject):
    user = message.from_user
    get_or_create_user(message.chat.id, user.id, user.username, user.first_name)
    name = (command.args or "").strip()
    if not name:
        await message.answer("Напишите имя после команды, например: /setname Иван")
        return
    update_user(message.chat.id, user.id, custom_name=name)
    await message.answer(f"Имя успешно установлено: {name}")


@dp.message(Command("setcolor"))
async def cmd_setcolor(message: Message, command: CommandObject):
    user = message.from_user
    get_or_create_user(message.chat.id, user.id, user.username, user.first_name)
    arg = (command.args or "").strip()

    if not arg:
        lines = []
        for c in COLORS:
            owner = find_color_owner(message.chat.id, c)
            mark = " (занят)" if owner and owner["user_id"] != user.id else ""
            lines.append(f"• {c}{mark}")
        await message.answer(
            "Доступные цвета:\n" + "\n".join(lines) +
            "\n\nНапишите /setcolor и цвет, например: /setcolor Синий"
        )
        return

    color = COLORS_LOWER.get(arg.lower())
    if not color:
        await message.answer("Такого цвета нет. Доступные цвета:\n" + ", ".join(COLORS))
        return

    owner = find_color_owner(message.chat.id, color)
    if owner and owner["user_id"] != user.id:
        await message.answer("Этот цвет уже занят другим игроком, выберите другой.")
        return

    update_user(message.chat.id, user.id, color=color)
    await message.answer("Цвет был поставлен успешно")


@dp.message(Command("warnprofile"))
async def cmd_warnprofile(message: Message):
    await message.answer("в будущем")


ROOM_JOB_PREFIX = "room_"


async def room_spam(chat_id: int):
    if get_spam_muted(chat_id):
        return
    room = get_room(chat_id)
    if not room:
        return
    try:
        await bot.send_message(
            chat_id,
            f'Пожалуйста зайдите в руму "{room["code"]}" на данный момент она активна',
        )
    except Exception as e:
        log.warning("room_spam failed for %s: %s", chat_id, e)


def schedule_room_job(chat_id: int):
    job_id = f"{ROOM_JOB_PREFIX}{chat_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        room_spam,
        "interval",
        minutes=ROOM_SPAM_INTERVAL_MIN,
        args=[chat_id],
        id=job_id,
        replace_existing=True,
    )


@dp.message(Command("createroom"))
async def cmd_createroom(message: Message, command: CommandObject):
    user = message.from_user
    get_or_create_user(message.chat.id, user.id, user.username, user.first_name)
    code = (command.args or "").strip()
    if not code:
        await message.answer("Напишите код румы, например: /createroom ABCD1")
        return

    set_room(message.chat.id, code, user.id)
    conn = db()
    conn.execute(
        "UPDATE users SET rooms_created = rooms_created + 1 WHERE chat_id=? AND user_id=?",
        (message.chat.id, user.id),
    )
    conn.commit()
    conn.close()

    schedule_room_job(message.chat.id)

    await message.answer(
        f'Комната "{code}" была создана\n'
        f"Чтобы зайти зайдите в амонгас и впишите {code}"
    )


@dp.message(Command("onlineroom"))
async def cmd_onlineroom(message: Message):
    room = get_room(message.chat.id)
    if not room:
        await message.answer("На данный момент нет активной румы.")
        return
    await message.answer(
        f'Пожалуйста зайдите в руму "{room["code"]}" на данный момент она активна'
    )


@dp.message(Command("mutespam"))
async def cmd_mutespam(message: Message, command: CommandObject):
    arg = (command.args or "").strip().lower()

    if arg not in ("включить", "выключить"):
        await message.answer(
            "Используйте:\n"
            "/mutespam включить — выключить спам румы\n"
            "/mutespam выключить — включить спам румы"
        )
        return

    if arg == "включить":
        set_spam_muted(message.chat.id, True)
        await message.answer("Спам румы выключен 🔇")
    else:
        set_spam_muted(message.chat.id, False)
        await message.answer("Спам румы включен 🔊")


COOL_JOB_PREFIX = "cool_"


async def cool_message_repost(chat_id: int):
    row = get_cool_message(chat_id)
    if not row:
        return
    try:
        await bot.forward_message(chat_id, chat_id, row["message_id"])
        await bot.send_message(chat_id, "Позорное сообщение этого чата ☝️")
    except Exception as e:
        log.warning("cool_message_repost failed for %s: %s", chat_id, e)


def schedule_cool_job(chat_id: int):
    job_id = f"{COOL_JOB_PREFIX}{chat_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        cool_message_repost,
        "interval",
        minutes=COOL_MESSAGE_INTERVAL_MIN,
        args=[chat_id],
        id=job_id,
        replace_existing=True,
    )


@dp.message(Command("coolmessage"))
async def cmd_coolmessage(message: Message):
    user = message.from_user
    get_or_create_user(message.chat.id, user.id, user.username, user.first_name)

    if message.reply_to_message is None:
        row = get_cool_message(message.chat.id)
        text = (
            "На данный момент это сообщение крутое, ответьте на любое сообщение "
            "и напишите команду /coolmessage и оно станет популярным"
        )
        if row:
            try:
                await bot.send_message(
                    message.chat.id, text, reply_to_message_id=row["message_id"]
                )
                return
            except Exception:
                pass
        await message.answer(text)
        return

    set_cool_message(message.chat.id, message.reply_to_message.message_id, user.id)
    schedule_cool_job(message.chat.id)
    await message.reply_to_message.reply(
        "Крутое сообщение выбрано теперь этот позор будет популярным"
    )


@dp.message(Command("lastnews"))
async def cmd_lastnews(message: Message, command: CommandObject):
    user = message.from_user
    get_or_create_user(message.chat.id, user.id, user.username, user.first_name)
    args = (command.args or "").strip()

    if args:
        if not await is_chat_creator(message.chat.id, user.id):
            await message.answer("Добавлять новости может только создатель чата.")
            return
        set_last_news(message.chat.id, args, user.id)
        await message.answer("Новость сохранена. Теперь ее можно посмотреть командой /lastnews")
        return

    news = get_last_news(message.chat.id)
    if not news:
        await message.answer("Пока нет новостей.")
        return
    await message.answer(f"📰 Последние новости:\n\n{news['text']}")


DAYPHOTO_JOB_PREFIX = "dayphoto_"


async def send_day_photo(chat_id: int):
    row = get_day_photo(chat_id)
    if not row:
        return
    try:
        media_type = row["media_type"] if "media_type" in row.keys() else "photo"
        if media_type == "video":
            await bot.send_video(chat_id, row["file_id"], caption=row["caption"] or None)
        else:
            await bot.send_photo(chat_id, row["file_id"], caption=row["caption"] or None)
    except Exception as e:
        log.warning("send_day_photo failed for %s: %s", chat_id, e)


def schedule_dayphoto_job(chat_id: int):
    job_id = f"{DAYPHOTO_JOB_PREFIX}{chat_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    scheduler.add_job(
        send_day_photo,
        "cron",
        hour=10,
        minute=0,
        timezone=MSK,
        args=[chat_id],
        id=job_id,
        replace_existing=True,
    )


@dp.message(Command("dayphoto"))
async def cmd_dayphoto(message: Message, command: CommandObject):
    user = message.from_user
    get_or_create_user(message.chat.id, user.id, user.username, user.first_name)

    if user.id != DAYPHOTO_ALLOWED_USER_ID:
        await message.answer("Эта команда доступна только определенному пользователю.")
        return

    if not message.photo and not message.video:
        await message.answer(
            "Отправьте фото или видео с подписью /dayphoto, чтобы бот каждый день "
            "в 10:00 по МСК публиковал этот медиафайл."
        )
        return

    if message.video:
        file_id = message.video.file_id
        media_type = "video"
    else:
        file_id = message.photo[-1].file_id
        media_type = "photo"

    caption = (command.args or "").strip()
    set_day_photo(message.chat.id, file_id, media_type, caption, user.id)
    schedule_dayphoto_job(message.chat.id)
    await message.answer("Медиа дня сохранено. Буду присылать его каждый день в 10:00 по МСК.")


@dp.message(Command("chatinfo"))
async def cmd_chatinfo(message: Message):
    await message.answer(
        "Привет новенький, инфо чата в этом канале: https://telegram.me/DuoBrawl"
    )


@dp.message(Command("FutureBot", ignore_case=True))
async def cmd_futurebot(message: Message):
    await message.answer("В следующем обновлении будет питомцы 🐾")


async def restore_jobs():
    for room in all_rooms():
        schedule_room_job(room["chat_id"])
    for cm in all_cool_messages():
        schedule_cool_job(cm["chat_id"])
    for dp_row in all_day_photos():
        schedule_dayphoto_job(dp_row["chat_id"])


async def main():
    init_db()
    scheduler.start()
    await restore_jobs()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
