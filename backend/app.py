from flask import Flask, request, jsonify, send_file, send_from_directory, Response
from flask_cors import CORS
import yt_dlp
import os
import threading
import time
import json
from collections import defaultdict

# ✅ Initialize Flask
app = Flask(__name__)
CORS(app)

# ✅ Define paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_FOLDER = os.path.abspath(os.path.join(BASE_DIR, "../frontend"))
DOWNLOADS_FOLDER = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOADS_FOLDER, exist_ok=True)

# ✅ Load cookies from Render Secret Files
COOKIES_PATH = "/etc/secrets/cookies.txt"  # Path where Render stores secret files

# ✅ Debug: Check if cookies.txt exists
print(f"📂 Checking cookies file: {COOKIES_PATH}, Exists: {os.path.exists(COOKIES_PATH)}")

# ✅ Global dictionary to store download progress for each URL
download_progress = defaultdict(lambda: {"progress": 0, "timestamp": time.time()})

# ✅ Clean up old progress entries periodically
def cleanup_progress_data():
    while True:
        current_time = time.time()
        to_remove = [url for url, data in download_progress.items() if current_time - data["timestamp"] > 1800]
        for url in to_remove:
            del download_progress[url]
        time.sleep(300)

# Start the cleanup thread
threading.Thread(target=cleanup_progress_data, daemon=True).start()

# ✅ Custom progress hook for yt-dlp
def progress_hook(d):
    if d['status'] == 'downloading':
        url = d.get('info_dict', {}).get('webpage_url', 'unknown')
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
        downloaded_bytes = d.get('downloaded_bytes', 0)
        percent = (downloaded_bytes / total_bytes) * 100 if total_bytes > 0 else min(95, download_progress[url]["progress"] + 1)

        download_progress[url] = {
            "progress": percent,
            "timestamp": time.time(),
            "downloaded_bytes": downloaded_bytes,
            "total_bytes": total_bytes,
            "speed": d.get('speed', 0),
            "eta": d.get('eta', 0)
        }

# ✅ Function to delete file after ensuring it's fully sent
def delayed_delete(filepath):
    time.sleep(60)
    try:
        os.remove(filepath)
        print(f"✅ Deleted file: {filepath}")
    except Exception as e:
        print(f"⚠️ Error deleting file: {e}")

# ✅ Serve `index.html` (Frontend UI)
@app.route("/")  
def home():
    return jsonify({"message": "YTCOMET Backend is Running!", "status": "success"}), 200

# ✅ Serve static files (`style.css`, `script.js`)
@app.route("/<path:filename>")
def serve_static_files(filename):
    return send_from_directory(FRONTEND_FOLDER, filename), 200

# ✅ Serve downloaded files properly
@app.route("/downloads/<filename>")
def serve_download(filename):
    file_path = os.path.join(DOWNLOADS_FOLDER, filename)

    if os.path.exists(file_path):
        print(f"📂 Sending file: {file_path}")
        return send_file(file_path, as_attachment=True)
    
    return jsonify({"error": "File not found!"}), 500

# ✅ Add route to check download progress
@app.route('/progress', methods=['POST'])
def check_progress():
    data = request.json
    video_url = data.get("url", "")
    
    if not video_url:
        return jsonify({"error": "No URL provided"}), 400
        
    progress_data = download_progress.get(video_url, {"progress": 0})
    return jsonify(progress_data)

# ✅ Function to find the closest available MP3 bitrate
def find_best_audio_format(video_url, preferred_quality):
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(video_url, download=False)
        available_formats = [fmt["format_id"] for fmt in info["formats"] if "audio" in fmt.get("format_note", "").lower()]

    bitrate_map = {"128k": "140", "192k": "251", "320k": "256"}
    return bitrate_map.get(preferred_quality, available_formats[-1] if available_formats else "bestaudio/best")

# ✅ Download video route (Handles MP3 & MP4 downloads, and playlists)
@app.route('/download', methods=['POST'])
def download_video():
    data = request.json
    video_url = data.get("url")
    format_type = data.get("format")  # "mp3" or "mp4"
    quality = data.get("quality")  # Selected quality

    if not video_url:
        return jsonify({"error": "No URL provided"}), 400

    download_progress[video_url] = {"progress": 0, "timestamp": time.time()}
    output_template = os.path.join(DOWNLOADS_FOLDER, "%(title)s_" + quality + ".%(ext)s")

    # ✅ Improved yt-dlp Download Options
    if format_type == "mp3":
        best_audio_format = find_best_audio_format(video_url, quality)
        options = {
            "format": best_audio_format,
            "outtmpl": output_template,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality}],
            "noplaylist": False,
            "retries": 5,
            "socket_timeout": 30,
            "cookiefile": COOKIES_PATH if os.path.exists(COOKIES_PATH) else None,  # ✅ Use cookies.txt if available
            "progress_hooks": [progress_hook],
        }
    else:
        video_quality = f"bestvideo[height<={quality}]+bestaudio/best/best"
        options = {
            "format": video_quality,
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "noplaylist": False,
            "retries": 5,
            "socket_timeout": 30,
            "cookiefile": COOKIES_PATH if os.path.exists(COOKIES_PATH) else None,  # ✅ Use cookies.txt if available
            "progress_hooks": [progress_hook],
        }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=True)
            download_progress[video_url]["progress"] = 100

            filename = ydl.prepare_filename(info)
            filepath = os.path.join(DOWNLOADS_FOLDER, os.path.basename(filename))

            if format_type == "mp3":
                mp3_filepath = filepath.replace(".webm", ".mp3").replace(".m4a", ".mp3")
                if os.path.exists(mp3_filepath):
                    filepath = mp3_filepath

            if os.path.exists(filepath):
                print(f"📂 Sending file: {filepath}")
                threading.Thread(target=delayed_delete, args=(filepath,), daemon=True).start()
                return send_file(filepath, as_attachment=True, mimetype="application/octet-stream")

            return jsonify({"error": "File not found!"}), 500

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ✅ Run the Flask app
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
