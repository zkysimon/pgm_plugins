from pagermaid.listener import listener
from pagermaid.enums import Message
import os
import json
import asyncio
import aiohttp

# 全局变量
from_lang = "auto"  # 源语言，固定为 auto
to_lang = "en"      # 默认目标语言为英语
global_translate_enabled = False  # 全局翻译开关

@listener(command="fy",
          description="控制翻译功能",
          parameters="[无参数] 开关当前聊天翻译 | all on/off 开启或关闭全局翻译 | set <目标语言> 设置目标语言",
          prefix=",")
async def handle_fy_command(message: Message):
    chat_id = message.chat.id
    args = message.parameter
    file_path = "fy.json"

    # 处理全局翻译开关
    if len(args) == 2 and args[0] == "all" and args[1] in ["on", "off"]:
        global global_translate_enabled
        global_translate_enabled = args[1] == "on"
        status = "开启" if global_translate_enabled else "关闭"
        await message.edit(f"全局翻译已{status}。")
        await asyncio.sleep(5)
        await message.delete()
        return  # 优先处理全局翻译，退出函数

    # 处理语言设置
    if len(args) == 2 and args[0] == "set":
        global to_lang
        new_to_lang = args[1]
        try:
            try:
                with open(file_path, "r", encoding="utf-8") as file:
                    settings = json.load(file)
            except (FileNotFoundError, json.JSONDecodeError):
                settings = {"translate_id": []}
            settings["from_lang"] = from_lang  # 固定为 auto
            settings["to_lang"] = new_to_lang
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(settings, file, indent=4, ensure_ascii=False)
            to_lang = new_to_lang
            await message.edit(f"翻译语言设置为：从 {from_lang} 到 {new_to_lang}")
            await asyncio.sleep(5)
            await message.delete()
        except Exception as e:
            await message.edit(f"保存设置时发生错误：{e}")
            await asyncio.sleep(5)
            await message.delete()
        return

    # 处理独立翻译开关
    if not args:  # 无参数时切换当前聊天翻译
        if not os.path.exists(file_path):
            data = {"translate_id": []}
        else:
            with open(file_path, "r", encoding="utf-8") as file:
                data = json.load(file)

        translate_ids = data.get("translate_id", [])

        if chat_id in translate_ids:
            translate_ids.remove(chat_id)
            action = "关闭"
        else:
            translate_ids.append(chat_id)
            action = "开启"

        data["translate_id"] = translate_ids

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)

        await message.edit(f"{action} 此ID为 <code>{chat_id}</code> 的群/人翻译成功。")
        await asyncio.sleep(10)
        await message.delete()
    else:
        await message.edit("用法：,fy [无参数] 或 ,fy all on/off 或 ,fy set <目标语言>")
        await asyncio.sleep(5)
        await message.delete()

def get_translate_ids():
    """读取配置文件中的翻译 ID 列表"""
    file_path = os.path.join(os.getcwd(), "fy.json")
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data.get("translate_id", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []

@listener(is_group=True, outgoing=True, ignore_edited=True)
async def global_translate(message: Message):
    """全局翻译监听器"""
    if not message.text:
        return

    # 检查是否需要翻译
    if not global_translate_enabled:
        translate_ids = get_translate_ids()
        if message.chat.id not in translate_ids:
            return

    # 忽略以特定前缀开头的消息
    prefixes = ["，", ",", "/", "-"]
    if any(message.text.startswith(prefix) for prefix in prefixes):
        return

    # 调用 DeepLX 翻译
    translated_text = await translate_deeplx(message.text)
    if translated_text:
        new_text = f"<b>{message.text}</b>\n<blockquote><i>{translated_text}</i></blockquote>"
        await message.edit(new_text)

async def translate_deeplx(text):
    """使用 DeepLX API 进行翻译"""
    url = "https://api.deeplx.org/EaEyeqJu9r6Or7Mpz4ufO2pPYc3MEkqtNN5G2LG1A8k/translate"
    payload = {
        "text": text,
        "source_lang": from_lang,
        "target_lang": to_lang
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status != 200:
                print(f"翻译失败：HTTP {response.status}")
                return None

            result = await response.json()
            if result.get("code") != 200:
                print(f"翻译失败：{result}")
                return None

            return result.get("data")