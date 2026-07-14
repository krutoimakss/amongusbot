import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timezone

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
    "Напишите /onlineroom чтобы посмотреть какая рума активна"
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
        """
    )
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


async def restore_jobs():
    for room in all_rooms():
        schedule_room_job(room["chat_id"])
    for cm in all_cool_messages():
        schedule_cool_job(cm["chat_id"])


async def main():
    init_db()
    scheduler.start()
    await restore_jobs()
    log.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
