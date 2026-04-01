import os
import json
import uuid
import threading
import yt_dlp
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import time
import random
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from PIL import Image
import aiofiles
import queue
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import undetected_chromedriver as uc
import shutil
import re
import threading

# Base configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
DEFAULT_DOWNLOAD_PATH = os.path.join(BASE_DIR, "downloads")
YT_PROFILE_DIR = os.path.join(BASE_DIR, ".yt_profile")
COOKIES_FILE = os.path.join(BASE_DIR, "cookies.txt")

# Ensure default directories exist
if not os.path.exists(DEFAULT_DOWNLOAD_PATH):
    os.makedirs(DEFAULT_DOWNLOAD_PATH)

# Settings Management
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {"download_path": DEFAULT_DOWNLOAD_PATH}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=4)

# Global State for jobs
jobs: Dict[str, Dict[str, Any]] = {}
download_queue = queue.Queue()  # FIFO queue for serial downloading
LAST_COOKIE_REFRESH = 0  # Timestamp of last successful headless cookie refresh
PROXIES = []  # List of loaded proxies

def load_proxies():
    """Load proxies from proxies.txt if it exists"""
    global PROXIES
    proxy_file = os.path.join(BASE_DIR, "proxies.txt")
    if os.path.exists(proxy_file):
        with open(proxy_file, "r") as f:
            PROXIES = [line.strip() for line in f if line.strip()]
    print(f"Loaded {len(PROXIES)} proxies.")

load_proxies()

app = FastAPI(title="Local Audio Downloader")

# Try to create templates directory if it doesn't exist
templates_dir = os.path.join(BASE_DIR, "templates")
if not os.path.exists(templates_dir):
    os.makedirs(templates_dir)

templates = Jinja2Templates(directory=templates_dir)

class DownloadRequest(BaseModel):
    url: str
    format: str = "mp3"
    bitrate: str = "320"
    embed_thumbnail: bool = True
    embed_metadata: bool = True

class SettingsUpdate(BaseModel):
    download_path: str

# Custom Exceptions for job control
class DownloadCancelled(Exception):
    pass

class DownloadPaused(Exception):
    pass

# Helper functions for processing
def strip_ansi(text):
    """Remove ANSI escape sequences from strings"""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def progress_hook(d, job_id):
    # Check for cancellation or pause requests from the UI
    status = jobs.get(job_id, {}).get('status')
    if status == 'cancelled':
        raise DownloadCancelled("Download stopped by user")
    if status == 'paused':
        raise DownloadPaused("Download paused by user")

    if d['status'] == 'downloading':
        percent_str = d.get('_percent_str', '0.0%')
        speed_str = d.get('_speed_str', '0.0 MB/s')
        eta_str = d.get('_eta_str', '00:00')
        
        # Clean ANSI codes
        percent_str = strip_ansi(percent_str).strip().replace('%', '')
        
        try:
            jobs[job_id]['percent'] = float(percent_str)
        except (ValueError, TypeError):
            # Fallback to byte calculation if string parsing fails
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            jobs[job_id]['percent'] = (downloaded / total) * 100
            
        jobs[job_id]['speed'] = strip_ansi(speed_str)
        jobs[job_id]['eta'] = strip_ansi(eta_str)
    elif d['status'] == 'finished':
        jobs[job_id]['status'] = 'processing'
        jobs[job_id]['percent'] = 100

def clean_title(title):
    """Remove common YouTube noise from titles for cleaner metadata"""
    # Remove things like [Official Video], (HQ), etc.
    patterns = [
        r'\[.*?\]', 
        r'\(.*?\)', 
        r'Official (Music )?Video', 
        r'HQ', 
        r'Lyrics', 
        r'HD', 
        r'Audio Only',
        r'Explicit'
    ]
    for pattern in patterns:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE)
    # Clean up double spaces and leading/trailing whitespace
    title = re.sub(r'\s+', ' ', title).strip()
    return title

def run_download(job_id: str, req: DownloadRequest, download_path: str):
    ext_map = {
        "mp3": "mp3",
        "m4a": "m4a",
        "aac": "m4a",
        "flac": "flac",
        "ogg": "vorbis",
        "opus": "opus",
        "wav": "wav"
    }
    
    acodec = ext_map.get(req.format, "mp3")
    
    # Try to get cookies if needed, or if specifically desired
    cookie_file = os.path.join(BASE_DIR, "cookies.txt")
    
    # Use 0 for "highest quality / VBR" unless a specific bitrate is forced
    quality = "0" if req.bitrate == "best" else req.bitrate
    
    # Dynamic Session & IP Rolling Logic
    global LAST_COOKIE_REFRESH
    current_time = time.time()
    
    # Auto-refresh cookies every 15 minutes
    if current_time - LAST_COOKIE_REFRESH > 900: # 15 minutes
        print("Session expired or 15 mins passed. Auto-refreshing cookies headlessly...")
        if get_cookies_with_selenium():
            LAST_COOKIE_REFRESH = current_time
            print("Cookies refreshed successfully.")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'ffmpeg_location': 'C:/ffmpeg-8.0-full_build/bin/ffmpeg.exe',
        'outtmpl': os.path.join(download_path, '%(title).100s [%(id)s].%(ext)s'),
        'restrictfilenames': True,
        'nooverwrites': True,
        'continuedl': True,
        'sleep_interval': random.randint(2, 5), # Human-like delay
        'max_sleep_interval': 10,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': acodec,
            'preferredquality': quality,
        }, {
            'key': 'FFmpegMetadata',
            'add_metadata': True,
        }],
        'writethumbnail': req.embed_thumbnail,
        'progress_hooks': [lambda d: progress_hook(d, job_id)],
        'quiet': False,
        'nocolor': True,
        'no_warnings': False,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web_embedded'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/',
        }
    }

    # Dynamic IP Rolling
    if PROXIES:
        selected_proxy = random.choice(PROXIES)
        print(f"Rolling IP... using proxy: {selected_proxy}")
        ydl_opts['proxy'] = selected_proxy

    if os.path.exists(cookie_file):
        ydl_opts['cookiefile'] = cookie_file

    if req.embed_thumbnail:
        ydl_opts['postprocessors'].append({
            'key': 'EmbedThumbnail',
            'already_have_thumbnail': False,
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Starting download for {req.url}")
            info = ydl.extract_info(req.url, download=True)
            if info:
                title = clean_title(info.get('title', 'Unknown'))
                jobs[job_id]['status'] = 'done'
                jobs[job_id]['title'] = title
                print(f"Download finished for: {title}")
            else:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = "Extraction failed: yt-dlp returned no info."
    except DownloadCancelled:
        print(f"Job {job_id} was cancelled.")
        jobs[job_id]['status'] = 'cancelled'
    except DownloadPaused:
        print(f"Job {job_id} was paused.")
        jobs[job_id]['status'] = 'paused'
    except Exception as e:
        print(f"Error during download for {req.url}: {str(e)}")
        jobs[job_id]['status'] = 'failed'
        jobs[job_id]['error'] = str(e)

def download_worker():
    """Serial worker that processes the download queue one-by-one"""
    print("Background download worker started.")
    while True:
        try:
            # Get next task from queue (blocks until one exists)
            job_id, req, download_path = download_queue.get()
            print(f"Worker picked up job: {job_id} ({req.url})")
            run_download(job_id, req, download_path)
            download_queue.task_done()
        except Exception as e:
            print(f"Worker error: {str(e)}")
            time.sleep(1)

def link_account_with_browser():
    """Launch a headed (visible) browser with a persistent profile for manual login"""
    print("Launching visible browser for manual YouTube login...")
    try:
        if not os.path.exists(YT_PROFILE_DIR):
            os.makedirs(YT_PROFILE_DIR)
            
        options = uc.ChromeOptions()
        options.add_argument(f"--user-data-dir={YT_PROFILE_DIR}")
        options.add_argument("--profile-directory=Default")
        
        # Launch headed browser
        driver = uc.Chrome(options=options)
        driver.get("https://music.youtube.com")
        
        print("Waiting for user to log in and close the browser...")
        # The script will block here until the user closes the browser manually
        # This allows them to handle 2FA/Captchas
        while True:
            try:
                _ = driver.window_handles
                time.sleep(1)
            except:
                break
        
        print("Login browser closed. Exporting cookies...")
        # Since uc uses the user-data-dir, cookies are already saved in the profile.
        # We can also explicitly export them to cookies.txt for yt-dlp compatibility.
        # Note: yt-dlp can also use --cookies-from-browser but requiring the profile path is cleaner for headles runs.
        # For now, we'll try to just point yt-dlp to the cookies exported as before.
        
        return True
    except Exception as e:
        print(f"Account linking failed: {str(e)}")
        return False

def get_cookies_with_selenium(url=None):
    """Establish a browser session to get cookies using the persistent profile"""
    print(f"Refreshing session cookies from persistent profile...")
    
    if not os.path.exists(YT_PROFILE_DIR):
        print("No persistent profile found. Please link your account first.")
        return False

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument(f"--user-data-dir={YT_PROFILE_DIR}")
    chrome_options.add_argument("--profile-directory=Default")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
    
    try:
        # Use standard selenium for headless extraction to avoid conflict with uc
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        
        # Visit the specific URL or homepages
        if url:
            driver.get(url)
            time.sleep(5)
        else:
            driver.get("https://www.youtube.com")
            time.sleep(3)
            driver.get("https://music.youtube.com")
            time.sleep(4)
            
        cookies = driver.get_cookies()
        
        with open(COOKIES_FILE, 'w') as f:
            f.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                domain = cookie.get('domain', '')
                domain_specified = 'TRUE' if domain.startswith('.') else 'FALSE'
                path = cookie.get('path', '/')
                secure = 'TRUE' if cookie.get('secure') else 'FALSE'
                expiry = int(cookie.get('expiry', time.time() + 3600 * 24))
                name = cookie.get('name', '')
                value = cookie.get('value', '')
                line = f"{domain}\t{domain_specified}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"
                f.write(line)
        
        global LAST_COOKIE_REFRESH
        LAST_COOKIE_REFRESH = time.time()
        driver.quit()
        return True
    except Exception as e:
        print(f"Cookie refresh failed: {str(e)}")
        return False

# API Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    settings = load_settings()
    return templates.TemplateResponse("index.html", {"request": request, "settings": settings})

@app.post("/api/download")
async def start_download(req: DownloadRequest):
    # Load latest settings
    current_settings = load_settings()
    download_path = current_settings.get("download_path", DEFAULT_DOWNLOAD_PATH)
    
    # Helper to check if URL is already in queue
    def is_already_queued(url):
        for job in jobs.values():
            if job['url'] == url and job['status'] in ['queued', 'downloading', 'processing']:
                return True
        return False
    
    if not req.url.strip():
        return {"status": "error", "message": "URL is empty"}
        
    # Pre-extract info to check if it's a playlist
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'nocolor': True,
    }
    cookie_file = os.path.join(BASE_DIR, "cookies.txt")
    if os.path.exists(cookie_file):
        ydl_opts['cookiefile'] = cookie_file
        
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"Analyzing URL: {req.url}")
            info = ydl.extract_info(req.url, download=False)
            
            # If it's a playlist, explode it
            if 'entries' in info:
                entries = list(info['entries'])
                print(f"Exploding playlist '{info.get('title')}' with {len(entries)} tracks")
                for entry in entries:
                    if not entry: continue
                    sub_url = entry.get('url') or entry.get('webpage_url')
                    if not sub_url:
                        # Construct URL from id if needed
                        if entry.get('id'):
                            sub_url = f"https://www.youtube.com/watch?v={entry['id']}"
                        else:
                            continue
                            
                    if is_already_queued(sub_url):
                        print(f"Skipping duplicate URL in playlist: {sub_url}")
                        continue
                        
                    job_id = str(uuid.uuid4())
                    
                    sub_req = DownloadRequest(
                        url=sub_url,
                        format=req.format,
                        bitrate=req.bitrate,
                        embed_thumbnail=req.embed_thumbnail,
                        embed_metadata=req.embed_metadata
                    )
                        
                    jobs[job_id] = {
                        "id": job_id,
                        "url": sub_url,
                        "title": entry.get('title', 'Pending...'),
                        "status": "queued",
                        "percent": 0.0,
                        "speed": "0.0 MB/s",
                        "eta": "00:00",
                        "_req_data": sub_req.dict(),
                        "_download_path": download_path
                    }
                    download_queue.put((job_id, sub_req, download_path))
            else:
                # Single track
                if is_already_queued(req.url):
                    return {"status": "error", "message": "Track is already in the queue or downloading."}
                    
                job_id = str(uuid.uuid4())
                jobs[job_id] = {
                    "id": job_id,
                    "url": req.url,
                    "title": info.get('title', 'Pending...'),
                    "status": "queued",
                    "percent": 0.0,
                    "speed": "0.0 MB/s",
                    "eta": "00:00",
                    "_req_data": req.dict(),
                    "_download_path": download_path
                }
                download_queue.put((job_id, req, download_path))
                
    except Exception as e:
        print(f"Initial extraction failed: {str(e)}. Attempting direct download...")
        # Fallback to direct download
        job_id = str(uuid.uuid4())
        jobs[job_id] = {
            "id": job_id,
            "url": req.url,
            "title": "Downloading...",
            "status": "queued",
            "percent": 0.0,
            "speed": "0.0 MB/s",
            "eta": "00:00",
            "_req_data": req.dict(),
            "_download_path": download_path
        }
        download_queue.put((job_id, req, download_path))

    return {"status": "success", "message": "Download task(s) created"}

@app.post("/api/jobs/{job_id}/pause")
async def pause_job(job_id: str):
    if job_id in jobs:
        if jobs[job_id]['status'] in ['queued', 'downloading']:
            jobs[job_id]['status'] = 'paused'
            return {"status": "success"}
    return {"status": "error", "message": "Job not found or cannot be paused"}

@app.post("/api/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    if job_id in jobs:
        if jobs[job_id]['status'] == 'paused':
            jobs[job_id]['status'] = 'queued'
            # Re-queue the task
            req_data = jobs[job_id].get('_req_data')
            if req_data:
                req = DownloadRequest(**req_data)
                download_path = jobs[job_id].get('_download_path', DEFAULT_DOWNLOAD_PATH)
                download_queue.put((job_id, req, download_path))
                return {"status": "success"}
    return {"status": "error", "message": "Job not found or cannot be resumed"}

@app.post("/api/jobs/{job_id}/stop")
async def stop_job(job_id: str):
    if job_id in jobs:
        jobs[job_id]['status'] = 'cancelled'
        return {"status": "success"}
    return {"status": "error", "message": "Job not found"}

@app.post("/api/queue/pause")
async def pause_all():
    count = 0
    for job_id, job in jobs.items():
        if job['status'] in ['queued', 'downloading']:
            job['status'] = 'paused'
            count += 1
    return {"status": "success", "message": f"Paused {count} jobs"}

@app.post("/api/queue/resume")
async def resume_all():
    count = 0
    for job_id, job in jobs.items():
        if job['status'] == 'paused':
            job['status'] = 'queued'
            req_data = job.get('_req_data')
            if req_data:
                req = DownloadRequest(**req_data)
                download_path = job.get('_download_path', DEFAULT_DOWNLOAD_PATH)
                download_queue.put((job_id, req, download_path))
                count += 1
    return {"status": "success", "message": f"Resumed {count} jobs"}

@app.post("/api/queue/stop")
async def stop_all():
    count = 0
    for job_id, job in jobs.items():
        if job['status'] in ['queued', 'downloading', 'paused']:
            job['status'] = 'cancelled'
            count += 1
    return {"status": "success", "message": f"Stopped {count} jobs"}

@app.post("/api/refresh-cookies")
async def refresh_cookies():
    success = get_cookies_with_selenium()
    if success:
        return {"status": "success", "message": "Cookies refreshed successfully from persistent profile"}
    else:
        return {"status": "error", "message": "Failed to refresh cookies. Is your account linked?"}

@app.post("/api/link-account")
async def link_account(background_tasks: BackgroundTasks):
    # This is a bit tricky as uc.Chrome() is blocking and needs a UI environment.
    # On a local machine, we can just run it in a thread.
    threading.Thread(target=link_account_with_browser).start()
    return {"status": "success", "message": "Login browser window opened. Please log in and then close it."}

@app.get("/api/account-status")
async def account_status():
    linked = os.path.exists(YT_PROFILE_DIR)
    return {
        "linked": linked,
        "last_refresh": LAST_COOKIE_REFRESH,
        "proxy_count": len(PROXIES)
    }
    has_cookies = os.path.exists(COOKIES_FILE)
    return {
        "linked": linked,
        "has_cookies": has_cookies,
        "cookies_modified": time.ctime(os.path.getmtime(COOKIES_FILE)) if has_cookies else "Never"
    }

@app.get("/api/jobs")
async def get_jobs():
    return list(jobs.values())

@app.get("/api/settings")
async def get_settings():
    return load_settings()

@app.post("/api/settings")
async def update_settings(settings: SettingsUpdate):
    if not os.path.exists(settings.download_path):
        try:
            os.makedirs(settings.download_path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid path: {str(e)}")
    
    save_settings({"download_path": settings.download_path})
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    # Start the serial background worker
    threading.Thread(target=download_worker, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8000)
