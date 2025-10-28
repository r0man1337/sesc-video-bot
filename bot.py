#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "python-telegram-bot[all]>=21.0",
#     "python-dotenv>=1.0.0",
#     "openai>=1.0.0",
# ]
# ///

"""
Simple Telegram Bot
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
• Отправьте мне видео, и выберите вариант обработки:
  - 🎵 Только аудио - извлечь MP3
  - 📝 Только транскрипция - текст с временными метками
  - 🎵📝 Аудио + транскрипция - и аудио, и текст

Формат транскрипции:
1. [00:00:00 - 00:00:15]
Текст первой фразы

2. [00:00:15 - 00:00:30]
Текст второй фразы

• Максимальный размер видео: {max_size_mb} MB
• Длинные аудио автоматически разбиваются на части по 5 минут
    """
    await update.message.reply_text(help_text)


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message."""
    await update.message.reply_text(f"Вы написали: {update.message.text}")


async def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_message = stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(f"ffprobe failed: {error_message}")

    return float(stdout.decode().strip())


async def split_audio(
    audio_path: Path, chunk_duration: int = 300
) -> list[Path]:
    """Split audio into chunks of specified duration (seconds)."""
    # Get total duration first
    total_duration = await get_audio_duration(audio_path)

    chunks = []
    chunk_index = 0
    start_time = 0

    while start_time < total_duration:
        chunk_path = audio_path.parent / f"chunk_{chunk_index:03d}.mp3"

        # Use -ss (start time) and -t (duration) for precise splitting
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", str(audio_path),
            "-ss", str(start_time),
            "-t", str(chunk_duration),
            "-acodec", "copy",  # Copy codec for speed
            "-y",  # Overwrite output file
            str(chunk_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        await process.communicate()

        if process.returncode == 0 and chunk_path.exists():
            chunks.append(chunk_path)
            logger.debug(f"Created audio chunk: {chunk_path}")

        start_time += chunk_duration
        chunk_index += 1

    logger.info(f"Split audio into {len(chunks)} chunks")
    return chunks


async def transcribe_audio_chunk(
    client: AsyncOpenAI, chunk_path: Path, max_retries: int = 3
) -> dict:
    """Transcribe audio chunk with timestamps using OpenAI Whisper API."""
    for attempt in range(max_retries):
        try:
            with open(chunk_path, "rb") as audio_file:
                response = await client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )

            logger.debug(f"Successfully transcribed {chunk_path}")
            return response.model_dump()

        except Exception as e:
            error_type = type(e).__name__
            logger.warning(
                f"Transcription attempt {attempt + 1}/{max_retries} failed for "
                f"{chunk_path}: {error_type} - {str(e)}"
            )

            # Don't retry authentication errors
            if "authentication" in str(e).lower() or "api_key" in str(e).lower():
                raise

            # Retry with exponential backoff for other errors
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.info(f"Retrying in {wait_time} seconds...")
                await asyncio.sleep(wait_time)
            else:
                raise


def format_time(seconds: float) -> str:
    """Format seconds to HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_transcription_to_text(transcriptions: list[dict], chunk_offsets: list[float]) -> str:
    """Format transcription segments to readable text with timestamps."""
    formatted_lines = []
    segment_counter = 1

    for chunk_idx, transcription in enumerate(transcriptions):
        time_offset = chunk_offsets[chunk_idx]
        segments = transcription.get("segments", [])

        for segment in segments:
            start_time = segment.get("start", 0) + time_offset
            end_time = segment.get("end", 0) + time_offset
            text = segment.get("text", "").strip()

            if text:
                formatted_lines.append(
                    f"{segment_counter}. [{format_time(start_time)} - {format_time(end_time)}]\n{text}\n"
                )
                segment_counter += 1

    return "\n".join(formatted_lines)


async def transcribe_full_audio(
    audio_path: Path,
    status_message=None,
    chunk_duration: int = 300
) -> str:
    """Transcribe full audio file, splitting into chunks if necessary."""
    # Get OpenAI API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not found in environment variables")

    client = AsyncOpenAI(api_key=api_key)

    # Check audio duration
    duration = await get_audio_duration(audio_path)
    logger.info(f"Audio duration: {duration:.1f} seconds")

    # Determine if splitting is needed
    if duration <= chunk_duration:
        # Transcribe directly without splitting
        logger.info("Audio is short, transcribing without splitting")
        if status_message:
            await status_message.edit_text("Транскрибирую аудио...")

        transcription = await transcribe_audio_chunk(client, audio_path)
        formatted_text = format_transcription_to_text([transcription], [0.0])
    else:
        # Split and transcribe each chunk
        num_chunks = int(duration // chunk_duration) + (1 if duration % chunk_duration > 0 else 0)
        logger.info(f"Splitting audio into {num_chunks} chunks")

        chunks = await split_audio(audio_path, chunk_duration)
        transcriptions = []
        chunk_offsets = []

        for idx, chunk_path in enumerate(chunks, 1):
            if status_message:
                await status_message.edit_text(
                    f"Транскрибирую аудио (часть {idx}/{len(chunks)})..."
                )

            transcription = await transcribe_audio_chunk(client, chunk_path)
            transcriptions.append(transcription)
            # Calculate time offset for this chunk
            chunk_offsets.append((idx - 1) * chunk_duration)

            # Clean up chunk file
            try:
                chunk_path.unlink()
                logger.debug(f"Deleted chunk file: {chunk_path}")
            except Exception as e:
                logger.warning(f"Failed to delete chunk {chunk_path}: {e}")

        formatted_text = format_transcription_to_text(transcriptions, chunk_offsets)

    logger.info(f"Transcription completed. Total segments: {formatted_text.count('.')}")
    return formatted_text


async def send_transcription_as_file(
    query,
    transcription_text: str,
    temp_dir: Path
) -> None:
    """Send transcription as a text file."""
    transcription_path = temp_dir / "transcription.txt"

    # Write transcription to file
    transcription_path.write_text(transcription_text, encoding="utf-8")

    # Send as document
    with open(transcription_path, "rb") as f:
        await query.message.reply_document(
            document=f,
            filename="transcription.txt",
            caption=f"📝 Транскрипция ({len(transcription_text)} символов)"
        )

    logger.info(f"Sent transcription file ({len(transcription_text)} chars)")


def create_processing_options_keyboard() -> InlineKeyboardMarkup:
    """Create inline keyboard with processing options."""
    keyboard = [
        [InlineKeyboardButton("🎵 Только аудио", callback_data="audio_only")],
        [InlineKeyboardButton("📝 Только транскрипция", callback_data="transcription_only")],
        [InlineKeyboardButton("🎵📝 Аудио + транскрипция", callback_data="audio_and_transcription")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle video messages by showing processing options."""
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
        f"Received video from user {user.id} ({user.username}): "
        f"{video.file_size / 1024 / 1024:.1f} MB"
    )

    # Store video file_id in user_data for later processing
    context.user_data["video_file_id"] = video.file_id
    context.user_data["video_size"] = video.file_size

    # Show processing options
    keyboard = create_processing_options_keyboard()
    await update.message.reply_text(
        "Выберите вариант обработки:",
        reply_markup=keyboard
    )


async def process_video_extraction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mode: str
) -> None:
    """Process video extraction based on selected mode.

    Modes:
    - audio_only: Extract and send MP3 only
    - transcription_only: Extract MP3, transcribe, send transcription, delete MP3
    - audio_and_transcription: Extract and send MP3, then transcribe and send transcription
    """
    query = update.callback_query
    user = query.from_user

    # Get video file_id from user_data
    video_file_id = context.user_data.get("video_file_id")
    video_size = context.user_data.get("video_size", 0)

    if not video_file_id:
        await query.edit_message_text("❌ Ошибка: видео не найдено. Пожалуйста, отправьте видео заново.")
        return

    logger.info(f"Processing video for user {user.id} in mode: {mode}")

    # Send initial processing message
    status_message = await query.edit_message_text("Обрабатываю видео.")

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
        video_file = await context.bot.get_file(video_file_id)
        await video_file.download_to_drive(video_path)

        # Convert video to MP3 using ffmpeg
        logger.info(f"Converting video to MP3: {audio_path}")
        ffmpeg_process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i", str(video_path),
            "-vn",
            "-acodec", "libmp3lame",
            "-ar", "44100",
            "-ac", "2",
            "-ab", "192k",
            "-f", "mp3",
            str(audio_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await ffmpeg_process.communicate()

        if ffmpeg_process.returncode != 0:
            error_message = stderr.decode("utf-8", errors="ignore")
            logger.error(f"FFmpeg conversion failed: {error_message}")
            raise RuntimeError(f"FFmpeg failed with return code {ffmpeg_process.returncode}")

        # Stop animation
        stop_animation.set()
        await animation_task

        # Check if audio file was created
        if not audio_path.exists():
            raise FileNotFoundError("Audio file was not created")

        audio_size_mb = audio_path.stat().st_size / 1024 / 1024
        logger.info(f"Conversion successful. Audio size: {audio_size_mb:.1f} MB")

        # Process based on mode
        if mode == "audio_only":
            # Send audio only
            await status_message.edit_text("Отправляю аудио...")
            with open(audio_path, "rb") as audio_file:
                await query.message.reply_document(
                    document=audio_file,
                    filename="audio.mp3",
                    caption="🎵 Аудио извлечено из видео",
                )
            await status_message.delete()
            logger.info(f"Audio sent successfully to user {user.id}")

        elif mode == "transcription_only":
            # Transcribe and send transcription only
            transcription = await transcribe_full_audio(
                audio_path,
                status_message=status_message
            )
            await status_message.edit_text("Отправляю транскрипцию...")
            await send_transcription_as_file(query, transcription, Path(temp_dir))
            await status_message.delete()
            logger.info(f"Transcription sent successfully to user {user.id}")

        elif mode == "audio_and_transcription":
            # Send both audio and transcription
            await status_message.edit_text("Отправляю аудио...")
            with open(audio_path, "rb") as audio_file:
                await query.message.reply_document(
                    document=audio_file,
                    filename="audio.mp3",
                    caption="🎵 Аудио извлечено из видео",
                )
            logger.info(f"Audio sent successfully to user {user.id}")

            # Now transcribe
            transcription = await transcribe_full_audio(
                audio_path,
                status_message=status_message
            )
            await status_message.edit_text("Отправляю транскрипцию...")
            await send_transcription_as_file(query, transcription, Path(temp_dir))
            await status_message.delete()
            logger.info(f"Audio and transcription sent successfully to user {user.id}")

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
        elif "OPENAI_API_KEY" in str(e):
            error_msg += "\nОшибка конфигурации OpenAI API."
        elif "authentication" in str(e).lower():
            error_msg += "\nОшибка аутентификации OpenAI API."
        else:
            error_msg += f"\n{type(e).__name__}"

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


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline keyboard."""
    query = update.callback_query
    await query.answer()

    # Extract callback data
    callback_data = query.data

    if callback_data in ["audio_only", "transcription_only", "audio_and_transcription"]:
        await process_video_extraction(update, context, callback_data)
    else:
        await query.edit_message_text("❌ Неизвестная команда.")


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

    # Register callback query handler for inline buttons
    application.add_handler(CallbackQueryHandler(handle_callback_query))

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
