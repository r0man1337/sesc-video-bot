#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "python-telegram-bot[all]>=21.0",
#     "python-dotenv>=1.0.0",
# ]
# ///

"""
Simple Telegram Bot
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Привет {user.mention_html()}!\n\n"
        f"Я простой Telegram бот. Отправь мне сообщение, и я отвечу!"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    max_size_mb = os.getenv("MAX_VIDEO_SIZE_MB", "100")
    help_text = f"""
Доступные команды:
/start - Начать работу с ботом
/help - Показать это сообщение

Возможности:
• Отправьте мне видео, и я извлеку из него аудио в формате MP3
• Максимальный размер видео: {max_size_mb} MB
    """
    await update.message.reply_text(help_text)


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    await update.message.reply_text(f"Вы написали: {update.message.text}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle video messages by extracting audio to MP3."""
    video = update.message.video
    user = update.effective_user

    # Get max video size from environment variable
    max_size_mb = int(os.getenv("MAX_VIDEO_SIZE_MB", "100"))
    max_size_bytes = max_size_mb * 1024 * 1024

    # Check file size
    if video.file_size > max_size_bytes:
        await update.message.reply_text(
            f"❌ Видео слишком большое! Максимальный размер: {max_size_mb} MB.\n"
            f"Размер вашего видео: {video.file_size / 1024 / 1024:.1f} MB"
        )
        return

    # Check if ffmpeg is installed
    if not shutil.which("ffmpeg"):
        await update.message.reply_text(
            "❌ Ошибка: FFmpeg не установлен на сервере.\n"
            "Обратитесь к администратору для установки FFmpeg."
        )
        logger.error("FFmpeg is not installed on the system")
        return

    logger.info(
        f"Processing video from user {user.id} ({user.username}): "
        f"{video.file_size / 1024 / 1024:.1f} MB"
    )

    # Send initial processing message
    status_message = await update.message.reply_text("Обрабатываю видео.")

    # Create event to stop animation
    stop_animation = asyncio.Event()

    # Start animation task
    animation_task = asyncio.create_task(
        animate_processing_message(status_message, "Обрабатываю видео", stop_animation)
    )

    temp_dir = None
    try:
        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix="video_bot_")
        video_path = Path(temp_dir) / "input_video.mp4"
        audio_path = Path(temp_dir) / "output_audio.mp3"

        # Download video file
        logger.info(f"Downloading video to {video_path}")
        video_file = await context.bot.get_file(video.file_id)
        await video_file.download_to_drive(video_path)

        # Convert video to MP3 using ffmpeg
        logger.info(f"Converting video to MP3: {audio_path}")
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-ab",
            "192k",
            "-f",
            "mp3",
            str(audio_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_message = stderr.decode("utf-8", errors="ignore")
            logger.error(f"FFmpeg conversion failed: {error_message}")
            raise RuntimeError(f"FFmpeg failed with return code {process.returncode}")

        # Stop animation
        stop_animation.set()
        await animation_task

        # Check if audio file was created
        if not audio_path.exists():
            raise FileNotFoundError("Audio file was not created")

        audio_size_mb = audio_path.stat().st_size / 1024 / 1024
        logger.info(f"Conversion successful. Audio size: {audio_size_mb:.1f} MB")

        # Update status message
        await status_message.edit_text("Отправляю аудио...")

        # Send audio file as document
        with open(audio_path, "rb") as audio_file:
            await update.message.reply_document(
                document=audio_file,
                filename="audio.mp3",
                caption="🎵 Аудио извлечено из видео",
            )

        # Delete status message
        await status_message.delete()

        logger.info(f"Audio sent successfully to user {user.id}")

    except Exception as e:
        # Stop animation if it's still running
        stop_animation.set()
        await animation_task

        # Send error message to user
        error_msg = "❌ Произошла ошибка при обработке видео."

        if isinstance(e, FileNotFoundError):
            error_msg += "\nНе удалось создать аудио файл."
        elif isinstance(e, RuntimeError):
            error_msg += "\nОшибка конвертации видео."

        await status_message.edit_text(error_msg)

        logger.error(f"Error processing video: {e}", exc_info=True)

    finally:
        # Clean up temporary files
        if temp_dir and Path(temp_dir).exists():
            try:
                shutil.rmtree(temp_dir)
                logger.debug(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                logger.error(f"Failed to clean up temp directory: {e}")


async def animate_processing_message(
    message, base_text: str, stop_event: asyncio.Event
) -> None:
    """Animate processing message by cycling through dots."""
    dots = [".", "..", "..."]
    idx = 0

    while not stop_event.is_set():
        try:
            await message.edit_text(f"{base_text}{dots[idx]}")
            idx = (idx + 1) % len(dots)
            await asyncio.sleep(1)
        except Exception as e:
            # Ignore errors during animation (e.g., message not modified)
            logger.debug(f"Animation error: {e}")
            await asyncio.sleep(1)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error("Exception while handling an update:", exc_info=context.error)


def main() -> None:
    """Start the bot."""
    # Get bot token from environment variable
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
        return

    # Create the Application
    application = Application.builder().token(token).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))

    # Register video handler
    application.add_handler(MessageHandler(filters.VIDEO, handle_video))

    # Register message handler for text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Register error handler
    application.add_error_handler(error_handler)

    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
