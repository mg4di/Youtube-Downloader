import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import customtkinter
from pytubefix import YouTube
from pytubefix.extract import video_id as extract_video_id
from pytubefix.streams import Stream
from pytubefix.exceptions import (
    RegexMatchError, 
    VideoUnavailable, 
    VideoPrivate, 
    MembersOnly, 
    AgeRestrictedError
)

# DATA MODELS & ENUMS
class DownloadMode(Enum):
    VIDEO_720P = "720p"
    AUDIO_ONLY = "audio"

@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[str]
    error_message: Optional[str]
    title: str = ""

    def __post_init__(self):
        if self.success and self.error_message is not None:
            raise ValueError("DownloadResult cannot be successful and contain an error message simultaneously.")
        if not self.success and not self.error_message:
            raise ValueError("A failed DownloadResult must include an error_message.")

@dataclass
class DownloadConfig:
    url: str
    mode: DownloadMode
    output_path: str
    yt: Optional[YouTube] = None  # Reuse YouTube object
    on_complete: Optional[Callable[[DownloadResult], None]] = None
    on_error: Optional[Callable[[DownloadResult], None]] = None


# UTILITY FUNCTIONS
_SANITIZE_RE = re.compile(r'[\\/*?:"<>|]')
_WHITESPACE_RE = re.compile(r'\s+')
_DIRECT_STREAM_CHUNK_SIZE = 1024 * 1024
_MIN_DIRECT_STREAM_CHUNK_SIZE = 64 * 1024
_CONTENT_RANGE_RE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")

def sanitize_filename(title: str) -> str:
    sanitized = _SANITIZE_RE.sub('', title)
    sanitized = _WHITESPACE_RE.sub(' ', sanitized).strip('._ ')
    return sanitized[:150] if len(sanitized) > 150 else (sanitized or "youtube_download")

def _get_unique_path(output_path: str, base_name: str, ext: str) -> str:
    """Generate unique file path with counter suffix."""
    path = os.path.join(output_path, f"{base_name}.{ext}")
    if not os.path.exists(path):
        return path
    counter = 1
    while True:
        new_path = os.path.join(output_path, f"{base_name}_{counter}.{ext}")
        if not os.path.exists(new_path):
            return new_path
        counter += 1

def _cleanup_temp_files(paths: list[str]):
    """Clean up temporary files efficiently."""
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

def _run_ffmpeg(cmd: list[str], temp_files: list[str]) -> bool:
    """Run FFmpeg command with error handling and timeout."""
    try:
        # 10 minute timeout for FFmpeg (should be plenty for 720p merge)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=600)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        _cleanup_temp_files(temp_files)
        return False


# DOWNLOAD HANDLER MODULE (WITH FFMPEG)
def _handle_download_error(e: Exception, yt: Optional[YouTube], temp_files: list[str]) -> DownloadResult:
    """Centralized error handling."""
    _cleanup_temp_files(temp_files)
    title = yt._title or "" if yt else ""

    if isinstance(e, FileNotFoundError):
        return DownloadResult(False, None, "Error: FFmpeg is required. Please install FFmpeg.", title)
    if isinstance(e, HTTPError) and e.code == 403:
        return DownloadResult(
            False,
            None,
            "YouTube rejected a VISION_OS direct media range (HTTP 403) even after refreshing the stream URL. No SABR stream was used.",
            title,
        )
    if isinstance(e, subprocess.CalledProcessError):
        return DownloadResult(False, None, "FFmpeg processing failed.", title)
    if isinstance(e, AgeRestrictedError):
        return DownloadResult(False, None, "Failed: Video is age-restricted.", title)
    if isinstance(e, MembersOnly):
        return DownloadResult(False, None, "Failed: Video is restricted to members only.", title)
    if isinstance(e, VideoPrivate):
        return DownloadResult(False, None, "Failed: Video is private.", title)
    if isinstance(e, RegexMatchError):
        return DownloadResult(False, None, f"YouTube data extraction failed: {e}", title)
    if isinstance(e, VideoUnavailable):
        return DownloadResult(False, None, "Video is unavailable with the supported YouTube clients.", title)
    return DownloadResult(False, None, f"An error occurred: {str(e)}", title)


def _create_youtube(url: str) -> YouTube:
    """Create a YouTube object without pytubefix switching to other clients."""
    yt = YouTube(url, "VISION_OS")
    yt.fallback_clients = []
    return yt


def _best_non_sabr_audio_stream(yt: YouTube) -> Optional[Stream]:
    """Prefer WebM audio because YouTube commonly denies AAC streams with HTTP 403."""
    for query in (
        yt.streams.filter(only_audio=True, mime_type="audio/webm"),
        yt.streams.filter(only_audio=True),
    ):
        audio_stream = None
        for stream in query.order_by("abr"):
            if not stream.is_sabr:
                audio_stream = stream
        if audio_stream is not None:
            return audio_stream
    return None


def _download_direct_stream(stream: Stream, destination_path: str, timeout: int) -> None:
    """Download direct streams with HTTP Range headers instead of pytubefix's range query."""
    if stream.is_sabr:
        raise RuntimeError("Refusing to download a SABR-protected stream.")

    total_size = stream.filesize
    if total_size <= 0:
        raise RuntimeError("YouTube did not provide a valid direct-stream size.")

    downloaded = 0
    chunk_size = _DIRECT_STREAM_CHUNK_SIZE
    with open(destination_path, "wb") as destination:
        while downloaded < total_size:
            requested_end = min(
                downloaded + chunk_size - 1,
                total_size - 1,
            )
            request = Request(
                stream.url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "accept-language": "en-US,en",
                    "Range": f"bytes={downloaded}-{requested_end}",
                },
            )

            try:
                response = urlopen(request, timeout=timeout)
            except HTTPError as error:
                if error.code != 403 or chunk_size <= _MIN_DIRECT_STREAM_CHUNK_SIZE:
                    raise
                error.close()
                chunk_size = max(chunk_size // 2, _MIN_DIRECT_STREAM_CHUNK_SIZE)
                continue

            with response:
                if response.status != 206:
                    raise RuntimeError(
                        f"YouTube returned HTTP {response.status} for a direct media range request."
                    )

                content_range = _CONTENT_RANGE_RE.fullmatch(
                    response.headers.get("Content-Range", "")
                )
                if content_range is None:
                    raise RuntimeError("YouTube returned an invalid Content-Range header.")

                range_start, range_end, response_size = map(int, content_range.groups())
                if (
                    range_start != downloaded
                    or range_end < range_start
                    or range_end > requested_end
                    or response_size != total_size
                ):
                    raise RuntimeError("YouTube returned an unexpected media byte range.")

                expected_bytes = range_end - range_start + 1
                received_bytes = 0
                while received_bytes < expected_bytes:
                    chunk = response.read(min(64 * 1024, expected_bytes - received_bytes))
                    if not chunk:
                        raise RuntimeError("YouTube ended a media range before all bytes arrived.")
                    destination.write(chunk)
                    received_bytes += len(chunk)

            downloaded += received_bytes


def _download_with_vision_os(config: DownloadConfig, download_attempt: Callable[[YouTube], DownloadResult]) -> DownloadResult:
    """Use only the non-SABR client and surface its errors without retrying other clients."""
    current_yt = config.yt
    if current_yt is None or current_yt.client != "VISION_OS":
        current_yt = _create_youtube(config.url)
    else:
        current_yt.fallback_clients = []

    try:
        return download_attempt(current_yt)
    except HTTPError as error:
        if error.code != 403:
            return _handle_download_error(error, current_yt, [])
        error.close()

        refreshed_yt: Optional[YouTube] = None
        try:
            refreshed_yt = _create_youtube(config.url)
            return download_attempt(refreshed_yt)
        except Exception as retry_error:
            return _handle_download_error(retry_error, refreshed_yt or current_yt, [])
    except Exception as error:
        return _handle_download_error(error, current_yt, [])


def download_video_720p(config: DownloadConfig) -> DownloadResult:
    """Downloads High-Quality Adaptive Video and merges it with Audio via FFmpeg."""
    def _do_download(yt_obj):
        sanitized_title = sanitize_filename(yt_obj.title)
        temp_files = []
        
        video_stream = next(
            (
                stream
                for stream in yt_obj.streams.filter(adaptive=True, only_video=True, res="720p")
                if not stream.is_sabr
            ),
            None,
        )
        if video_stream is None:
            for stream in yt_obj.streams.filter(adaptive=True, only_video=True).order_by("resolution"):
                if not stream.is_sabr:
                    video_stream = stream
            if video_stream is None:
                raise RuntimeError("YouTube only provided SABR-protected video streams for this client.")

        audio_stream = _best_non_sabr_audio_stream(yt_obj)
        if audio_stream is None:
            raise RuntimeError("YouTube only provided SABR-protected audio streams for this client.")
        
        # Use tempfile for safer temp file handling
        with tempfile.NamedTemporaryFile(suffix=f".{video_stream.subtype}", dir=config.output_path, delete=False) as vf:
            temp_video_path = vf.name
        with tempfile.NamedTemporaryFile(suffix=f".{audio_stream.subtype}", dir=config.output_path, delete=False) as af:
            temp_audio_path = af.name
        
        temp_files = [temp_video_path, temp_audio_path]
        
        try:
            _download_direct_stream(video_stream, temp_video_path, timeout=300)
            _download_direct_stream(audio_stream, temp_audio_path, timeout=300)
            if any(os.path.getsize(path) == 0 for path in temp_files):
                raise RuntimeError("YouTube returned an empty media stream.")
        except Exception:
            _cleanup_temp_files(temp_files)
            raise

        final_path = _get_unique_path(config.output_path, sanitized_title, "mp4")

        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', temp_video_path, '-i', temp_audio_path,
            '-c:v', 'copy', '-c:a', 'copy', final_path
        ]
        
        if not _run_ffmpeg(ffmpeg_cmd, temp_files):
            return DownloadResult(False, None, "Failed to merge audio and video using FFmpeg.", yt_obj.title)

        _cleanup_temp_files(temp_files)
        return DownloadResult(True, final_path, None, yt_obj.title) if os.path.exists(final_path) else DownloadResult(False, None, "Failed to create final file.", yt_obj.title)

    return _download_with_vision_os(config, _do_download)


def download_audio_only(config: DownloadConfig) -> DownloadResult:
    """Downloads the best audio stream and converts it to MP3 format via FFmpeg."""
    def _do_download(yt_obj):
        sanitized_title = sanitize_filename(yt_obj.title)
        temp_files = []
        
        audio_stream = _best_non_sabr_audio_stream(yt_obj)
        if audio_stream is None:
            raise RuntimeError("YouTube only provided SABR-protected audio streams for this client.")
        
        with tempfile.NamedTemporaryFile(suffix=f".{audio_stream.subtype}", dir=config.output_path, delete=False) as af:
            temp_audio_path = af.name
        temp_files = [temp_audio_path]

        try:
            _download_direct_stream(audio_stream, temp_audio_path, timeout=300)
            if os.path.getsize(temp_audio_path) == 0:
                raise RuntimeError("YouTube returned an empty audio stream.")
        except Exception:
            _cleanup_temp_files(temp_files)
            raise
        
        final_path = _get_unique_path(config.output_path, sanitized_title, "mp3")
        
        ffmpeg_cmd = ['ffmpeg', '-y', '-i', temp_audio_path, '-b:a', '320k', final_path]
        
        if not _run_ffmpeg(ffmpeg_cmd, temp_files):
            return DownloadResult(False, None, "Failed to convert audio to MP3.", yt_obj.title)

        _cleanup_temp_files(temp_files)
        return DownloadResult(True, final_path, None, yt_obj.title) if os.path.exists(final_path) else DownloadResult(False, None, "Failed to create MP3 file.", yt_obj.title)

    return _download_with_vision_os(config, _do_download)


# WORKER THREAD & UI CONTROLLER LAYER
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="yt-dl")

def _run_download_worker(config: DownloadConfig, app_ctx: customtkinter.CTk):
    try:
        result = download_video_720p(config) if config.mode == DownloadMode.VIDEO_720P else download_audio_only(config)
    except Exception as error:
        result = _handle_download_error(error, config.yt, [])
    callback = config.on_complete if result.success else config.on_error
    if callback:
        app_ctx.after(0, lambda r=result: callback(r))

def start_download_threaded(config: DownloadConfig, app_ctx: customtkinter.CTk) -> None:
    _executor.submit(_run_download_worker, config, app_ctx)


class YouTubeDownloaderApp(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.geometry("720x480")
        self.title("YouTube Video Downloader")
        customtkinter.set_appearance_mode("System")
        customtkinter.set_default_color_theme("blue")
        
        self._yt_cache: Optional[YouTube] = None
        self._yt_cache_url = ""
        self._cached_url = ""
        self._build_ui()

    def _build_ui(self):
        self.title_label = customtkinter.CTkLabel(self, text="", font=customtkinter.CTkFont(size=14), text_color="lightgray", wraplength=600)
        self.title_label.pack(padx=10, pady=(30, 5))
        
        self.link_entry = customtkinter.CTkEntry(self, width=450, height=40, placeholder_text="Enter YouTube Video URL")
        self.link_entry.pack(padx=10, pady=5)
        self.link_entry.bind("<FocusOut>", self._fetch_title)
        self.link_entry.bind("<KeyRelease>", self._on_url_change)
        
        self.mode_label = customtkinter.CTkLabel(self, text="Download Mode:", font=customtkinter.CTkFont(size=13, weight="bold"))
        self.mode_label.pack(padx=10, pady=(20, 5))
        
        self.mode_selector = customtkinter.CTkSegmentedButton(self, values=["Video 720p", "Audio Only"])
        self.mode_selector.pack(padx=10, pady=5)
        self.mode_selector.set("Video 720p")
        
        self.progress_bar = customtkinter.CTkProgressBar(self, width=400)
        self.progress_bar.pack(padx=10, pady=20)
        self.progress_bar.set(0)
        
        self.download_button = customtkinter.CTkButton(self, text="Download", width=200, height=40, command=self._on_download)
        self.download_button.pack(padx=10, pady=5)
        
        self.status_label = customtkinter.CTkLabel(self, text="", font=customtkinter.CTkFont(size=13, weight="bold"))
        self.status_label.pack(padx=10, pady=15)

    def _on_url_change(self, event=None):
        url = self.link_entry.get().strip()
        if url == self._cached_url:
            return
        self._yt_cache = None
        self._yt_cache_url = ""
        if len(url) > 15:
            self._fetch_title()
        else:
            self._cached_url = url

    def _fetch_title(self, event=None):
        url = self.link_entry.get().strip()
        if not url or url == self._cached_url:
            return
        self._cached_url = url
        _executor.submit(self._title_worker, url)

    def _title_worker(self, url: str):
        try:
            yt = _create_youtube(url)
        except Exception:
            yt = None

        def update_title():
            if url != self._cached_url:
                return
            self._yt_cache = yt
            self._yt_cache_url = url if yt is not None else ""
            self.title_label.configure(text=yt.title if yt is not None else "")

        self.after(0, update_title)

    def _on_download(self):
        entered_url = self.link_entry.get().strip()
        if not entered_url:
            self.status_label.configure(text="Please enter a URL first", text_color="orange")
            return

        try:
            video_id = extract_video_id(entered_url)
        except RegexMatchError:
            self.status_label.configure(text="Please enter a valid YouTube video URL", text_color="orange")
            return

        url = f"https://www.youtube.com/watch?v={video_id}"

        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download")
        os.makedirs(output_dir, exist_ok=True)

        mode_enum = DownloadMode.VIDEO_720P if self.mode_selector.get() == "Video 720p" else DownloadMode.AUDIO_ONLY

        self.download_button.configure(state="disabled")
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()
        self.status_label.configure(
            text="Downloading Video 720p..." if mode_enum == DownloadMode.VIDEO_720P else "Downloading Audio MP3...",
            text_color="white"
        )

        # Reuse cached YouTube object if URL matches
        yt = self._yt_cache if entered_url == self._yt_cache_url else None
        
        config = DownloadConfig(
            url=url, mode=mode_enum, output_path=output_dir, yt=yt,
            on_complete=self._on_complete, on_error=self._on_error
        )
        start_download_threaded(config, self)

    def _on_complete(self, result: DownloadResult):
        self._reset_ui()
        self.status_label.configure(
            text=f"Success!\nSaved in 'download' folder:\n{os.path.basename(result.file_path)}",
            text_color="green"
        )
        self.link_entry.delete(0, 'end')
        self._cached_url = ""
        self._yt_cache = None
        self._yt_cache_url = ""

    def _on_error(self, result: DownloadResult):
        self._reset_ui()
        self.status_label.configure(text=result.error_message, text_color="red")
        self._yt_cache = None
        self._yt_cache_url = ""

    def _reset_ui(self):
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.download_button.configure(state="normal")


# APPLICATION RUNTIME ENTRYPOINT
if __name__ == "__main__":
    app = YouTubeDownloaderApp()
    try:
        app.mainloop()
    finally:
        _executor.shutdown(wait=False)