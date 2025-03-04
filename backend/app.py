from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import yt_dlp
import os
import shutil
import threading
import time
from collections import defaultdict

# ✅ Initialize Flask
app = Flask(__name__)
CORS(app)

# ✅ Define Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_FOLDER = os.path.abspath(os.path.join(BASE_DIR, "../frontend"))
DOWNLOADS_FOLDER = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOADS_FOLDER, exist_ok=True)

# ✅ Handle Cookies File
SECRET_COOKIES_PATH = "/etc/secrets/cookies.txt"  # Read-only secret file in Render
WRITABLE_COOKIES_PATH = "/tmp/cookies.txt"  # Copy to a writable location

# ✅ Copy cookies file to a writable location
if os.path.exists(SECRET_COOKIES_PATH):
    shutil.copy(SECRET_COOKIES_PATH, WRITABLE_COOKIES_PATH)
    print(f"📂 Copied cookies.txt to {WRITABLE_COOKIES_PATH}")
else:
    print("❌ No cookies.txt found in /etc/secrets")

# ✅ Global Dictionary for Download Progress
download_progress = defaultdict(lambda: {"progress": 0, "timestamp": time.time()})

# ✅ Clean Up Old Progress Data
def cleanup_progress_data():
    while True:
        current_time = time.time()
        to_remove = [url for url, data in download_progress.items() if current_time - data["timestamp"] > 1800]
        for url in to_remove:
            del download_progress[url]
        time.sleep(300)

# Start Cleanup Thread
threading.Thread(target=cleanup_progress_data, daemon=True).start()

# ✅ Custom Progress Hook
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

# ✅ Function to Delete Files After Sending
def delayed_delete(filepath):
    time.sleep(60)
    try:
        os.remove(filepath)
        print(f"✅ Deleted file: {filepath}")
    except Exception as e:
        print(f"⚠️ Error deleting file: {e}")

# ✅ Root Route (Backend Status)
@app.route("/")  
def home():
    return jsonify({"message": "YTCOMET Backend is Running!", "status": "success"}), 200

# ✅ Serve Static Files
@app.route("/<path:filename>")
def serve_static_files(filename):
    return send_from_directory(FRONTEND_FOLDER, filename), 200

# ✅ Serve Downloaded Files
@app.route("/downloads/<filename>")
def serve_download(filename):
    file_path = os.path.join(DOWNLOADS_FOLDER, filename)

    if os.path.exists(file_path):
        print(f"📂 Sending file: {file_path}")
        return send_file(file_path, as_attachment=True)
    
    return jsonify({"error": "File not found!"}), 500

# ✅ Check Download Progress
@app.route('/progress', methods=['POST'])
def check_progress():
    data = request.json
    video_url = data.get("url", "")
    
    if not video_url:
        return jsonify({"error": "No URL provided"}), 400
        
    progress_data = download_progress.get(video_url, {"progress": 0})
    return jsonify(progress_data)

# ✅ Find Best Audio Format
def find_best_audio_format(video_url, preferred_quality):
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(video_url, download=False)
        available_formats = [fmt["format_id"] for fmt in info["formats"] if "audio" in fmt.get("format_note", "").lower()]

    bitrate_map = {"128k": "140", "192k": "251", "320k": "256"}
    return bitrate_map.get(preferred_quality, available_formats[-1] if available_formats else "bestaudio/best")

# ✅ Download Video (Handles MP3 & MP4)
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

    # ✅ yt-dlp Download Options
    cookies_option = WRITABLE_COOKIES_PATH if os.path.exists(WRITABLE_COOKIES_PATH) else None

    if format_type == "mp3":
        best_audio_format = find_best_audio_format(video_url, quality)
        options = {
            "format": best_audio_format,
            "outtmpl": output_template,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": quality}],
            "noplaylist": False,
            "retries": 5,
            "socket_timeout": 30,
            "cookiefile": cookies_option,  # ✅ Use copied cookies.txt
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
            "cookiefile": cookies_option,  # ✅ Use copied cookies.txt
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

# ✅ Run Flask App
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)
