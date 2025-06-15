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
    user = msg.from_user or msg.sender_chat
    if not user:
        return None

    name = " ".join(filter(None, [getattr(user, "first_name", None), getattr(user, "last_name", None)])) or getattr(
        user, "title", None) or "未知"

    avatar_base64 = None
    if getattr(user, "photo", None):
        try:
            avatar = await client.download_media(user.photo.big_file_id, in_memory=True)
            avatar_base64 = base64.b64encode(avatar.read()).decode()
        except Exception:
            pass

    text = msg.text or msg.caption or ""

    s3_key_for_cleanup = None

    has_content = (
            msg.text or
            msg.caption or
            msg.photo or
            msg.sticker or
            (msg.document and getattr(msg.document, 'mime_type', '').startswith('image/')) or
            (msg.animation)  # 包含动画 (WebM/MP4)
    )

    if not has_content:
        return None

    if not text:
        if msg.photo or msg.sticker or \
                (msg.document and getattr(msg.document, 'mime_type', '').startswith('image/')) or \
                msg.animation:
            text = ""
        else:
            text = "[媒体]"

    data = {
        "from": {
            "id": user.id,
            "name": name,
            "username": getattr(user, "username", ""),
            "emoji_status": str(getattr(getattr(user, "emoji_status", None), "custom_emoji_id", "")) or None,
        },
        "avatar": True,
        "text": text,
    }

    if avatar_base64:
        data["from"]["photo"] = {"base64": avatar_base64}

    # --- 用户提供的代码片段，直接替换现有逻辑 ---
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
    # --- 代码片段结束 ---

    if client and (msg.photo or msg.sticker or msg.animation or (
            msg.document and getattr(msg.document, 'mime_type', '').startswith('image/'))):
        try:
            media_item = None
            media_type_str = None
            downloaded_media_io = None
            format_type = None

            if msg.photo:
                media_item = msg.photo
                media_type_str = "photo"
            elif msg.sticker:
                media_item = msg.sticker
                media_type_str = "sticker"
            elif msg.document and getattr(msg.document, 'mime_type', '').startswith('image/'):
                media_item = msg.document
                media_type_str = "document_image"
            elif msg.animation:  # 处理动画，如GIF或WebM
                media_item = msg.animation
                media_type_str = "animation"

            if media_item:
                downloaded_media_io = await client.download_media(media_item, in_memory=True)
                downloaded_media_io.seek(0)

                file_size = len(downloaded_media_io.getvalue())
                if file_size > MEDIA_SETTINGS["max_file_size"]:
                    await message_obj.edit(
                        f"❌ 媒体文件过大，大小: {file_size} 字节，最大限制: {MEDIA_SETTINGS['max_file_size']} 字节")
                    await asyncio.sleep(10)
                    data["text"] = "*媒体文件过大*"
                    return data

                media_bytes_peek = downloaded_media_io.getvalue()[:2048]
                detected_format = detect_image_format(media_bytes_peek)

                processed_media_io = downloaded_media_io  # 默认使用原始下载的io
                upload_media_type = media_type_str  # 默认上传类型

                # 如果是动画（GIF 或 WebM/MP4），提取第一帧
                if media_type_str == "animation" or detected_format == "gif" or detected_format == "webm":
                    await message_obj.edit("正在提取媒体第一帧...")
                    first_frame_io = await extract_first_frame(downloaded_media_io, message_obj)
                    if first_frame_io:
                        processed_media_io = first_frame_io
                        format_type = "jpg"  # 提取的第一帧总是JPEG
                        upload_media_type = "extracted_frame"  # 标记为提取的帧
                        await message_obj.edit("第一帧提取成功，准备上传...")
                    else:
                        await message_obj.edit("❌ 提取第一帧失败，跳过媒体处理。")
                        await asyncio.sleep(5)
                        data["text"] = "*提取第一帧失败*"
                        return data  # 提取失败则不继续处理媒体

                else:  # 对于非动画图像，直接使用检测到的格式
                    format_type = detected_format

                if format_type.lower() in MEDIA_SETTINGS['supported_formats'] or upload_media_type == "extracted_frame":
                    if S3_CONFIG.get("bucket_name") and S3_CONFIG.get("access_key"):
                        image_url, s3_key_for_cleanup = await upload_to_s3(processed_media_io, upload_media_type,
                                                                           format_type, message_obj)
                        if image_url:
                            data["media"] = {
                                "url": image_url,
                                "type": "image",  # 提取第一帧后，统一视为图片
                                "s3_key": s3_key_for_cleanup
                            }
                            data["text"] = ""
                        else:
                            await message_obj.edit("❌ S3上传失败，跳过媒体处理。")
                            await asyncio.sleep(5)
                            data["text"] = "*S3上传失败*"
                    else:
                        await message_obj.edit("❌ S3未配置或配置不完整，跳过媒体处理。")
                        await asyncio.sleep(5)
                        data["text"] = "*S3未配置*"
                else:
                    await message_obj.edit(f"❌ 不支持的媒体格式: {format_type}。")
                    await asyncio.sleep(5)
                    data["text"] = f"*不支持的媒体格式: {format_type}*"

        except Exception as e:
            await message_obj.edit(f"❌ 媒体文件处理失败: {str(e)}\n请检查文件或网络。")
            await asyncio.sleep(10)
            data["text"] = f"*媒体文件处理失败：{str(e)}*"

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
    base_msg = message.reply_to_message or message
    process_msg = await message.edit("生成中...")  # 简化初始提示

    offset = 0
    background_color = QUOTE_SETTINGS["background_color"]
    enable_reply = False

    for param in message.parameter:
        p = param.lstrip("-")
        if p.isdigit():
            offset = int(p)
        elif param.lower() in ("r", "回复"):
            enable_reply = True
        elif param.startswith("#") or param.isalpha():
            background_color = param

    base_id = base_msg.id
    # 修正多条消息获取逻辑，确保ids是正确的范围
    if offset < 0:
        ids = list(range(base_id + offset, base_id + 1))  # 从回复消息往前数
    elif offset > 0:
        ids = list(range(base_id, base_id + offset))  # 从回复消息往后数（不包含回复消息本身）
    else:  # offset == 0
        ids = [base_id]  # 仅处理回复消息本身

    messages_to_process = []
    for msg_id in ids:
        msg = await get_message(client, chat_id, msg_id, process_msg)
        if msg:
            messages_to_process.append(msg)

    if not messages_to_process:
        await process_msg.edit("❌ 未找到有效消息。")
        await asyncio.sleep(5)
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
        # 优化头像和用户名显示逻辑：如果同一用户连续发送多条消息，后续消息不显示头像和用户名
        if all_messages_data and current_user_id == last_user_id:
            data["avatar"] = False
            data["from"]["name"] = ""
            data["from"]["username"] = ""
            if "photo" in data["from"]:
                del data["from"]["photo"]
        else:
            last_user_id = current_user_id

        if enable_reply and m.reply_to_message:
            reply_data = await extract_message(m.reply_to_message, client, process_msg)
            if reply_data:
                if reply_data.get("media") and reply_data["media"].get("s3_key"):
                    s3_keys_to_delete.append(reply_data["media"]["s3_key"])

                reply_name = reply_data["from"].get("name", "")
                if not reply_name:
                    reply_name_parts = filter(None, [
                        reply_data["from"].get("first_name"),
                        reply_data["from"].get("last_name")
                    ])
                    reply_name = " ".join(reply_name_parts) or "未知用户"

                data["replyMessage"] = {
                    "name": reply_name,
                    "text": reply_data.get("text", ""),
                    "entities": reply_data.get("entities", []),
                    "chatId": reply_data["from"]["id"],
                }
                # 如果回复消息带有媒体，Quote API也支持显示其媒体
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
        json_data = res.json()
        if not json_data.get("ok"):
            error_msg = json_data.get("error", "未知API错误")
            raise Exception(f"API返回失败: {error_msg}")
        img_bytes = base64.b64decode(json_data["result"]["image"])
        img_io = io.BytesIO(img_bytes)
        img_io.name = f"quote.{QUOTE_SETTINGS['format']}"
        img_io.seek(0)

        # 根据最终生成的图片格式发送
        if QUOTE_SETTINGS['format'] == 'webp':
            # 如果语录生成器始终返回 WebP，则发送动画
            await client.send_animation(chat_id, img_io)
        else:
            # 否则发送文档（图片）
            await client.send_document(chat_id, img_io)
        await process_msg.safe_delete()

        # 清理S3文件，不编辑消息
        if s3_keys_to_delete:
            for s3_key in s3_keys_to_delete:
                await delete_s3_file(s3_key)

    except Exception as e:
        await process_msg.edit(f"❌ 语录生成失败：{str(e)}\n请检查Quote API服务状态或请求内容。")
        await asyncio.sleep(10)