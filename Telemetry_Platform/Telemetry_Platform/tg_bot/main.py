import asyncio
import os
import secrets
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, 
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO)


BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))


PRICE_MONTH = 7500 
PRICE_YEAR = 50000   

redis_client = redis.Redis(host="redis", port=6379, decode_responses=True)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()



def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy Access", callback_data="menu_buy")],
        [InlineKeyboardButton(text="⏱ Get Free Demo (1 Hour)", callback_data="menu_demo")], 
        [InlineKeyboardButton(text="🔑 My Active Keys", callback_data="menu_keys")],
        [InlineKeyboardButton(text="💬 Support", callback_data="menu_support")]
    ])

def get_back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu_main")]
    ])

def get_keys_keyboard(has_keys: bool):
    buttons = []
    if has_keys:
        buttons.append([InlineKeyboardButton(text="📥 Send My Keys to Chat", callback_data="action_send_keys")])
    buttons.append([InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
def get_tariffs_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 1 Month (7500 ⭐️)", callback_data="invoice_month")],
        [InlineKeyboardButton(text="🏆 1 Year (50000 ⭐️) - Save 44%", callback_data="invoice_year")],
        [InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu_main")]
    ])



def generate_api_key():
    return f"TRADER-{secrets.token_hex(8).upper()}"

def generate_demo_key():
    return f"DEMO-{secrets.token_hex(6).upper()}"



@dp.message(Command("start"))
async def cmd_start(message: Message):
    welcome_text = (
        "⚡️ *Welcome*\n\n"
        "Realtime Multi-Exchange Market Telemetry Platform, "
        "Bitcoin and Ethereum.\n\n"
        "Select an option below to get started:"
    )
    
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard())




@dp.callback_query(F.data == "menu_main")
async def nav_main(call: CallbackQuery):
    
    await call.message.edit_text(
        text="⚡️ *Welcome*\n\nPlease select an option below:",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
    await call.answer()


@dp.callback_query(F.data == "menu_support")
async def nav_support(call: CallbackQuery):
    
    support_text = (
        "💬 <b>Customer Support</b>\n\n"
        "If you have any questions or technical issues, please contact our support team:\n\n"
        "👤 <b>Admin:</b> @ADMIN\n" 
        "📧 <b>Email:</b> EMAIL"
    )
    await call.message.edit_text(
        text=support_text,
        parse_mode="HTML", 
        reply_markup=get_back_keyboard()
    )
    await call.answer()

@dp.callback_query(F.data == "menu_keys")
async def nav_keys(call: CallbackQuery):
    user_id = str(call.from_user.id)
    user_keys = await redis_client.smembers(f"user_keys:{user_id}")
    
    if user_keys:
        text = f"🔑 *Your Licenses*\n\nYou currently have *{len(user_keys)} active key(s)* attached to your account."
        await call.message.edit_text(text=text, parse_mode="Markdown", reply_markup=get_keys_keyboard(True))
    else:
        text = "🚫 *No Active Licenses*\n\nYou don't have any active API keys yet. Click 'Buy Access' to get one."
        await call.message.edit_text(text=text, parse_mode="Markdown", reply_markup=get_keys_keyboard(False))
    
    await call.answer()


@dp.callback_query(F.data == "action_send_keys")
async def action_send_keys(call: CallbackQuery):
    user_id = str(call.from_user.id)
    user_keys = await redis_client.smembers(f"user_keys:{user_id}")
    
    if user_keys:
        keys_formatted = "\n".join([f"`{k}`" for k in user_keys])
        await call.message.answer(
            f"📥 *Here are your API Keys:*\n\n{keys_formatted}\n\n"
            f"🌐 *Terminal URL:* https://Address\n\n"
            f"_Tap a key to copy it, then paste it in the terminal._",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    await call.answer("Keys sent to chat!")

@dp.callback_query(F.data == "menu_demo")
async def nav_demo(call: CallbackQuery):
    user_id = str(call.from_user.id)
    
    
    if await redis_client.exists(f"demo_used:{user_id}"):
        await call.message.edit_text(
            text="🚫 *Demo Already Claimed*\n\nYou have already used your free demo access. To continue using the terminal, please purchase a full license.",
            parse_mode="Markdown",
            reply_markup=get_back_keyboard()
        )
        await call.answer()
        return

    
    demo_key = generate_demo_key()
    
    
    await redis_client.sadd("demo_keys_pending", demo_key)
    
    await redis_client.set(f"demo_used:{user_id}", "1")
    
    text = (
        f"⏱ *Your 1-Hour Demo Key is Ready!*\n\n"
        f"`{demo_key}`\n\n"
        f"⚠️ *Important:* The 1-hour countdown will **ONLY** start when you connect to the terminal for the first time.\n\n"
        f"🌐 *Terminal URL:* https://Address"
    )
    
    await call.message.edit_text(text=text, parse_mode="Markdown", reply_markup=get_back_keyboard())
    await call.answer()



@dp.callback_query(F.data == "menu_buy")
async def nav_buy(call: CallbackQuery):
    text = (
        "🛒 *Select Subscription Plan*\n\n"
        "🔹 *1 Month:* Full access to all Pro features for 30 days.\n"
        "🔹 *1 Year:* Full access for 365 days *(Best Value!)*."
    )
    
    await call.message.edit_text(text=text, parse_mode="Markdown", reply_markup=get_tariffs_keyboard())
    await call.answer()
@dp.callback_query(F.data == "invoice_month")
async def send_invoice_month(call: CallbackQuery):
    await call.message.delete() 
    
    await bot.send_invoice(
        chat_id=call.message.chat.id,
        title="1 Month",
        description="Access 30 days.",
        payload="sub_month",
        provider_token="", currency="XTR",
        prices=[LabeledPrice(label="1 Month Access", amount=PRICE_MONTH)],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Pay {PRICE_MONTH} ⭐️", pay=True)],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="action_cancel_invoice")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "invoice_year")
async def send_invoice_year(call: CallbackQuery):
    await call.message.delete()
    
    await bot.send_invoice(
        chat_id=call.message.chat.id,
        title="1 Year",
        description="Access 365 days.",
        payload="sub_year",
        provider_token="", currency="XTR",
        prices=[LabeledPrice(label="1 Year Access", amount=PRICE_YEAR)],
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"Pay {PRICE_YEAR} ⭐️", pay=True)],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="action_cancel_invoice")]
        ])
    )
    await call.answer()

@dp.callback_query(F.data == "action_cancel_invoice")
async def cancel_invoice(call: CallbackQuery):
    await call.message.delete()
    await call.answer("Purchase cancelled.")

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    user_id = str(message.from_user.id)
    new_key = generate_api_key()
    
    
    payload = message.successful_payment.invoice_payload
    
    
    if payload == "sub_month":
        status_val = "sub_31d"
        sub_text = "1 Month"
    else:
        status_val = "sub_367d"
        sub_text = "1 Year"
    
    
    await redis_client.hset("valid_api_keys", new_key, status_val)
    await redis_client.sadd(f"user_keys:{user_id}", new_key)
    
    await message.answer(
        f"*Payment Successful!*\n\n"
        f"Thank you for your {sub_text} subscription. Here is your new API Key:\n\n"
        f"`{new_key}`\n\n"
        f"⚠️ *Important:* The countdown will **ONLY** start when you connect to the terminal for the first time.\n\n"
        f"🌐 *Terminal URL:* https://Address\n\n"
        f"_You can always view your keys in the 'My Active Keys' menu._",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    
    await message.answer(
        "⚡️ *Realtime Telemetry Platform*\n\nMain Menu:", 
        parse_mode="Markdown", 
        reply_markup=get_main_keyboard()
    )
    
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, f"*New Sale!* User @{message.from_user.username or message.from_user.id} bought {sub_text} subscription.", parse_mode="Markdown")
        except Exception:
            pass


@dp.message(Command("genkey"))
async def admin_gen_key(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    new_key = generate_api_key()
    await redis_client.hset("valid_api_keys", new_key, "admin_generated")
    await redis_client.sadd(f"user_keys:{message.from_user.id}", new_key)
    
    await message.answer(
        f"*Admin Key Generated:*\n\n"
        f"`{new_key}`\n\n"
        f"*Terminal URL:* https://Address", 
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

@dp.message(Command("gendemo"))
async def admin_gen_demo(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    
    args = message.text.split()
    count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    
    keys = []
    for _ in range(count):
        demo_key = generate_demo_key()
        await redis_client.sadd("demo_keys_pending", demo_key)
        keys.append(f"`{demo_key}`")
        
    keys_str = "\n".join(keys)
    
    await message.answer(
        f"*Generated {count} Demo Key(s):*\n\n"
        f"{keys_str}\n\n"
        f"_These keys will activate their 1-hour timer only upon first use._", 
        parse_mode="Markdown"
    )
@dp.message(Command("genmonth"))
async def admin_gen_month(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    new_key = generate_api_key()
    
    
    await redis_client.hset("valid_api_keys", new_key, "sub_31d")
    await redis_client.sadd(f"user_keys:{message.from_user.id}", new_key)
    
    await message.answer(
        f"*Generated 1-MONTH Key:*\n\n"
        f"`{new_key}`\n\n"
        f"_This key will activate for 31 days upon first login._\n"
        f"🌐 *Terminal URL:* https://Address", 
        parse_mode="Markdown", disable_web_page_preview=True
    )

@dp.message(Command("genyear"))
async def admin_gen_year(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    new_key = generate_api_key()
    
    
    await redis_client.hset("valid_api_keys", new_key, "sub_367d")
    await redis_client.sadd(f"user_keys:{message.from_user.id}", new_key)
    
    await message.answer(
        f"*Generated 1-YEAR Key:*\n\n"
        f"`{new_key}`\n\n"
        f"_This key will activate for 367 days upon first login._\n"
        f"🌐 *Terminal URL:* https://Address", 
        parse_mode="Markdown", disable_web_page_preview=True
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())