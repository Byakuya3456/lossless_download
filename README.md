# Local Audio Downloader

A high-fidelity, local-first web application for downloading audio from YouTube. Built to be stealthy, robust, and highly controllable, this downloader uses advanced anti-bot techniques (like dynamic cookie refreshing and IP rolling) to ensure stable batch playlist extraction without getting flagged or timed out.

## Features

- **Advanced Anti-Bot Measures**:
  - **Dynamic Cookie Refreshing**: Automatically utilizes a headless browser (`undetected-chromedriver`) to refresh YouTube session cookies every 15 minutes, ensuring your session stays active in the background.
  - **Persistent Browser Profile**: Saves your login state to a local `.yt_profile` to bypass "Sign in to confirm you're not a bot" blocks.
  - **IP Rolling**: Optional proxy rotation. Add proxies to `proxies.txt` to randomize the connection IP per track.
  - **Human Mimicry**: Randomized sleep delays (2-10 seconds) between actions to mimic real user behavior.
- **High-Fidelity Audio Pipeline**:
  - Extracts the **Highest Native Quality** (lossless/best VBR) available without re-encoding quality loss.
  - Integrated with **FFmpeg** for professional-grade tagging, metadata cleanup, and thumbnail/cover art embedding.
  - Smart title and metadata cleaning (strips annoying strings like "[Official Video]", "(Lyrics)", etc.).
- **Unmatched Concurrency & Control**:
  - **Serial Task Queue**: Enforces strict FIFO (First-In, First-Out) sequential downloading to prevent file-in-use errors (`WinError 32`) and keep your system stable.
  - **Full UI Controls**: Pause, Resume, and Stop individual tracks—or control the entire batch globally.
  - **Real-time Dashboard**: Track download percentage, ETA, speed, session health (when cookies were last refreshed), and active proxies via the beautiful, responsive backend-driven UI.

## Requirements

- **Python 3.13+**
- **FFmpeg & FFprobe (Bundled)**: This application now comes bundled with its own FFmpeg and FFprobe binaries in the `bin/` folder. No manual installation or PATH configuration is strictly required for core functionality.
- **Google Chrome**: Required for `undetected-chromedriver` to create a persistent profile.

## Setup Instructions

1. **Clone the repository** (or download the files to a local directory).
2. **Set up a Virtual Environment**:
   ```bash
   python -m venv .venv
   ```
3. **Activate the Virtual Environment**:
   - Windows:
     ```bash
     .\.venv\Scripts\activate
     ```
   - macOS / Linux:
     ```bash
     source .venv/bin/activate
     ```
4. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
5. **(Optional) Configure Proxies**:
   - Open `proxies.txt` and add proxy URLs (one per line, e.g., `http://user:pass@ip:port` or `socks5://ip:port`). If you leave it empty, the app will use your direct connection.

## Usage

1. **Start the Applicaton**:
   With your virtual environment activated, run:
   ```bash
   python app.py
   ```
2. **Access the Dashboard**:
   Open a web browser and navigate to:
   ```
   http://localhost:8000
   ```
3. **Link Your Account (Important)**:
   - On the web dashboard, click **Link Account** under the YouTube Account section.
   - An automated Chrome window will appear. Sign in to your YouTube/Google account.
   - Once signed in, you can close the window. The app saves your session securely in `.yt_profile` and uses it to extract audio smoothly. *You only have to do this once.*
4. **Download Audio**:
   - Paste a YouTube video or playlist URL into the input field.
   - Click "Queue Download."
   - The server will dynamically fetch and explode playlists into individual jobs, managing the downloads sequentially while updating the UI in real time.

## Project Structure

- `app.py` - FastAPI backend, queuing mechanism, session management, and `yt-dlp` background runner.
- `templates/index.html` - The frontend interactive dashboard (HTML/Tailwind CSS/JS).
- `requirements.txt` - Python package dependencies.
- `proxies.txt` - List of IPs for proxy rotation.
- `cookies.txt` - Extracted runtime session cookies (auto-generated).
- `.yt_profile/` - Internal Chrome profile data (auto-generated).
- `bin/` - Bundled FFmpeg and FFprobe binaries for Windows.
- `downloads/` - Default directory where your high-quality MP3 tracks are saved (now tracked in git).

## Notes & Troubleshooting

- **Sign In Required / 403 Errors**: Make sure your account is linked. If downloads begin failing, click the "Refresh session" button in the UI or restart the server.
- **File In Use Errors**: The application now forces strict sequential downloads to prevent multiple threads from colliding on the same temporary file.
- **Bot Detection**: If you plan to download massive playlists frequently, it is highly recommended to fill `proxies.txt` with reliable residential/datacenter proxies.

## License
MIT License
