# YouTube Downloader

A desktop app for downloading YouTube videos as MP4 or audio as MP3.

## Requirements

- Python
- FFmpeg available on your system `PATH`

The app uses `pytubefix` and `customtkinter`, which are installed from `requirements.txt`.

## Setup and run

### Windows

From the project folder, run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

### macOS and Linux

From the project folder, run:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

## Install FFmpeg

FFmpeg is required to combine video and audio into MP4 and to convert audio to MP3. After installing it, make sure the `ffmpeg` command is available on your `PATH`:

- **Windows:** Download an FFmpeg build, such as the essentials build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/). Extract it and add its `bin` folder to `PATH`.
- **macOS:** Install with Homebrew: `brew install ffmpeg`.
- **Linux:** Install `ffmpeg` with your distribution's package manager.

Verify the installation in a new terminal:

```sh
ffmpeg -version
```

## Use

1. Paste a YouTube video URL into the app.
2. Choose **Video 720p** or **Audio Only**.
3. Select **Download**.

Downloads are saved in the `download` folder beside `app.py`. Video mode prefers 720p when available and uses another available resolution if not. Audio mode saves an MP3.

The downloader uses pytubefix's `VISION_OS` client and selects direct, non-SABR streams. YouTube availability and stream access can vary; if a video cannot be downloaded, the app displays an error rather than switching to SABR streams.
