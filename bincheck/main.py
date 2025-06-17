import json
from json.decoder import JSONDecodeError
from pagermaid.enums import Message, Client
from pagermaid.listener import listener
from pagermaid.utils import pip_install

# from pagermaid.services import client as requests # 替换这里
from pagermaid.services import client as requests


@listener(command="bin", description="查询信用卡信息", parameters="[bin（4到8位数字）]")
async def card(_: Client, message: Message):
    await message.edit("正在查询中...")
    try:
        card_bin = message.arguments
        if not card_bin or not card_bin.isdigit() or not (4 <= len(card_bin) <= 8):
            raise ValueError
    except ValueError:
        await message.edit("出错了呜呜呜 ~ 无效的参数。请输入一个4到8位的数字。")
        return

    url = f"https://lookup.binlist.net/{card_bin}"
    headers = {
        "Accept-Version": "3",
        "User-Agent": "PagerMaid-PY"
    }

    try:
        response = await requests.get(url, headers=headers)
    except Exception as e:
        await message.edit(f"出错了呜呜呜 ~ 无法访问到API：{e}")
        return

    if response.status_code == 404:
        await message.edit(f"出错了呜呜呜 ~ 未找到该 BIN 的信息，请检查 BIN 是否正确。")
        return
    elif response.status_code != 200:
        await message.edit(f"出错了呜呜呜 ~ API返回错误状态码：{response.status_code}")
        return

    try:
        # !!! 修正这里：如果 response.json() 返回的是字典，则移除 await !!!
        response_json = response.json()
    except JSONDecodeError:
        await message.edit("出错了呜呜呜 ~ 无法解析API返回的数据。")
        return

    if not response_json:
        await message.edit("出错了呜呜呜 ~ API查询失败，请检查BIN是否正确。")
        return

    bin_data = response_json

    msg_out = [f"**卡头：**`{card_bin}`"]
    if bin_data.get("scheme"):
        msg_out.append(f"**品牌：**`{bin_data.get('scheme')}`")
    if bin_data.get("type"):
        msg_out.append(f"**类型：**`{bin_data.get('type')}`")
    if bin_data.get("brand"):
        msg_out.append(f"**种类：**`{bin_data.get('brand')}`")
    if "prepaid" in bin_data:
        msg_out.append(f"**预付：**`{'是' if bin_data.get('prepaid') else '否'}`")

    if bin_data.get("bank", {}).get("name"):
        msg_out.append(f"**卡行：**`{bin_data.get('bank', {}).get('name')}`")
    if bin_data.get("bank", {}).get("url"):
        msg_out.append(f"**网站：**`{bin_data.get('bank', {}).get('url')}`")
    if bin_data.get("bank", {}).get("phone"):
        msg_out.append(f"**电话：**`{bin_data.get('bank', {}).get('phone')}`")
    if bin_data.get("bank", {}).get("city"):
        msg_out.append(f"**城市：**`{bin_data.get('bank', {}).get('city')}`")

    country_data = bin_data.get("country", {})
    if country_data.get("name"):
        country_flag = country_data.get("emoji", "")
        msg_out.append(f"**国家：**`{country_data.get('name')} {country_flag}`")
    if country_data.get("alpha2"):
        msg_out.append(f"**代码：**`{country_data.get('alpha2')}`")

    if bin_data.get("country", {}).get("currency"):
        currency_code = bin_data.get("country", {}).get("currency")
        msg_out.append(f"**货币：**`{currency_code}`")
    else:
        currency_code = None

    exchange_rate_usd = None
    exchange_rate_cny = None
    usd_to_cny = None

    if currency_code and currency_code != "未知":
        try:
            exchange_rate_api = f"https://api.exchangerate-api.com/v4/latest/{currency_code}"
            # !!! 修正这里：如果 response.json() 返回的是字典，则移除 await !!!
            rate_response = await requests.get(exchange_rate_api)
            if rate_response.status_code == 200:
                rate_data = rate_response.json() # 移除 await
                exchange_rate_usd = rate_data["rates"].get("USD", None)
                exchange_rate_cny = rate_data["rates"].get("CNY", None)
            usd_rate_response = await requests.get("https://api.exchangerate-api.com/v4/latest/USD")
            if usd_rate_response.status_code == 200:
                usd_rate_data = usd_rate_response.json() # 移除 await
                usd_to_cny = usd_rate_data["rates"].get("CNY", None)
        except Exception:
            exchange_rate_usd = None
            exchange_rate_cny = None
            usd_to_cny = None

    if exchange_rate_usd:
        msg_out.append(f"**1 {currency_code} = {exchange_rate_usd:.2f} USD**")
    if exchange_rate_cny:
        msg_out.append(f"**1 {currency_code} = {exchange_rate_cny:.2f} CNY**")
    if usd_to_cny:
        msg_out.append(f"**1 USD = {usd_to_cny:.2f} CNY**")

    if not msg_out:
        await message.edit("查询失败，没有找到有效信息。")
        return
    result = "> " + "\n> ".join(msg_out)
    await message.edit(result)
