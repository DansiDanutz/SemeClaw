"""Helper script to get your Telegram user ID.
Run this, then send any message to your bot on Telegram.
It will print your user ID and exit.
"""
import asyncio

async def main():
    try:
        from telegram import Update
        from telegram.ext import Application, MessageHandler, filters, ContextTypes
    except ImportError:
        print("Installing python-telegram-bot...")
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "python-telegram-bot"])
        from telegram import Update
        from telegram.ext import Application, MessageHandler, filters, ContextTypes

    import os as _os, pathlib as _pl
    BOT_TOKEN = _os.environ.get("TELEGRAM_BOT_TOKEN") or _os.environ.get("DLS_DAVID_BOT_TOKEN") or ""
    if not BOT_TOKEN:
        fe = _pl.Path.home() / ".openclaw" / "fleet.env"
        if fe.exists():
            for _line in fe.read_text().splitlines():
                if "=" in _line and "BOT_TOKEN" in _line and not _line.startswith("#"):
                    BOT_TOKEN = _line.partition("=")[2].strip().strip('"').strip("'")
                    break
    if not BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN env var or add it to ~/.openclaw/fleet.env")

    print("=" * 50)
    print("  Telegram User ID Finder")
    print("=" * 50)
    print()
    print("Bot is running. Send ANY message to your bot on Telegram.")
    print("Your user ID will be printed here.")
    print()

    found_event = asyncio.Event()
    user_id_result = {}

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.message.from_user
        user_id_result["id"] = str(user.id)
        user_id_result["username"] = user.username or "no username"
        user_id_result["name"] = user.first_name or "unknown"

        print(f"Found you!")
        print(f"  Name: {user_id_result['name']}")
        print(f"  Username: @{user_id_result['username']}")
        print(f"  User ID: {user_id_result['id']}")
        print()
        print(f">>> Your Telegram user ID is: {user_id_result['id']} <<<")
        print()

        await update.message.reply_text(
            f"Got it! Your user ID is: {user.id}\n"
            f"SemeClaw will now be configured for you."
        )

        # Stop the application
        found_event.set()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async with app:
        await app.start()
        await app.updater.start_polling()

        # Wait until we get a message or timeout after 60 seconds
        try:
            await asyncio.wait_for(found_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("Timed out after 60 seconds. No message received.")

        await app.updater.stop()
        await app.stop()

    if "id" in user_id_result:
        print()
        print("Done! Copy your user ID and add it to config.user.yaml")


if __name__ == "__main__":
    asyncio.run(main())
