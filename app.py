import os
import re
import threading
import subprocess
from enum import Enum
from dataclasses import dataclass
from typing import Callable, Optional
import customtkinter
from pytubefix import YouTube
from pytubefix.exceptions import (
    RegexMatchError, 
    VideoUnavailable, 
    VideoPrivate, 
    MembersOnly, 
    AgeRestrictedError
)

# ==========================================
# 1. DATA MODELS & ENUMS
# ==========================================

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
    on_complete: Optional[Callable[[DownloadResult], None]] = None
    on_error: Optional[Callable[[DownloadResult], None]] = None

# ==========================================
# 2. UTILITY FUNCTIONS
# ==========================================

def sanitize_filename(title: str) -> str:
    sanitized = re.sub(r'[\\/*?:"<>|]', '', title)
    sanitized = re.sub(r'\s+', ' ', sanitized)
    sanitized = sanitized.strip('._ ')
    
    if len(sanitized) > 150:
        sanitized = sanitized[:150]
        
    return sanitized if sanitized else "youtube_download"

def clean_temporary_files(output_path: str, sanitized_title: str):
    """Removes remaining temporary files (.part, .tmp, or FFmpeg chunks)"""
    try:
        if os.path.exists(output_path):
            for item in os.listdir(output_path):
                if (item.startswith(f"temp_v_{sanitized_title}") or 
                    item.startswith(f"temp_a_{sanitized_title}") or 
                    item.endswith('.part') or item.endswith('.tmp')):
                    file_to_remove = os.path.join(output_path, item)
                    if os.path.exists(file_to_remove):
                        os.remove(file_to_remove)
    except Exception:
        pass

# ==========================================
# 3. DOWNLOAD HANDLER MODULE (WITH FFMEG)
# ==========================================

def download_video_720p(config: DownloadConfig) -> DownloadResult:
    """Downloads High-Quality Adaptive Video and merges it with Audio via FFmpeg"""
    sanitized_title = "video"
    temp_video_path = ""
    temp_audio_path = ""
    try:
        yt = YouTube(config.url)
        sanitized_title = sanitize_filename(yt.title)
        
        video_stream = yt.streams.filter(adaptive=True, only_video=True, res="720p").first()
        if video_stream is None:
            video_stream = yt.streams.filter(adaptive=True, only_video=True).order_by("resolution").last()
            if video_stream is None:
                return DownloadResult(success=False, file_path=None, error_message="Video stream is unavailable.", title=yt.title)
        
        audio_stream = yt.streams.get_audio_only()
        if audio_stream is None:
            return DownloadResult(success=False, file_path=None, error_message="Audio stream is unavailable.", title=yt.title)
            
        temp_video_name = f"temp_v_{sanitized_title}.{video_stream.subtype}"
        temp_audio_name = f"temp_a_{sanitized_title}.{audio_stream.subtype}"
        
        temp_video_path = video_stream.download(output_path=config.output_path, filename=temp_video_name)
        temp_audio_path = audio_stream.download(output_path=config.output_path, filename=temp_audio_name)
        
        ext = "mp4"
        base_filename = sanitized_title
        counter = 1
        while os.path.exists(os.path.join(config.output_path, f"{base_filename}.{ext}")):
            base_filename = f"{sanitized_title}_{counter}"
            counter += 1
        final_path = os.path.join(config.output_path, f"{base_filename}.{ext}")
        
        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-i', temp_video_path,
            '-i', temp_audio_path,
            '-c:v', 'copy',
            '-c:a', 'copy',
            final_path
        ]
        subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
        if os.path.exists(temp_video_path): os.remove(temp_video_path)
        if os.path.exists(temp_audio_path): os.remove(temp_audio_path)
            
        if os.path.exists(final_path):
            return DownloadResult(success=True, file_path=final_path, error_message=None, title=yt.title)
        return DownloadResult(success=False, file_path=None, error_message="Failed to process the final file.", title=yt.title)
        
    except FileNotFoundError:
        clean_temporary_files(config.output_path, sanitized_title)
        return DownloadResult(success=False, file_path=None, error_message="Error: FFmpeg is required. Please ensure FFmpeg is installed on your system.", title=yt.title)
    except subprocess.CalledProcessError:
        clean_temporary_files(config.output_path, sanitized_title)
        return DownloadResult(success=False, file_path=None, error_message="Failed to merge audio and video using FFmpeg.", title=yt.title)
    except (RegexMatchError, VideoUnavailable):
        return DownloadResult(success=False, file_path=None, error_message="Invalid URL or video is unavailable.")
    except VideoPrivate:
        return DownloadResult(success=False, file_path=None, error_message="Failed: Video is private.")
    except MembersOnly:
        return DownloadResult(success=False, file_path=None, error_message="Failed: Video is restricted to members only.")
    except AgeRestrictedError:
        return DownloadResult(success=False, file_path=None, error_message="Failed: Video is age-restricted.")
    except Exception as e:
        clean_temporary_files(config.output_path, sanitized_title)
        return DownloadResult(success=False, file_path=None, error_message=f"An error occurred: {str(e)}")


def download_audio_only(config: DownloadConfig) -> DownloadResult:
    """Downloads the best audio stream and converts it to MP3 format via FFmpeg"""
    sanitized_title = "audio"
    temp_audio_path = ""
    try:
        yt = YouTube(config.url)
        sanitized_title = sanitize_filename(yt.title)
        
        audio_stream = yt.streams.get_audio_only()
        if audio_stream is None:
            return DownloadResult(success=False, file_path=None, error_message="Audio stream is unavailable.", title=yt.title)
            
        temp_audio_name = f"temp_a_{sanitized_title}.{audio_stream.subtype}"
        temp_audio_path = audio_stream.download(output_path=config.output_path, filename=temp_audio_name)
        
        ext = "mp3"
        base_filename = sanitized_title
        counter = 1
        while os.path.exists(os.path.join(config.output_path, f"{base_filename}.{ext}")):
            base_filename = f"{sanitized_title}_{counter}"
            counter += 1
        final_path = os.path.join(config.output_path, f"{base_filename}.{ext}")
        
        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-i', temp_audio_path,
            '-b:a', '320k',
            final_path
        ]
        subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
            
        if os.path.exists(final_path):
            return DownloadResult(success=True, file_path=final_path, error_message=None, title=yt.title)
        return DownloadResult(success=False, file_path=None, error_message="Failed to create MP3 file.", title=yt.title)
        
    except FileNotFoundError:
        clean_temporary_files(config.output_path, sanitized_title)
        return DownloadResult(success=False, file_path=None, error_message="Error: MP3 conversion requires FFmpeg installed on your system.", title=yt.title)
    except subprocess.CalledProcessError:
        clean_temporary_files(config.output_path, sanitized_title)
        return DownloadResult(success=False, file_path=None, error_message="Failed to convert audio to MP3.", title=yt.title)
    except Exception as e:
        clean_temporary_files(config.output_path, sanitized_title)
        return DownloadResult(success=False, file_path=None, error_message=f"Connection lost while downloading audio: {str(e)}")

# ==========================================
# 4. WORKER THREAD & UI CONTROLLER LAYER
# ==========================================

def _run_download_worker(config: DownloadConfig, app_ctx: customtkinter.CTk):
    if config.mode == DownloadMode.VIDEO_720P:
        result = download_video_720p(config)
    else:
        result = download_audio_only(config)
        
    if result.success:
        app_ctx.after(0, lambda: config.on_complete(result))
    else:
        app_ctx.after(0, lambda: config.on_error(result))

def start_download_threaded(config: DownloadConfig, app_ctx: customtkinter.CTk) -> None:
    thread = threading.Thread(target=_run_download_worker, args=[config, app_ctx], daemon=True)
    thread.start()


class YouTubeDownloaderApp(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        
        self.geometry("720x480")
        self.title("YouTube Video Downloader")
        
        customtkinter.set_appearance_mode("System")
        customtkinter.set_default_color_theme("blue")
        
        self.current_validated_url = ""
        self._build_ui()

    def _build_ui(self):
        self.title_label = customtkinter.CTkLabel(self, text="", font=customtkinter.CTkFont(size=14), text_color="lightgray", wraplength=600)
        self.title_label.pack(padx=10, pady=(30, 5))
        
        self.link_entry = customtkinter.CTkEntry(self, width=450, height=40, placeholder_text="Enter YouTube Video URL")
        self.link_entry.pack(padx=10, pady=5)
        self.link_entry.bind("<FocusOut>", self.async_fetch_title)
        self.link_entry.bind("<KeyRelease>", self.on_url_typing)
        
        self.mode_label = customtkinter.CTkLabel(self, text="Download Mode:", font=customtkinter.CTkFont(size=13, weight="bold"))
        self.mode_label.pack(padx=10, pady=(20, 5))
        
        self.mode_selector = customtkinter.CTkSegmentedButton(self, values=["Video 720p", "Audio Only"])
        self.mode_selector.pack(padx=10, pady=5)
        self.mode_selector.set("Video 720p")
        
        self.progress_bar = customtkinter.CTkProgressBar(self, width=400)
        self.progress_bar.pack(padx=10, pady=20)
        self.progress_bar.set(0)
        
        self.download_button = customtkinter.CTkButton(self, text="Download", width=200, height=40, command=self.on_download_click)
        self.download_button.pack(padx=10, pady=5)
        
        self.status_label = customtkinter.CTkLabel(self, text="", font=customtkinter.CTkFont(size=13, weight="bold"))
        self.status_label.pack(padx=10, pady=15)

    def on_url_typing(self, event=None):
        url = self.link_entry.get().strip()
        if len(url) > 15 and url != self.current_validated_url:
            self.async_fetch_title()

    def async_fetch_title(self, event=None):
        url = self.link_entry.get().strip()
        if not url or url == self.current_validated_url:
            return
        self.current_validated_url = url
        threading.Thread(target=self._title_fetch_worker, args=[url], daemon=True).start()

    def _title_fetch_worker(self, url: str):
        try:
            yt = YouTube(url)
            self.after(0, lambda: self.title_label.configure(text=yt.title))
        except Exception:
            self.after(0, lambda: self.title_label.configure(text=""))

    def on_download_click(self):
        url = self.link_entry.get().strip()
        if not url:
            self.status_label.configure(text="Please enter a URL first", text_color="orange")
            return
            
        script_parent_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_parent_dir, "download")
        os.makedirs(output_dir, exist_ok=True)
            
        selected_mode = self.mode_selector.get()
        mode_enum = DownloadMode.VIDEO_720P if selected_mode == "Video 720p" else DownloadMode.AUDIO_ONLY
        
        self.download_button.configure(state="disabled")
        
        if mode_enum == DownloadMode.VIDEO_720P:
            self.status_label.configure(text="Downloading Video 720p...", text_color="white")
        else:
            self.status_label.configure(text="Downloading Audio MP3...", text_color="white")
            
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()
        
        config = DownloadConfig(
            url=url, mode=mode_enum, output_path=output_dir,
            on_complete=self.on_download_complete, on_error=self.on_download_error
        )
        start_download_threaded(config, self)

    def on_download_complete(self, result: DownloadResult):
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.download_button.configure(state="normal")
        self.status_label.configure(text=f"Success!\nSaved in 'download' folder:\n{os.path.basename(result.file_path)}", text_color="green")
        self.link_entry.delete(0, 'end')
        self.current_validated_url = ""

    def on_download_error(self, result: DownloadResult):
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.download_button.configure(state="normal")
        self.status_label.configure(text=result.error_message, text_color="red")

# ==========================================
# 5. APPLICATION RUNTIME ENTRYPOINT
# ==========================================

if __name__ == "__main__":
    app = YouTubeDownloaderApp()
    app.mainloop()