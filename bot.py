import logging
import os
import uuid

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

# ================= STATES =================

class UserFlow(StatesGroup):
    amount = State()
    vic_ready_screenshot = State()
    waiting_admin = State()
    payment_screenshot = State()
    waiting_claim = State()

class AdminFlow(StatesGroup):
    waiting_paypal = State()
    waiting_payout_text = State()

# ================= STORAGE =================

requests = {}  # ref_id -> data

# ================= START =================

@dp.message_handler(commands=["start"], state="*")
async def start(message: types.Message, state: FSMContext):
    await state.finish()
    ref_id = str(uuid.uuid4())[:6].upper()

    requests[ref_id] = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "amount": None,
        "admin_message_id": None
    }

    await state.update_data(ref_id=ref_id)

    await message.answer(
        f"Willkommen @{message.from_user.username}\n\n"
        "Gib bitte den Betrag ein,\n"
        "den dein VIC überweisen möchte"
    )
    await UserFlow.amount.set()

# ================= AMOUNT =================

@dp.message_handler(state=UserFlow.amount, content_types=types.ContentTypes.TEXT)
async def get_amount(message: types.Message, state: FSMContext):
    if not message.text.replace(".", "").isdigit():
        return await message.answer("Bitte nur eine Zahl eingeben.")

    data = await state.get_data()
    ref_id = data["ref_id"]
    requests[ref_id]["amount"] = message.text

    await message.answer(
        "Bitte sende nun einen Screenshot vom Chat,\n"
        "der zeigt, dass der VIC bereit zum Zahlen ist."
    )
    await UserFlow.vic_ready_screenshot.set()

# ================= SCREENSHOT 1 =================

@dp.message_handler(state=UserFlow.vic_ready_screenshot, content_types=types.ContentTypes.PHOTO)
async def get_vic_ready(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ref_id = data["ref_id"]
    r = requests[ref_id]

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Anfrage annehmen", callback_data=f"accept:{ref_id}"),
        InlineKeyboardButton("❌ Anfrage ablehnen", callback_data=f"reject:{ref_id}")
    )

    sent = await bot.send_photo(
        ADMIN_ID,
        photo=message.photo[-1].file_id,
        caption=(
            f"👤 Kunde: @{r['username']}\n"
            f"🆔 Referenz: #{ref_id}\n"
            f"💰 Betrag: {r['amount']} €\n\n"
            "Status: Screenshot erhalten"
        ),
        reply_markup=kb
    )

    r["admin_message_id"] = sent.message_id

    await message.answer(
        "Vielen Dank.\n"
        "Alle Informationen wurden übermittelt.\n"
        "Bitte warte, bis wir uns bei dir melden."
    )
    await UserFlow.waiting_admin.set()

@dp.message_handler(state=UserFlow.vic_ready_screenshot)
async def block_text_before_photo(message: types.Message):
    await message.answer("Bitte sende **nur einen Screenshot**.")

# ================= ADMIN ACCEPT =================

@dp.callback_query_handler(lambda c: c.data.startswith("accept:"))
async def admin_accept(call: types.CallbackQuery, state: FSMContext):
    ref_id = call.data.split(":")[1]
    r = requests[ref_id]

    await bot.send_message(
        ADMIN_ID,
        f"Bitte sende jetzt die PayPal-Adresse für @{r['username']}"
    )
    await state.update_data(ref_id=ref_id)
    await AdminFlow.waiting_paypal.set()
    await call.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("reject:"))
async def admin_reject(call: types.CallbackQuery):
    ref_id = call.data.split(":")[1]
    r = requests[ref_id]

    await bot.send_message(
        r["user_id"],
        "❌ Deine Zahlung ist leider nicht durchgegangen.\n\n"
        "❌ Es findet keine Auszahlung statt."
    )
    await call.answer("Abgelehnt")

# ================= PAYPAL =================

@dp.message_handler(state=AdminFlow.waiting_paypal, content_types=types.ContentTypes.TEXT)
async def admin_paypal(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ref_id = data["ref_id"]
    r = requests[ref_id]

    await bot.send_message(
        r["user_id"],
        f"Bitte lass den VIC den Betrag von {r['amount']} € an die folgende PayPal-Adresse senden:\n\n"
        f"{message.text}\n\n"
        "Bitte sende anschließend einen Screenshot der PayPal-Überweisung\n"
        "hier in den Chat."
    )

    await UserFlow.payment_screenshot.set()
    await state.finish()

# ================= PAYMENT SCREENSHOT =================

@dp.message_handler(state=UserFlow.payment_screenshot, content_types=types.ContentTypes.PHOTO)
async def get_payment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ref_id = data["ref_id"]
    r = requests[ref_id]

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🔗 Auszahlungslink senden", callback_data=f"payout:{ref_id}"),
        InlineKeyboardButton("❌ Zahlung nicht durchgegangen", callback_data=f"fail:{ref_id}")
    )

    await bot.edit_message_media(
        chat_id=ADMIN_ID,
        message_id=r["admin_message_id"],
        media=types.InputMediaPhoto(
            media=message.photo[-1].file_id,
            caption=(
                f"👤 Kunde: @{r['username']}\n"
                f"🆔 Referenz: #{ref_id}\n"
                f"💰 Betrag: {r['amount']} €\n\n"
                "Status: Überweisungsnachweis erhalten"
            )
        ),
        reply_markup=kb
    )

    await message.answer("Danke. Bitte warte auf weitere Informationen.")
    await UserFlow.waiting_admin.set()

# ================= PAYOUT =================

@dp.callback_query_handler(lambda c: c.data.startswith("payout:"))
async def payout_button(call: types.CallbackQuery, state: FSMContext):
    ref_id = call.data.split(":")[1]
    await state.update_data(ref_id=ref_id)

    await bot.send_message(
        ADMIN_ID,
        "Bitte sende jetzt den Auszahlungstext oder Link."
    )
    await AdminFlow.waiting_payout_text.set()
    await call.answer()

@dp.message_handler(state=AdminFlow.waiting_payout_text, content_types=types.ContentTypes.TEXT)
async def send_payout(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ref_id = data["ref_id"]
    r = requests[ref_id]

    await bot.send_message(
        r["user_id"],
        f"💰 Dein Geld wartet auf dich!\n\n"
        f"{message.text}\n\n"
        "Bestätige deine Auszahlung mit /claim:"
    )

    await UserFlow.waiting_claim.set()
    await state.finish()

# ================= CLAIM =================

@dp.message_handler(commands=["claim"], state=UserFlow.waiting_claim)
async def claim(message: types.Message, state: FSMContext):
    await message.answer(
        f"✅ Auszahlung bestätigt.\n\nVielen Dank @{message.from_user.username}"
    )
    await state.finish()

# ================= RUN =================

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)

