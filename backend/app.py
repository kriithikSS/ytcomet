from flask import Flask, request, jsonify, send_file, send_from_directory,Response
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

# Global dictionary to store download progress for each URL
download_progress = defaultdict(lambda: {"progress": 0, "timestamp": time.time()})

# ✅ Clean up old progress entries periodically
def cleanup_progress_data():
    while True:
        current_time = time.time()
        # Remove entries older than 30 minutes
        to_remove = []
        
        for url, data in download_progress.items():
            if current_time - data["timestamp"] > 1800:  # 30 minutes
                to_remove.append(url)
        
        for url in to_remove:
            del download_progress[url]
            
        time.sleep(300)  # Clean up every 5 minutes

# Start the cleanup thread
threading.Thread(target=cleanup_progress_data, daemon=True).start()

# ✅ Custom progress hook for yt-dlp
def progress_hook(d):
    if d['status'] == 'downloading':
        url = d.get('info_dict', {}).get('webpage_url', 'unknown')
        
        # Calculate progress percentage
        total_bytes = d.get('total_bytes')
        downloaded_bytes = d.get('downloaded_bytes', 0)
        
        if not total_bytes:
            total_bytes = d.get('total_bytes_estimate', 0)
            
        if total_bytes > 0:
            percent = (downloaded_bytes / total_bytes) * 100
        else:
            # If we can't determine total size, use a placeholder
            percent = min(95, download_progress[url]["progress"] + 1)
            
        # Update progress tracker
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
    time.sleep(60)  # Wait 1 minutes before deleting
    try:
        os.remove(filepath)
        print(f"✅ Deleted file: {filepath}")
    except Exception as e:
        print(f"⚠️ Error deleting file: {e}")

# ✅ Serve `index.html` (Frontend UI)
@app.route("/")
def home():
    return send_from_directory(FRONTEND_FOLDER, "index.html")

# ✅ Serve static files (`style.css`, `script.js`)
@app.route("/<path:filename>")
def serve_static_files(filename):
    return send_from_directory(FRONTEND_FOLDER, filename), 200

# ✅ Serve downloaded files properly
@app.route("/downloads/<filename>")
def serve_download(filename):
    file_path = os.path.join(DOWNLOADS_FOLDER, filename)

    # ✅ Debugging logs
    print(f"📂 Expected file path: {file_path}")
    print(f"📂 Actual files in folder: {os.listdir(DOWNLOADS_FOLDER)}")

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
        
    # Get progress data for this URL
    progress_data = download_progress.get(video_url, {"progress": 0})
    
    return jsonify({
        "progress": progress_data.get("progress", 0),
        "downloaded_bytes": progress_data.get("downloaded_bytes", 0),
        "total_bytes": progress_data.get("total_bytes", 0),
        "speed": progress_data.get("speed", 0),
        "eta": progress_data.get("eta", 0)
    })

# ✅ Function to find the closest available MP3 bitrate
def find_best_audio_format(video_url, preferred_quality):
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(video_url, download=False)
        available_formats = [
            fmt["format_id"] for fmt in info["formats"] if "audio" in fmt.get("format_note", "").lower()
        ]
    
    # ✅ Match requested quality OR find closest available
    bitrate_map = {"128k": "140", "192k": "251", "320k": "256"}  # Common yt-dlp audio IDs
    if preferred_quality in bitrate_map and bitrate_map[preferred_quality] in available_formats:
        return bitrate_map[preferred_quality]
    return available_formats[-1] if available_formats else "bestaudio/best"

# ✅ Download video route (Handles MP3 & MP4 downloads, and playlists)
@app.route('/download', methods=['POST'])
def download_video():
    data = request.json
    video_url = data.get("url")
    format_type = data.get("format")  # "mp3" or "mp4"
    quality = data.get("quality")  # Selected quality

    if not video_url:
        return jsonify({"error": "No URL provided"}), 400

    # Reset progress for this URL
    download_progress[video_url] = {"progress": 0, "timestamp": time.time()}
    
    # ✅ Add resolution/bitrate to filename (Prevents overwriting)
    output_template = os.path.join(DOWNLOADS_FOLDER, "%(title)s_" + quality + ".%(ext)s")

    # ✅ Improved yt-dlp Download Options
    if format_type == "mp3":
        best_audio_format = find_best_audio_format(video_url, quality)
        options = {
            "format": best_audio_format,
            "outtmpl": output_template,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality
            }],
            "noplaylist": False,
            "retries": 5,
            "socket_timeout": 30,
            "progress_hooks": [progress_hook],  # Add progress hook
        }
    else:  # MP4 Video
        video_quality = f"bestvideo[height<={quality}]+bestaudio/best/best"
        options = {
            "format": video_quality,
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "noplaylist": False,
            "retries": 5,
            "socket_timeout": 30,
            "progress_hooks": [progress_hook],  # Add progress hook
        }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=True)

            # Set progress to 100% when download completes
            download_progress[video_url]["progress"] = 100

            if "entries" in info:  # ✅ Handle Playlists
                downloaded_files = []
                for entry in info["entries"]:
                    if not entry:
                        continue
                    filename = ydl.prepare_filename(entry)
                    filepath = os.path.join(DOWNLOADS_FOLDER, os.path.basename(filename))

                    if os.path.exists(filepath):
                        downloaded_files.append(os.path.basename(filepath))

                if not downloaded_files:
                    return jsonify({"error": "No valid files found!"}), 500

                return jsonify({"message": "Playlist downloaded!", "files": downloaded_files})

            else:  # ✅ Single Video Download
                filename = ydl.prepare_filename(info)
                filepath = os.path.join(DOWNLOADS_FOLDER, os.path.basename(filename))

                # ✅ Fix MP3 File Not Found Issue
                if format_type == "mp3":
                    mp3_filepath = filepath.replace(".webm", ".mp3").replace(".m4a", ".mp3")
                    if os.path.exists(mp3_filepath):
                        filepath = mp3_filepath

                # ✅ Debugging logs
                print(f"📂 Expected file path: {filepath}")
                print(f"📂 Actual files in folder: {os.listdir(DOWNLOADS_FOLDER)}")

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