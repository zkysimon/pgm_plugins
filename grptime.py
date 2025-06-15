import datetime
from collections import defaultdict
import json
from pagermaid.listener import listener
from pagermaid.enums import Client, Message
from pyrogram.enums import ParseMode


@listener(is_plugin=True, outgoing=True, command="grptime",
          description="查询用户入群时间（仅限群组）",
          parameters="(可选) @用户名 (可选) 开始 结束")
async def join_time(client: Client, message: Message):
    """查询用户入群时间。"""

    args = message.arguments.split()
    start = int(args[0]) if len(args) > 0 and args[0].isdigit() else 1
    end = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5

    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        try:
            chat_member = await client.get_chat_member(message.chat.id, user_id)
            joined_date = chat_member.joined_date
            if joined_date:
                joined_timestamp = int(joined_date.timestamp())
                joined_datetime = datetime.datetime.fromtimestamp(joined_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                await message.edit(f"用户 {user_id} 的入群时间为：{joined_datetime}")
            else:
                await message.edit(f"无法获取用户 {user_id} 的入群时间。")
        except Exception as e:
            await message.edit(f"获取入群时间时出错：{e}")
    else:
        # 统计群成员入群时间分布
        await message.edit("正在统计群成员入群时间分布...")
        join_month_counts = defaultdict(int)
        join_times = []

        async for member in client.get_chat_members(message.chat.id):
            joined_date = member.joined_date
            if joined_date:
                join_month_counts[joined_date.strftime("%Y-%m")] += 1
                join_times.append((joined_date, member.user))

        # 按加入时间排序
        join_times.sort(key=lambda x: x[0])

        # 获取指定范围
        specified_members = join_times[start-1:end]

        # 格式化输出
        if join_month_counts:
            sorted_months = sorted(join_month_counts.keys())
            result = "本群成员入群时间分布：\n"
            for month in sorted_months:
                count = join_month_counts[month]
                result += f"> {month}: **{count}** 人\n"

            if specified_members:
                result += "\n最早进群的成员：\n"
                for join_date, user in specified_members:
                    name = (user.first_name or '') + ' ' + (user.last_name or '')
                    name = name.strip() or str(user.id)
                    joined_datetime = join_date.strftime('%Y-%m-%d %H:%M:%S')
                    result += f"- {name} ({joined_datetime})\n"

            await message.edit(result, parse_mode=ParseMode.MARKDOWN)
        else:
            await message.edit("无法获取群成员入群时间信息。")
        return

@listener(is_plugin=True, outgoing=True, incoming=True, ignore_edited=True)
async def query_join_time(client: Client, message: Message):
    """查询用户入群时间。"""
    if message.text == "我要查询入群时间":
  

      user_id = message.from_user.id

      try:
          chat_member = await client.get_chat_member(message.chat.id, user_id)
          joined_date = chat_member.joined_date
          if joined_date:
            joined_timestamp = int(joined_date.timestamp())
            joined_datetime = datetime.datetime.fromtimestamp(joined_timestamp).strftime('%Y-%m-%d %H:%M:%S')
            await message.reply(f"您的入群时间为：{joined_datetime}")
          else:
            await message.reply(f"无法获取您的入群时间。")
      except Exception as e:
        await message.reply(f"获取入群时间时出错：{e}")
        
    
    if message.text != "我的信息":
        return
    
    user_id = message.from_user.id

    try:
        chat_member = await client.get_chat_member(message.chat.id, user_id)

    # 将 chat_member 的内容转为 JSON 格式
        chat_member_json = json.dumps(chat_member, default=str)  # 使用 default=str 处理非序列化对象

    # 将内容写入文件
        with open("chat_member_info.json", "w") as f:
            f.write(chat_member_json)

    # 发送文件
        with open("chat_member_info.json", "rb") as f:
            await message.reply_document(f, caption="这是您请求的聊天成员信息。")

    except Exception as e:
        await message.reply(f"获取信息时出错：{e}")
