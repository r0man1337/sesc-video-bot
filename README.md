# Telegram Video Processing Bot

A Telegram bot that extracts audio from videos and provides transcription capabilities using OpenAI's Whisper API.

## Features

- **Video to Audio Conversion**: Extract MP3 audio from video files
- **Audio Transcription**: Generate text transcriptions with timestamps using OpenAI Whisper
- **Flexible Processing Options**:
  - Audio only (MP3 extraction)
  - Transcription only (text with timestamps)
  - Audio + transcription (both files)
- **Large File Support**: Automatically splits long audio files into chunks for transcription
- **User-Friendly Interface**: Interactive inline keyboard for processing options

## Prerequisites

- Python 3.12 or higher
- `uv` package manager
- FFmpeg installed on your system
- Telegram Bot Token
- OpenAI API Key

## Installation

### 1. Install FFmpeg

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**macOS (using Homebrew):**
```bash
brew install ffmpeg
```

**Windows:**
Download from [FFmpeg official website](https://ffmpeg.org/download.html) and add to PATH.

### 2. Clone and Setup Project

```bash
# Clone the repository
git clone <your-repo-url>
cd <project-directory>

# Install dependencies using uv
uv sync
```

## Configuration

### 1. Environment Variables

Copy the example environment file and configure your settings:

```bash
cp .env.example .env
```

Edit `.env` file with your credentials:

```env
# Required: Your Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# Required: OpenAI API Key for transcription
OPENAI_API_KEY=your_openai_api_key_here

# Optional: Maximum video size in MB (default: 100)
MAX_VIDEO_SIZE_MB=100
```

### 2. Get Telegram Bot Token

1. Start a chat with [@BotFather](https://t.me/botfather) on Telegram
2. Use `/newbot` command to create a new bot
3. Copy the token and add it to your `.env` file

### 3. Get OpenAI API Key

1. Go to [OpenAI Platform](https://platform.openai.com/)
2. Create an account and navigate to API Keys
3. Generate a new API key and add it to your `.env` file

## Usage

### Running the Bot

```bash
# Run directly with uv
uv run bot.py

# Or make executable and run
chmod +x bot.py
./bot.py
```

### Bot Commands

- `/start` - Initialize the bot and get welcome message
- `/help` - Show detailed help with all features and limitations

### How to Use

1. Send a video file to the bot (max size configurable via `MAX_VIDEO_SIZE_MB`)
2. Choose processing option from the inline keyboard:
   - **🎵 Только аудио** - Extract MP3 audio only
   - **📝 Только транскрипция** - Generate text transcription with timestamps
   - **🎵📝 Аудио + транскрипция** - Get both audio file and transcription

### Transcription Format

Transcriptions include timestamps in HH:MM:SS format:
```
1. [00:00:00 - 00:00:15]
First phrase text

2. [00:00:15 - 00:00:30]
Second phrase text
```

## Project Structure

```
├── bot.py              # Main bot application
├── pyproject.toml      # Project dependencies and configuration
├── .env.example        # Example environment variables
├── .gitignore          # Git ignore rules
├── .python-version     # Python version specification
└── example/            # Example media files
    ├── audio.mp3
    └── video.mp4
```

## Dependencies

Main dependencies (managed by `uv`):

- `python-telegram-bot[all]>=21.0` - Telegram Bot API wrapper
- `openai>=1.0.0` - OpenAI API client for transcription
- `python-dotenv>=1.0.0` - Environment variables management

## Troubleshooting

### Common Issues

1. **FFmpeg not found**: Ensure FFmpeg is installed and available in system PATH
2. **Authentication errors**: Verify Telegram Bot Token and OpenAI API Key in `.env` file
3. **Video size limits**: Check `MAX_VIDEO_SIZE_MB` setting for large files
4. **Transcription failures**: Ensure OpenAI API key has sufficient credits and permissions

### Logs

The bot outputs detailed logs showing processing steps and any errors encountered.

## License

[Add your license here]

## Contributing

[Add contribution guidelines here]
