# coded by Mr. Ankush
# tg: @Coder_ankushBot



import uvloop
uvloop.install()
import asyncio
import subprocess
import os
import time
import re
from pyrogram import Client, filters
import config as c
import tempfile
import uuid
import shutil
from pathlib import Path

active_downloads = {}

ank = Client(
    "pbot",
    api_id=c.API_ID,
    api_hash=c.API_HASH,
    bot_token=c.BOT_TOKEN,
    workers=10,
    sleep_threshold=5,
    max_concurrent_transmissions=1000
)


@ank.on_message(filters.command("start"))
async def start(client, message):
    user_mention = message.from_user.mention
    await message.reply_text(
        f"**Hello {user_mention},\n\n"
        "I am a powerful video downloader bot.\n**"
    )

@ank.on_message(filters.text & filters.private)
async def process_link(client, message):
    video_url = message.text.strip()
    if not re.match(r'(https?://\S+)', video_url):
        await message.reply_text("Please send a valid URL.")
        return
    status_message = await message.reply_text(
        f"**🔍 Processing your link...**",
        quote=True
    )
    await process_video_download(client, status_message, video_url)

def get_temp_dir(user_id=None):
    session_id = f"{user_id}_{uuid.uuid4()}" if user_id else str(uuid.uuid4())
    temp_dir = os.path.join(tempfile.gettempdir(), f"ytdlp_bot_{session_id}")
    os.makedirs(temp_dir, exist_ok=True)
    
    c.logger.info(f"Created temp directory: {temp_dir}")
    return temp_dir

async def process_video_download(client, message, video_url, format_id="best"):
    thumbnail_path = None
    video_path = None
    temp_dir = None
    download_id = None
    
    try:
        user_id = message.chat.id if hasattr(message, 'chat') else None
        allowed, error_message = await check_user_limits(user_id)
        if not allowed:
            await message.edit_text(error_message)
            return
        await message.edit_text("**Your Task Added for Downloading...**")
        video_info = await get_video_info(video_url)
        if not video_info:
            await message.edit_text("❌ **Failed to get video information. Please check the URL and try again.**")
            return
        title = video_info
        video_path = await download_video(video_url, title, format_id, message)
        if not video_path:
            await message.edit_text("❌ **Download failed. Please try again later.**")
            return
        
        if 'download_id' in locals() and download_id in active_downloads:
            temp_dir = active_downloads[download_id]['temp_dir']
        else:
            temp_dir = get_temp_dir(user_id)
        
        timestamp = int(time.time())
        thumbnail_path = os.path.join(temp_dir, f"{timestamp}.jpg")
        
        c.logger.info(f"Attempting to extract thumbnail from video: {video_path}")
        thumbnail_success = await extract_thumbnail(video_path, thumbnail_path)
            
        if thumbnail_success and os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            c.logger.info(f"Successfully created thumbnail at: {thumbnail_path}")
        else:
            c.logger.warning("Could not generate valid thumbnail, proceeding without it")
            thumbnail_path = None
        await message.edit_text(
            f"📤 **Uploading to Telegram...**\n\n"
            f"**Title:** {title}\n"
        )
        await send_video(client, message, video_path, title, thumbnail_path)
        
    except Exception as e:
        c.logger.error(f"Error processing video: {str(e)}")
        await message.edit_text(f"❌ **An error occurred: {str(e)}**")
    finally:
        try:
            if 'download_id' in locals() and download_id in active_downloads:
                del active_downloads[download_id]
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                c.logger.info(f"Removed temporary directory: {temp_dir}")
        except Exception as e:
            c.logger.error(f"Error during cleanup: {str(e)}")
        asyncio.create_task(cleanup_old_temp_dirs())

async def download_video(video_url, title_str, format_id="best", message=None, user_id=None):
    try:
        temp_dir = get_temp_dir(user_id)
        download_id = str(uuid.uuid4())
        active_downloads[download_id] = {
            'user_id': user_id,
            'url': video_url,
            'start_time': time.time(),
            'temp_dir': temp_dir,
            'status': 'starting'
        }
        safe_title = "".join([c for c in title_str if c.isalnum() or c in " ._-"]).strip()
        if not safe_title:
            safe_title = f"Video_{int(time.time())}"
        timestamp = int(time.time())
        unique_filename = f"{timestamp}_download.mp4"
        output_path = os.path.join(temp_dir, unique_filename)
        info_cmd = [
            "yt-dlp",
            "--print", "filesize_approx",
            "--no-download",
            "-f", format_id,
            video_url
        ]
        
        try:
            info_process = await asyncio.create_subprocess_exec(
                *info_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await info_process.communicate()
            
            estimated_size = 0
            if info_process.returncode == 0:
                size_str = stdout.decode().strip()
                if size_str and size_str.isdigit():
                    estimated_size = int(size_str)
        except Exception as e:
            c.logger.warning(f"Failed to get file size estimate: {str(e)}")
            estimated_size = 0
        cmd = [
            "yt-dlp", 
            video_url, 
            "-f", "b" if format_id == "best" else format_id,
            "-o", output_path,
            "-R", "5",
            "--fragment-retries", "3",
            "--concurrent-fragments", "25",
            "--external-downloader", "aria2c",
            "--external-downloader-args", "aria2c:-x 16 -j 32 -s 16 --continue=true --allow-overwrite=true",
            "--merge-output-format", "mp4",
            "--no-post-overwrites",
        ]
        download = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        if hasattr(progress_callback, "start_time"):
            delattr(progress_callback, "start_time")
        download_complete = False
        last_update_time = 0
        while not download_complete:
            if download.returncode is not None:
                download_complete = True
                continue
            if os.path.exists(output_path):
                current_size = os.path.getsize(output_path)
                if message and (time.time() - last_update_time) > 2:
                    if estimated_size > 0:
                        await progress_callback(current_size, estimated_size, message, "Downloading")
                    else:
                        placeholder_size = max(current_size * 2, 10000000)
                        await progress_callback(current_size, placeholder_size, message, "Downloading")
                    last_update_time = time.time()
            await asyncio.sleep(1)
        stdout, stderr = await download.communicate()
        
        if download.returncode != 0:
            c.logger.error(f"Error downloading video: {stderr.decode()}")
            if download_id in active_downloads:
                active_downloads[download_id]['status'] = 'failed'
            return None
        if os.path.exists(output_path):
            c.logger.info(f"Video downloaded successfully: {output_path}")
            if download_id in active_downloads:
                active_downloads[download_id]['status'] = 'completed'
                active_downloads[download_id]['file_path'] = output_path
            return output_path
        temp_dir = active_downloads[download_id]['temp_dir'] if download_id in active_downloads else temp_dir
        for file in os.listdir(temp_dir):
            if file.endswith(".mp4"):
                video_path = os.path.join(temp_dir, file)
                if os.path.exists(video_path):
                    c.logger.info(f"Video downloaded with different name: {file}")
                    if download_id in active_downloads:
                        active_downloads[download_id]['status'] = 'completed'
                        active_downloads[download_id]['file_path'] = video_path
                    return video_path
        if download_id in active_downloads:
            active_downloads[download_id]['status'] = 'failed'
        return None
    except Exception as e:
        c.logger.error(f"Exception in download_video: {str(e)}")
        if 'download_id' in locals() and download_id in active_downloads:
            active_downloads[download_id]['status'] = 'failed'
            active_downloads[download_id]['error'] = str(e)
        return None
    finally:
        pass


async def get_video_info(video_url):
    try:
        command = [
            "yt-dlp",
            "--get-title",
            "--get-format",
            video_url
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        current_time = int(time.time())
        default_title = f"Video_{current_time}"
        if process.returncode == 0:
            info = stdout.decode().splitlines()
            title = info[0] if len(info) > 0 and info[0] else default_title
        else:
            c.logger.warning(f"Could not get video info, using defaults. Error: {stderr.decode()}")
            title = default_title
        return (title)
    except Exception as e:
        c.logger.error(f"Exception in get_video_info: {str(e)}")
        current_time = int(time.time())
        return (f"Video_{current_time}")
async def extract_thumbnail(video_path, thumbnail_path):
    try:
        thumbnail_dir = os.path.dirname(thumbnail_path)
        if not os.path.exists(thumbnail_dir):
            os.makedirs(thumbnail_dir, exist_ok=True)
        c.logger.info(f"Extracting thumbnail from video: {video_path}")
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-ss", "00:00:01.000",
            "-vframes", "1",
            "-loglevel", "quiet",
            "-y",
            thumbnail_path
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            stderr_text = stderr.decode() if stderr else "No error output"
            c.logger.error(f"Error extracting thumbnail: {stderr_text}")
            return False
        if os.path.exists(thumbnail_path) and os.path.getsize(thumbnail_path) > 0:
            c.logger.info(f"Thumbnail extraction successful: {thumbnail_path}")
            return True
        else:
            c.logger.error(f"Thumbnail file missing or empty: {thumbnail_path}")
            return False
    except Exception as e:
        c.logger.error(f"Exception in extract_thumbnail: {str(e)}")
        return False


async def send_video(client, message, video_path, title, thumb_path=None):
    temp_files = []
    try:
        duration, width, height = await get_video_metadata(video_path)
        if os.path.getsize(video_path) > 2000 * 1024 * 1024:  
            c.logger.info(f"Video size exceeds 2GB limit, compressing: {video_path}")
            compressed_path = await compress_video(video_path)
            if compressed_path:
                video_path = compressed_path
                temp_files.append(compressed_path)
        if thumb_path and not os.path.exists(thumb_path):
            c.logger.warning(f"Thumbnail file does not exist: {thumb_path}")
            thumb_path = None
        elif not thumb_path: #Coded by Mr. Ankush Tg: @Coder_ankushBot
            try:
                temp_dir = os.path.dirname(video_path)
                auto_thumb_path = os.path.join(temp_dir, f"auto_thumb_{uuid.uuid4()}.jpg")
                thumb_success = await extract_thumbnail(video_path, auto_thumb_path)
                if thumb_success:
                    c.logger.info(f"Auto-generated thumbnail: {auto_thumb_path}")
                    thumb_path = auto_thumb_path
                    temp_files.append(auto_thumb_path)  
            except Exception as e:
                c.logger.error(f"Error generating auto-thumbnail: {str(e)}")
        video_params = {
            "chat_id": message.chat.id,
            "video": video_path,
            "caption": f"**📹 {title}**\n\n"
                     f"**🕒 Duration:** {format_duration(duration)}\n"
                     f"**📏 Resolution:** {width}x{height}",
            "supports_streaming": True,
            "progress": progress_callback,
            "progress_args": (message, "Uploading")
        }
        if duration:
            video_params["duration"] = int(duration)
        if width and height:
            video_params["width"] = width
            video_params["height"] = height
        if thumb_path and os.path.exists(thumb_path):
            video_params["thumb"] = thumb_path
        await client.send_video(**video_params)
        await message.edit_text("✅ **Download completed successfully!**")
    except Exception as e:
        c.logger.error(f"Error sending video: {str(e)}")
        await message.edit_text(f"❌ **Error sending video: {str(e)}**")
        if os.path.exists(video_path):
            try:
                os.remove(video_path)
                c.logger.info(f"Removed video file: {video_path}")
            except Exception as e:
                c.logger.error(f"Error removing video file: {str(e)}")
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
                c.logger.info(f"Removed thumbnail file: {thumb_path}")
            except Exception as e:
                c.logger.error(f"Error removing thumbnail file: {str(e)}")
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    c.logger.info(f"Removed temporary file: {temp_file}")
            except Exception as e:
                c.logger.error(f"Error removing temporary file: {str(e)}")
    finally:
        pass

async def get_video_metadata(video_path):
    try:
        command = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "csv=s=,:p=0",
            video_path
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            c.logger.error(f"Error getting video metadata: {stderr.decode()}")
            return None, None, None
        data = stdout.decode().strip().split(',')
        if len(data) >= 3:
            width = int(data[0])
            height = int(data[1])
            duration = float(data[2])
            return duration, width, height
        return None, None, None
    except Exception as e:
        c.logger.error(f"Exception in get_video_metadata: {str(e)}")
        return None, None, None
    

async def compress_video(video_path):
    temp_dir = os.path.dirname(video_path)
    basename = os.path.basename(video_path)
    unique_id = str(uuid.uuid4())[:8]
    output_path = os.path.join(temp_dir, f"{os.path.splitext(basename)[0]}_{unique_id}_compressed.mp4")
    try:
        target_size_mb = 1998
        original_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        c.logger.info(f"Compressing video: {basename} ({original_size_mb:.2f} MB → {target_size_mb} MB)")
        
        duration, _, _ = await get_video_metadata(video_path)
        if duration and duration > 0:
            target_video_bitrate_kbps = int((target_size_mb * 8 * 1024 * 0.9) / duration)
            target_audio_bitrate_kbps = min(192, int((target_size_mb * 8 * 1024 * 0.1) / duration))
            
            target_video_bitrate_kbps = max(500, min(target_video_bitrate_kbps, 5000))
            target_audio_bitrate_kbps = max(64, target_audio_bitrate_kbps)
            
            c.logger.info(f"Compression settings: Video: {target_video_bitrate_kbps}kbps, Audio: {target_audio_bitrate_kbps}kbps")
        else:
            target_video_bitrate_kbps = 1000
            target_audio_bitrate_kbps = 128
        command = [
            "ffmpeg",
            "-i", video_path,
            "-c:v", "libx264",
            "-b:v", f"{target_video_bitrate_kbps}k",
            "-maxrate", f"{int(target_video_bitrate_kbps * 1.5)}k",
            "-bufsize", f"{target_video_bitrate_kbps * 2}k",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-b:a", f"{target_audio_bitrate_kbps}k",
            "-y",
            output_path
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            stderr_text = stderr.decode() if stderr else "No error output"
            c.logger.error(f"Error compressing video: {stderr_text}")
            return None
        if os.path.exists(output_path):
            new_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            c.logger.info(f"Compression successful: {original_size_mb:.2f} MB → {new_size_mb:.2f} MB")
            
            if new_size_mb >= original_size_mb:
                c.logger.warning(f"Compressed file is larger than original, using original file")
                os.remove(output_path)
                return video_path
            return output_path
        else:
            c.logger.error(f"Compressed file not found at {output_path}")
            return None
    except Exception as e:
        c.logger.error(f"Exception in compress_video: {str(e)}")
        return None


def format_duration(duration_str):
    try:
        seconds = int(float(duration_str))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02}:{minutes:02}:{seconds:02}"
        else:
            return f"{minutes:02}:{seconds:02}"
    except:
        return duration_str

def format_size(size_in_bytes):
    if not size_in_bytes:
        return "0B"
    power = 2**10
    n = 0
    power_labels = {0: "", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}
    
    while size_in_bytes > power:
        size_in_bytes /= power
        n += 1
    return f"{size_in_bytes:.2f} {power_labels[n]}"

async def progress_callback(current, total, message, status="Uploading"):
    try:
        if not total or total == 0:
            return
        now = time.time()
        if not hasattr(progress_callback, "start_time"):
            progress_callback.start_time = now
            progress_callback.last_update_time = now - 3  
            progress_callback.last_percentage = 0
        if (now - progress_callback.last_update_time) < 2 and current != total:
            return
        percentage = current * 100 / total
        if (percentage - progress_callback.last_percentage < 5) and percentage != 100 and current != total:
            return
        progress_callback.last_update_time = now
        progress_callback.last_percentage = percentage
        elapsed_time = now - progress_callback.start_time
        speed = current / elapsed_time if elapsed_time > 0 else 0
        eta = (total - current) / speed if speed > 0 else 0
        bar_length = 10
        filled_length = int(percentage / 10)
        bar = "█" * filled_length + "▒" * (bar_length - filled_length)
        try:
            await message.edit_text(
                f"**╔════❰ {status} ❱════❍⊱❁۪۪*\n**"
                f"**║╭━━━━━━━━━━━━━━➣\n**"
                f"**║┣⪼🤖Bᴏᴛ ʙʏ: [𝕄𝕤ᴡ ℙʀᴇ𝕤ᴇɴᴛ𝕤™](https://t.me/mswpresent)\n**"
                f"**║┣⪼⚡[{bar}] {percentage:.2f}%\n**"
                f"**║┣⪼📟ᴅᴏɴᴇ: `{format_size(current)}` ᴏғ `{format_size(total)}`\n**"
                f"**║┣⪼🚀sᴘᴇᴇᴅ: {format_size(speed)}/s\n**"
                f"**║┣⪼⏱ᴇᴛᴀ: {format_duration(eta)}\n**"
                f"**║╰━━━━━━━━━━━━━━➣\n**"
                f"**╚════════════════════❍⊱❁۪۪*  **",
                disable_web_page_preview=True
            )
        except Exception as e:
            c.logger.warning(f"Progress update error: {e}")
            await asyncio.sleep(5)
    except Exception as e:
        c.logger.error(f"Progress callback error: {str(e)}")


async def cleanup_old_temp_dirs(max_age_minutes=30):
    try:
        temp_root = tempfile.gettempdir()
        current_time = time.time()
        pattern = "ytdlp_bot_*"
        for temp_path in Path(temp_root).glob(pattern):
            try:
                dir_stat = os.stat(temp_path)
                dir_age_minutes = (current_time - dir_stat.st_mtime) / 60
                if dir_age_minutes > max_age_minutes:
                    c.logger.info(f"Removing old temp directory: {temp_path} (age: {dir_age_minutes:.1f} minutes)")
                    shutil.rmtree(temp_path, ignore_errors=True)
            except Exception as e:
                c.logger.error(f"Error checking/removing temp directory {temp_path}: {str(e)}")
    except Exception as e:
        c.logger.error(f"Error in cleanup_old_temp_dirs: {str(e)}")

async def periodic_cleanup():
    while True:
        try:
            await cleanup_old_temp_dirs(max_age_minutes=30)
            await cleanup_stale_downloads()
            await asyncio.sleep(15 * 60)
        except Exception as e:
            c.logger.error(f"Error in periodic cleanup: {str(e)}")
            await asyncio.sleep(60)
async def cleanup_stale_downloads(max_age_minutes=60):
    current_time = time.time()
    to_remove = []
    for download_id, download_info in active_downloads.items():
        if 'start_time' in download_info:
            age_minutes = (current_time - download_info['start_time']) / 60
            if age_minutes > max_age_minutes:
                c.logger.warning(f"Removing stale download {download_id} (age: {age_minutes:.1f} minutes)")
                to_remove.append(download_id)
                if 'temp_dir' in download_info and os.path.exists(download_info['temp_dir']):
                    try:
                        shutil.rmtree(download_info['temp_dir'], ignore_errors=True)
                        c.logger.info(f"Removed stale temp directory: {download_info['temp_dir']}")
                    except Exception as e:
                        c.logger.error(f"Error removing stale temp dir: {str(e)}")
    for download_id in to_remove:
        try:
            del active_downloads[download_id]
        except KeyError:
            pass


async def check_user_limits(user_id):
    MAX_CONCURRENT_DOWNLOADS = 3
    active_count = 0
    for download_info in active_downloads.values():
        if download_info.get('user_id') == user_id and download_info.get('status') in ['starting', 'downloading']:
            active_count += 1
    if active_count >= MAX_CONCURRENT_DOWNLOADS:
        return False, f"⚠️ You have reached the maximum limit of {MAX_CONCURRENT_DOWNLOADS} concurrent downloads. Please wait for your current downloads to finish."
    return True, None
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(periodic_cleanup())
    ank.run()