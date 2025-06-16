import json
import logging
from pagermaid.listener import listener
from pagermaid.enums import Client, Message
from pagermaid.utils import pip_install
from pagermaid.dependence import sqlite

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 尝试导入 aiohttp，如果不存在则安装
try:
    import aiohttp
except ImportError:
    pip_install("aiohttp")
    import aiohttp

# 尝试导入 edge_tts，如果不存在则安装
try:
    import edge_tts
except ImportError:
    pip_install("edge-tts")
    import edge_tts

default_config = {
    "short_name": "zh-CN-XiaoxiaoNeural",
    "style": "general",
    "rate": "+0%",
    "volume": "+0%"
}
output = "data/mtts.mp3"

async def config_check() -> dict:
    if not sqlite.get('edge-tts', {}):
        sqlite['edge-tts'] = default_config
    return sqlite['edge-tts']

async def config_set(configset, cmd) -> bool:
    config = await config_check()
    config[cmd] = configset
    sqlite['edge-tts'] = config
    return True

async def getmodel():
    headers = {'origin': 'https://azure.microsoft.com'}
    url = "https://eastus.api.speech.microsoft.com/cognitiveservices/voices/list"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    logger.error(f"API请求失败，状态码: {response.status}, 响应: {await response.text()}")
                    raise Exception(f"API请求失败，状态码: {response.status}")
                data = await response.json()
                return data
    except Exception as e:
        logger.error(f"获取语音列表失败: {str(e)}")
        raise

@listener(command="mtts", description="文本转语音",
          parameters="[str]\r\nmtts setname [str]\r\nmtts setrate [int]\r\nmtts setvolume [int]\r\nmtts list [str]")
async def mtts(msg: Message):
    opt = msg.arguments
    replied_msg = msg.reply_to_message
    if opt.startswith("setname "):
        model_name = opt.split(" ")[1]
        # 验证语音名称
        try:
            voice_model = await getmodel()
            if not any(model['ShortName'] == model_name for model in voice_model):
                return await msg.edit(f"❗️ 无效的语音名称: {model_name}")
        except Exception:
            return await msg.edit("无法访问微软API，请稍后重试。")
        status = await config_set(model_name, "short_name")
        if not status:
            return await msg.edit("❗️ TTS设置失败")
        await msg.edit(f"成功设置TTS语音模型为: {model_name}")
    elif opt.startswith("setrate "):
        rate = opt.split(" ")[1]
        status = await config_set(rate, "rate")
        if not status:
            return await msg.edit("❗️ TTS设置失败")
        await msg.edit(f"成功设置TTS语速为: {rate}")
    elif opt.startswith("setvolume "):
        volume = opt.split(" ")[1]
        status = await config_set(volume, "volume")
        if not status:
            return await msg.edit("❗️ TTS设置失败")
        await msg.edit(f"成功设置TTS音量为: {volume}")
    elif opt.startswith("list "):
        tag = opt.split(" ")[1]
        try:
            voice_model = await getmodel()
        except Exception:
            return await msg.edit("无法访问微软API，请稍后重试。")
        s = "code | local name | Gender | LocaleName\r\n"
        for model in voice_model:
            if tag in model['ShortName'] or tag in model['LocalName'] or tag in model['LocaleName']:
                s += f"{model['ShortName']} | {model['LocalName']} | {model['Gender']} | {model['LocaleName']}\r\n"
        await msg.edit(s)
    elif opt and opt != " ":
        config = await config_check()
        try:
            mp3_buffer = edge_tts.Communicate(
                text=opt,
                voice=config["short_name"],
                rate=config["rate"],
                volume=config["volume"]
            )
            await mp3_buffer.save(output)
        except Exception as e:
            logger.error(f"TTS转换失败: {str(e)}")
            return await msg.edit("无法访问微软API，请稍后重试。")
        if replied_msg is None:
            await msg.reply_voice(output)
            await msg.delete()
        else:
            await msg.reply_voice(output, reply_to_message_id=replied_msg.id)
            await msg.delete()
    elif replied_msg:
        config = await config_check()
        try:
            mp3_buffer = edge_tts.Communicate(
                text=replied_msg.text,
                voice=config["short_name"],
                rate=config["rate"],
                volume=config["volume"]
            )
            await mp3_buffer.save(output)
        except Exception as e:
            logger.error(f"TTS转换失败: {str(e)}")
            return await msg.edit("无法访问微软API，请稍后重试。")
        await msg.reply_voice(output, reply_to_message_id=replied_msg.id)
        await msg.delete()
    else:
        await msg.edit("错误，请使用帮助命令查看用法")
