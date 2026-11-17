import asyncio
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import imageio_ffmpeg
import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
STATIC_DIR = BASE_DIR / "static"

DOWNLOADS_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

FFMPEG_PATH = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()

app = FastAPI(title="Descargador de Videos (TikTok, Facebook, YouTube)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()

URL_REGEX = re.compile(r"https?://[^\s<>\"']+")


class InfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    format_choice: str = "best"  # best, 1080, 720, 480, mp3


def extract_url(raw_text: str) -> str:
    """Extract the first HTTP/HTTPS URL from shared text or raw link."""
    match = URL_REGEX.search(raw_text.strip())
    if not match:
        raise ValueError("No se encontró un enlace válido en el texto.")
    return match.group(0).rstrip(".,;)")


def detect_platform(url: str) -> str:
    lower = url.lower()
    if "tiktok.com" in lower:
        return "TikTok"
    if "facebook.com" in lower or "fb.watch" in lower or "fb.com" in lower:
        return "Facebook"
    if "youtube.com" in lower or "youtu.be" in lower:
        return "YouTube"
    return "Otro"


def format_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "--:--"
    total = int(seconds)
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def build_ydl_format(choice: str) -> Dict[str, Any]:
    if choice == "mp3":
        return {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    if choice == "1080":
        return {
            "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "merge_output_format": "mp4",
        }
    if choice == "720":
        return {
            "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "merge_output_format": "mp4",
        }
    if choice == "480":
        return {
            "format": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]/best",
            "merge_output_format": "mp4",
        }
    # Default: best quality MP4
    return {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best",
        "merge_output_format": "mp4",
    }


def fetch_video_info(url: str) -> Dict[str, Any]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "ffmpeg_location": FFMPEG_PATH,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if "entries" in info and info["entries"]:
            info = info["entries"][0]
        return {
            "title": info.get("title") or "Video sin título",
            "thumbnail": info.get("thumbnail"),
            "duration": format_duration(info.get("duration")),
            "uploader": info.get("uploader") or info.get("creator") or info.get("channel") or "Desconocido",
            "platform": detect_platform(url),
            "webpage_url": info.get("webpage_url") or url,
        }


def run_download_job(job_id: str, url: str, format_choice: str) -> None:
    def progress_hook(d: Dict[str, Any]) -> None:
        with jobs_lock:
            job = jobs.get(job_id)
            if not job:
                return
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                pct = (downloaded / total * 100) if total > 0 else 0
                job["status"] = "downloading"
                job["progress"] = round(min(pct, 99.9), 1)
                job["speed"] = d.get("_speed_str", "").strip()
                job["eta"] = d.get("_eta_str", "").strip()
            elif status == "finished":
                job["status"] = "processing"
                job["progress"] = 99.0

    def postprocessor_hook(d: Dict[str, Any]) -> None:
        if d.get("status") == "finished":
            filepath = d.get("info_dict", {}).get("filepath")
            if filepath:
                with jobs_lock:
                    if job_id in jobs:
                        jobs[job_id]["filepath"] = filepath
                        jobs[job_id]["filename"] = Path(filepath).name

    format_opts = build_ydl_format(format_choice)
    outtmpl = str(DOWNLOADS_DIR / f"%(title).80s_{job_id[:6]}.%(ext)s")

    ydl_opts: Dict[str, Any] = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": FFMPEG_PATH,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "restrictfilenames": True,
        **format_opts,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

            expected_path = ydl.prepare_filename(info)
            if format_choice == "mp3":
                expected_path = str(Path(expected_path).with_suffix(".mp3"))
            elif format_opts.get("merge_output_format") == "mp4":
                mp4_candidate = str(Path(expected_path).with_suffix(".mp4"))
                if Path(mp4_candidate).exists():
                    expected_path = mp4_candidate

            with jobs_lock:
                job = jobs[job_id]
                final_path = job.get("filepath") or expected_path
                if not Path(final_path).exists():
                    # Fallback: find matching file by job_id suffix
                    matches = list(DOWNLOADS_DIR.glob(f"*_{job_id[:6]}.*"))
                    if matches:
                        final_path = str(matches[0])

                job["filepath"] = final_path
                job["filename"] = Path(final_path).name
                job["title"] = info.get("title") or job["filename"]
                job["status"] = "completed"
                job["progress"] = 100.0
    except Exception as exc:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = str(exc)


@app.post("/api/info")
async def api_info(req: InfoRequest) -> JSONResponse:
    try:
        clean_url = extract_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        data = await asyncio.to_thread(fetch_video_info, clean_url)
        return JSONResponse(data)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"No se pudo obtener información del video: {exc}",
        )


@app.post("/api/download")
async def api_download(req: DownloadRequest) -> JSONResponse:
    try:
        clean_url = extract_url(req.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "url": clean_url,
            "platform": detect_platform(clean_url),
            "format_choice": req.format_choice,
            "status": "queued",
            "progress": 0.0,
            "speed": "",
            "eta": "",
            "filename": None,
            "filepath": None,
            "title": None,
            "error": None,
        }

    thread = threading.Thread(
        target=run_download_job,
        args=(job_id, clean_url, req.format_choice),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"job_id": job_id})


@app.get("/api/progress/{job_id}")
async def api_progress(job_id: str) -> JSONResponse:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Descarga no encontrada.")
        return JSONResponse({
            "job_id": job["job_id"],
            "status": job["status"],
            "progress": job["progress"],
            "speed": job["speed"],
            "eta": job["eta"],
            "filename": job["filename"],
            "title": job["title"],
            "error": job["error"],
        })


@app.get("/api/file/{job_id}")
async def api_file(job_id: str) -> FileResponse:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or not job.get("filepath"):
            raise HTTPException(status_code=404, detail="Archivo no disponible.")
        filepath = Path(job["filepath"])

    if not filepath.exists():
        raise HTTPException(status_code=404, detail="El archivo ya no existe en el servidor.")

    return FileResponse(
        path=filepath,
        filename=filepath.name,
        media_type="application/octet-stream",
    )


@app.get("/api/history")
async def api_history() -> JSONResponse:
    files = []
    for path in sorted(DOWNLOADS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_file():
            size_mb = round(path.stat().st_size / (1024 * 1024), 2)
            files.append({
                "filename": path.name,
                "size_mb": size_mb,
            })
    return JSONResponse({"files": files[:20]})


@app.get("/api/history/{filename}")
async def api_history_file(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    filepath = DOWNLOADS_DIR / safe_name
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    return FileResponse(
        path=filepath,
        filename=safe_name,
        media_type="application/octet-stream",
    )


@app.delete("/api/history/{filename}")
async def api_delete_history(filename: str) -> JSONResponse:
    safe_name = Path(filename).name
    filepath = DOWNLOADS_DIR / safe_name
    if filepath.exists() and filepath.is_file():
        filepath.unlink()
    return JSONResponse({"deleted": safe_name})


@app.get("/manifest.json")
async def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/.well-known/assetlinks.json")
async def assetlinks() -> FileResponse:
    return FileResponse(STATIC_DIR / "assetlinks.json", media_type="application/json")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
