import base64
import io
import configparser
import asyncio
import os
import imghdr

from pagermaid.enums import Message
from pagermaid.listener import listener
from pagermaid.services import client as requests
from pagermaid.utils import pip_install
from pyrogram.enums import MessageEntityType
from uuid import uuid4

try:
    import boto3
except ImportError:
    pip_install("boto3")
    import boto3

try:
    import cv2
except ImportError:
    pip_install("opencv-python")
    import cv2

# --- 尝试多个可能的配置文件路径 ---
possible_paths = [
    'q.config',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'q.config'),
    '/app/q.config',
    '/root/pagermaid-pyro/plugins/q.config',
]

# --- 从配置文件加载配置 ---
config = configparser.ConfigParser()
config_read_error_message = None
found_config_path = None

for path in possible_paths:
    try:
        if os.path.exists(path):
            config.read(path)
            found_config_path = path
            break
    except Exception:
        pass  # 静默处理读取失败，直到所有路径都尝试过

if not found_config_path:
    config_read_error_message = "❌ 插件启动错误: 未能找到并读取q.config文件。\n请确保文件存在于以下任一路径且可读: " + ", ".join(
        possible_paths)

# API 配置
TEXT_QUOTE_API_URL = config.get('API', 'quote_api_url', fallback="https://quote.git.llc/generate")

# S3 配置
S3_CONFIG = {
    "bucket_name": config.get('S3', 'bucket_name', fallback=""),
    "public_url": config.get('S3', 'public_url', fallback=""),
    "access_key": config.get('S3', 'access_key', fallback=""),
    "secret_key": config.get('S3', 'secret_key', fallback=""),
    "endpoint_url": config.get('S3', 'endpoint_url', fallback=""),
    "region": config.get('S3', 'region', fallback="auto"),
}

# Quote 默认设置
QUOTE_SETTINGS = {
    "background_color": config.get('QUOTE', 'background_color', fallback="#1b1429"),
    "width": config.getint('QUOTE', 'width', fallback=512),
    "height": config.getint('QUOTE', 'height', fallback=768),
    "scale": config.getint('QUOTE', 'scale', fallback=2),
    "emoji_brand": config.get('QUOTE', 'emoji_brand', fallback="apple"),
    "format": config.get('QUOTE', 'format', fallback="webp"),
}

# Media 媒体处理设置
MEDIA_SETTINGS = {
    "max_file_size": config.getint('MEDIA', 'max_file_size', fallback=10485760),
    # 移除 gif 和 mp4（WebM 通常是 mp4 容器或单独 WebM），因为我们将提取其第一帧作为图像
    "supported_formats": [f.strip() for f in
                          config.get('MEDIA', 'supported_formats', fallback="jpg,jpeg,png,webp").split(',')],
    "enable_compression": config.getboolean('MEDIA', 'enable_compression', fallback=True),
}

s3_client_instance = None


async def init_s3_client(message_obj: Message):
    global s3_client_instance
    if s3_client_instance:
        return s3_client_instance

    if not (S3_CONFIG.get("access_key") and S3_CONFIG.get("secret_key") and S3_CONFIG.get(
            "endpoint_url") and S3_CONFIG.get("bucket_name")):
        await message_obj.edit("❌ S3配置不完整，无法初始化R2客户端。请检查q.config文件中[S3]部分所有必需项。")
        await asyncio.sleep(5)
        return None

    try:
        s3_client_instance = boto3.client(
            's3',
            aws_access_key_id=S3_CONFIG["access_key"],
            aws_secret_access_key=S3_CONFIG["secret_key"],
            endpoint_url=S3_CONFIG["endpoint_url"],
            region_name=S3_CONFIG["region"]
        )
        s3_client_instance.list_objects_v2(Bucket=S3_CONFIG["bucket_name"], MaxKeys=1)
        # 成功初始化后不再编辑消息，只在开始时有“开始生成语录...”
        return s3_client_instance
    except Exception as e:
        error_detail = str(e)
        if "SignatureDoesNotMatch" in error_detail:
            error_detail = "凭证错误或权限不足 (SignatureDoesNotMatch)"
        elif "InvalidAccessKeyId" in error_detail:
            error_detail = "Access Key ID错误"
        elif "NoSuchBucket" in error_detail:
            error_detail = "存储桶名称错误或不存在"
        elif "ConnectTimeout" in error_detail or "Failed to connect" in error_detail:
            error_detail = "无法连接到R2端点，请检查网络或endpoint_url"

        await message_obj.edit(f"❌ R2客户端初始化或连接失败: {error_detail}\n请检查q.config文件中的S3配置。")
        await asyncio.sleep(10)
        s3_client_instance = None
        return None


def detect_image_format(data_bytes: bytes) -> str:
    img_type = imghdr.what(None, h=data_bytes)

    if img_type == 'jpeg':
        return 'jpg'
    elif img_type == 'png':
        return 'png'
    elif img_type == 'gif':
        return 'gif'
    elif img_type == 'webp':
        if data_bytes.startswith(b'RIFF') and data_bytes[8:12] == b'WEBP':
            return 'webp'
    # 对于无法直接通过 imghdr 识别的，尝试根据已知文件头判断或返回 unknown
    if data_bytes.startswith(b'\x1A\x45\xDF\xA3'):  # WEBM/MKV magic number
        return 'webm'
    return img_type if img_type else 'unknown'


async def extract_first_frame(video_data_io: io.BytesIO, message_obj: Message) -> io.BytesIO | None:
    """
    使用 OpenCV 提取视频（GIF, MP4, WebM等）的第一帧。
    返回一个包含 JPEG 图像数据的 BytesIO 对象。
    """
    temp_file_path = f"/tmp/{uuid4()}"
    try:
        video_data_io.seek(0)
        with open(temp_file_path, 'wb') as f:
            f.write(video_data_io.getvalue())

        cap = cv2.VideoCapture(temp_file_path)
        if not cap.isOpened():
            await message_obj.edit("❌ 无法打开视频文件以提取第一帧。")
            await asyncio.sleep(5)
            return None

        ret, frame = cap.read()
        cap.release()
        os.remove(temp_file_path)  # 清理临时文件

        if not ret:
            await message_obj.edit("❌ 无法读取视频的第一帧。")
            await asyncio.sleep(5)
            return None

        # 将帧编码为 JPEG 格式
        is_success, buffer = cv2.imencode(".jpg", frame)
        if not is_success:
            await message_obj.edit("❌ 无法将第一帧编码为 JPEG。")
            await asyncio.sleep(5)
            return None

        img_io = io.BytesIO(buffer.tobytes())
        img_io.name = "first_frame.jpg"
        return img_io

    except Exception as e:
        await message_obj.edit(f"❌ 提取视频第一帧失败: {str(e)}")
        await asyncio.sleep(10)
        return None
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)


async def upload_to_s3(media_data: io.BytesIO, media_type: str, format_type: str, message_obj: Message) -> tuple[
                                                                                                               str, str] | \
                                                                                                           tuple[
                                                                                                               None, None]:
    if not s3_client_instance:
        await message_obj.edit("❌ S3客户端未就绪，无法上传文件。")
        await asyncio.sleep(5)
        return None, None

    if isinstance(media_data, io.BytesIO):
        media_data.seek(0)
        file_content = media_data.getvalue()
    elif isinstance(media_data, bytes):
        file_content = media_data
    else:
        await message_obj.edit(f"❌ 不支持的媒体数据类型: {type(media_data)}")
        await asyncio.sleep(5)
        return None, None

    # 对于通过 extract_first_frame 提取的图像，统一上传为 jpg
    if media_type == "extracted_frame":
        object_name = f"frame_{uuid4()}.jpg"
        mime_type_header = "image/jpeg"
    else:
        object_name = f"{media_type}_{uuid4()}.{format_type}"
        content_type_map = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp",
        }
        mime_type_header = content_type_map.get(format_type.lower(), "application/octet-stream")

    try:
        s3_client_instance.put_object(
            Bucket=S3_CONFIG["bucket_name"],
            Key=object_name,
            Body=file_content,
            ContentType=mime_type_header
        )
        public_file_url = f"{S3_CONFIG['public_url']}/{object_name}"
        return public_file_url, object_name
    except Exception as e:
        error_detail = str(e)
        if "Access Denied" in error_detail:
            error_detail = "R2存储桶权限不足 (Access Denied)"
        await message_obj.edit(f"❌ 上传文件 '{object_name[:8]}...' 到R2失败: {error_detail}\n请检查R2权限。")
        await asyncio.sleep(10)
        return None, None


async def delete_s3_file(s3_key: str):
    if not s3_client_instance:
        # 无法输出到用户，静默失败
        return False

    try:
        s3_client_instance.delete_object(
            Bucket=S3_CONFIG["bucket_name"],
            Key=s3_key
        )
        return True

    except Exception:  # 清理失败不向用户报告，不阻塞主流程
        return False


async def get_message(client, chat_id, msg_id, message_obj: Message):
    try:
        return await client.get_messages(chat_id, msg_id)
    except Exception as e:
        await message_obj.edit(f"❌ 获取消息失败: {str(e)}\n请检查消息ID或权限。")
        await asyncio.sleep(5)
        return None


async def extract_message(msg, client=None, message_obj: Message = None):
    # --- 用户身份判断逻辑 ---
    user = None
    name = None
    user_id = None
    is_hidden_forward = False

    # 优先级 1: 转发自隐藏了身份的用户
    if msg.forward_sender_name:
        is_hidden_forward = True
        name = msg.forward_sender_name
        user_id = hash(name)  # 使用名字的哈希作为唯一标识
    # 优先级 2: 转发自公开身份的用户
    elif msg.forward_from:
        user = msg.forward_from
    # 优先级 3: 转发自频道
    elif msg.forward_from_chat:
        user = msg.forward_from_chat
    # 优先级 4: 普通消息
    else:
        user = msg.from_user or msg.sender_chat

    if user:
        user_id = user.id
        if getattr(user, "is_deleted", False):
            name = "已删除账户"
        else:
            # 优先用 title (用于频道), 否则拼接 first_name 和 last_name
            name = getattr(user, "title", None) or " ".join(
                filter(None, [getattr(user, "first_name", None), getattr(user, "last_name", None)])) or "未知"

    if not name:
        return None  # 如果最终无法确定发送者姓名，则跳过

    avatar_base64 = None
    # 隐藏身份的转发、已删除账户或没有头像的用户，不处理头像
    if user and not is_hidden_forward and not getattr(user, "is_deleted", False) and getattr(user, "photo", None):
        try:
            avatar = await client.download_media(user.photo.big_file_id, in_memory=True)
            avatar_base64 = base64.b64encode(avatar.getvalue()).decode()
        except Exception:
            # 静默失败，避免因头像下载失败导致整个插件崩溃
            pass

    text = msg.text or msg.caption or ""
    has_content = (text or msg.photo or msg.sticker or
                   (msg.document and getattr(msg.document, 'mime_type', '').startswith('image/')) or
                   msg.animation)

    if not has_content:
        return None

    data = {
        "from": {
            "id": user_id,
            "name": name,
            "username": getattr(user, "username", "") if user else "",
            "emoji_status": str(getattr(getattr(user, "emoji_status", None), "custom_emoji_id", "")) if user else None,
            "photo": {"base64": avatar_base64} if avatar_base64 else {"base64": None},
        },
        # --- 核心修正 ---
        # 始终设为 True，让API去决定是显示真头像还是占位符。
        # 对于不应该显示头像的情况（如连续消息），由 quotly_handler 稍后覆盖此值为 False。
        "avatar": True,
        "text": text,
    }

    if msg.entities:
        data["entities"] = [
            {
                "type": (e.type.name.lower() if hasattr(e.type, "name") else str(e.type)),
                "offset": e.offset,
                "length": e.length,
                **({"custom_emoji_id": str(e.custom_emoji_id)} if getattr(e, "type",
                                                                          None) == MessageEntityType.CUSTOM_EMOJI else {})
            }
            for e in msg.entities
        ]

    if client and (msg.photo or msg.sticker or msg.animation or (
            msg.document and getattr(msg.document, 'mime_type', '').startswith('image/'))):
        try:
            media_item = msg.photo or msg.sticker or msg.animation or msg.document
            downloaded_media_io = await client.download_media(media_item, in_memory=True)
            downloaded_media_io.seek(0)

            file_size = len(downloaded_media_io.getvalue())
            if file_size > MEDIA_SETTINGS["max_file_size"]:
                await message_obj.edit(f"❌ 媒体文件过大: {file_size} > {MEDIA_SETTINGS['max_file_size']} 字节")
                await asyncio.sleep(10)
                data["text"] = "*媒体文件过大*"
                return data

            media_bytes_peek = downloaded_media_io.getvalue()[:2048]
            detected_format = detect_image_format(media_bytes_peek)

            processed_media_io = downloaded_media_io
            upload_media_type = "media"
            format_type = detected_format

            # 如果是动画（GIF, WebM等），提取第一帧
            if detected_format in ["gif", "webm"] or (msg.animation):
                await message_obj.edit("正在提取媒体第一帧...")
                first_frame_io = await extract_first_frame(downloaded_media_io, message_obj)
                if first_frame_io:
                    processed_media_io = first_frame_io
                    format_type = "jpg"
                    upload_media_type = "extracted_frame"
                    await message_obj.edit("第一帧提取成功，准备上传...")
                else:
                    await message_obj.edit("❌ 提取第一帧失败，跳过媒体处理。")
                    data["text"] = "*提取第一帧失败*"
                    return data

            if format_type.lower() in MEDIA_SETTINGS['supported_formats'] or upload_media_type == "extracted_frame":
                if S3_CONFIG.get("bucket_name") and S3_CONFIG.get("access_key"):
                    image_url, s3_key_for_cleanup = await upload_to_s3(processed_media_io, upload_media_type,
                                                                       format_type, message_obj)
                    if image_url:
                        data["media"] = {
                            "url": image_url,
                            "type": "image",
                            "s3_key": s3_key_for_cleanup
                        }
                    else:
                        data["text"] = "*S3上传失败*"
                else:
                    data["text"] = "*S3未配置*"
            else:
                data["text"] = f"*不支持的媒体格式: {format_type}*"

        except Exception as e:
            await message_obj.edit(f"❌ 媒体文件处理失败: {str(e)}")
            await asyncio.sleep(10)
            data["text"] = f"*媒体文件处理失败*"

    return data


@listener(command="q", description="语录生成 支持颜色参数 r启用回复 多条消息生成")
async def quotly_handler(message: Message):
    global config_read_error_message
    if config_read_error_message:
        await message.edit(config_read_error_message)
        await asyncio.sleep(10)
        return

    client = message._client
    chat_id = message.chat.id
    base_msg = message.reply_to_message
    process_msg = await message.edit("生成中...")  # 简化初始提示

    # 如果没有回复消息，直接返回提示
    if not base_msg:
        await process_msg.edit("❌ 请回复一条消息来生成语录。")
        await asyncio.sleep(5)
        return

    offset = 0
    background_color = QUOTE_SETTINGS["background_color"]
    enable_reply = False

    for param in message.parameter:
        p = param.lstrip("-")
        if p.isdigit():
            # 检查是否为自然数（正整数或零）
            if int(p) >= 0:
                offset = int(p)
            else:
                await process_msg.edit("❌ 参数错误: 'q' 后的数字必须是自然数 (0 或正整数)。")
                await asyncio.sleep(5)
                return
        elif param.lower() in ("r", "回复"):
            enable_reply = True
        elif param.startswith("#") or param.isalpha():
            background_color = param

    base_id = base_msg.id
    ids = [base_id + i for i in (range(offset + 1, 1) if offset < 0 else range(0, offset + 1) if offset > 0 else [0])]

    messages_to_process = []
    for msg_id in ids:
        msg = await get_message(client, chat_id, msg_id, process_msg)
        if msg:
            messages_to_process.append(msg)

    if not messages_to_process:
        await process_msg.edit("❌ 未找到有效消息。")
        return

    global s3_client_instance
    s3_client_instance = await init_s3_client(process_msg)
    if not s3_client_instance:
        return

    all_messages_data = []
    s3_keys_to_delete = []
    last_user_id = None

    for m in messages_to_process:
        data = await extract_message(m, client, process_msg)
        if not data:
            continue

        if data.get("media") and data["media"].get("s3_key"):
            s3_keys_to_delete.append(data["media"]["s3_key"])

        current_user_id = data["from"]["id"]
        # 如果是同一用户连续发送多条消息，后续消息不显示头像和用户名
        if all_messages_data and current_user_id == last_user_id:
            data["avatar"] = False
            data["from"]["name"] = ""
            data["from"]["username"] = ""
            data["from"]["photo"] = {"base64": None}
        else:
            last_user_id = current_user_id

        if enable_reply and m.reply_to_message:
            reply_data = await extract_message(m.reply_to_message, client, process_msg)
            if reply_data:
                if reply_data.get("media") and reply_data["media"].get("s3_key"):
                    s3_keys_to_delete.append(reply_data["media"]["s3_key"])

                reply_name = reply_data["from"].get("name", "未知用户")
                reply_text = reply_data.get("text", "")

                if not reply_text and (
                        m.reply_to_message.photo or m.reply_to_message.video or m.reply_to_message.animation or m.reply_to_message.sticker or m.reply_to_message.audio or m.reply_to_message.voice or m.reply_to_message.document):
                    reply_text = "[媒体文件]"

                data["replyMessage"] = {
                    "name": reply_name,
                    "text": reply_text,
                    "entities": reply_data.get("entities", []),
                    "chatId": reply_data["from"]["id"],
                }
                if reply_data.get("media"):
                    data["replyMessage"]["media"] = {
                        "url": reply_data["media"]["url"],
                        "type": reply_data["media"]["type"]
                    }

        all_messages_data.append(data)

    if not all_messages_data:
        await process_msg.edit("❌ 未找到可生成语录的有效内容。")
        await asyncio.sleep(5)
        return

    payload = {
        "backgroundColor": background_color,
        "width": QUOTE_SETTINGS["width"],
        "height": QUOTE_SETTINGS["height"],
        "scale": QUOTE_SETTINGS["scale"],
        "emojiBrand": QUOTE_SETTINGS["emoji_brand"],
        "messages": all_messages_data,
        "format": QUOTE_SETTINGS["format"]
    }

    try:
        res = await requests.post(TEXT_QUOTE_API_URL, json=payload)
        res.raise_for_status()
        json_data = res.json()
        if not json_data.get("ok"):
            error_msg = json_data.get("error", "未知API错误")
            raise Exception(f"API返回失败: {error_msg}")
        img_bytes = base64.b64decode(json_data["result"]["image"])
        img_io = io.BytesIO(img_bytes)
        img_io.name = f"quote.{QUOTE_SETTINGS['format']}"
        img_io.seek(0)

        if QUOTE_SETTINGS['format'] == 'webp':
            await client.send_animation(chat_id, img_io)
        else:
            await client.send_document(chat_id, img_io)
        await process_msg.safe_delete()

        if s3_keys_to_delete:
            for s3_key in s3_keys_to_delete:
                await delete_s3_file(s3_key)

    except Exception as e:
        await process_msg.edit(f"❌ 语录生成失败：{str(e)}\n请检查Quote API服务状态或网络。")
        await asyncio.sleep(10)
