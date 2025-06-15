import json
from json.decoder import JSONDecodeError
from pagermaid.enums import Message, Client
from pagermaid.listener import listener
from pagermaid.utils import pip_install

pip_install("requests")

import requests


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
    url = "https://bin-ip-checker.p.rapidapi.com/"
    querystring = {"bin": card_bin}
    headers = {
        "x-rapidapi-key": "036aa3f60bmsh6ba2d197966fd03p15f4e7jsnf59859793378",
        "x-rapidapi-host": "bin-ip-checker.p.rapidapi.com",
        "Content-Type": "application/json"
    }
    payload = {
        "bin": card_bin
    }

    try:
        response = requests.post(url, json=payload, headers=headers, params=querystring)
    except requests.RequestException as e:
        await message.edit(f"出错了呜呜呜 ~ 无法访问到API：{e}")
        return

    if response.status_code != 200:
        await message.edit(f"出错了呜呜呜 ~ API返回错误状态码：{response.status_code}")
        return

    try:
        response_json = response.json()
    except JSONDecodeError:
        await message.edit("出错了呜呜呜 ~ 无法解析API返回的数据。")
        return

    if not response_json.get("success", False):
        await message.edit("出错了呜呜呜 ~ API查询失败，请检查BIN是否正确。")
        return
    bin_data = response_json.get("BIN", {})
    country_data = bin_data.get("country", {})
    issuer_data = bin_data.get("issuer", {})

    msg_out = [f"**卡头：**`{card_bin}`"]  # 显示 BIN 卡头
    if bin_data.get("scheme"):
        msg_out.append(f"**品牌：**`{bin_data.get('scheme')}`")
    if bin_data.get("type"):
        msg_out.append(f"**类型：**`{bin_data.get('type')}`")
    if bin_data.get("brand"):
        msg_out.append(f"**种类：**`{bin_data.get('brand')}`")
    if bin_data.get("level"):
        msg_out.append(f"**级别：**`{bin_data.get('level')}`")
    if bin_data.get("is_commercial") is not None:
        msg_out.append(f"**商业：**`{'是' if bin_data.get('is_commercial') == 'true' else '否'}`")
    if bin_data.get("is_prepaid") is not None:
        msg_out.append(f"**预付：**`{'是' if bin_data.get('is_prepaid') == 'true' else '否'}`")
    if issuer_data.get("name"):
        msg_out.append(f"**卡行：**`{issuer_data.get('name')}`")
    if issuer_data.get("website"):
        msg_out.append(f"**网站：**`{issuer_data.get('website')}`")
    if issuer_data.get("phone"):
        msg_out.append(f"**电话：**`{issuer_data.get('phone')}`")

    # 添加国家信息
    if country_data.get("name"):
        country_flag = country_data.get("flag", "")
        msg_out.append(f"**国家：**`{country_data.get('name')} {country_flag}`")
    if country_data.get("alpha2"):
        msg_out.append(f"**代码：**`{country_data.get('alpha2')}`")
    if country_data.get("currency") and country_data.get("currency_name"):
        msg_out.append(
            f"**货币：**`{country_data.get('currency')} ({country_data.get('currency_name')})`")
    currency_code = country_data.get("currency", "未知")
    exchange_rate_usd = None
    exchange_rate_cny = None
    usd_to_cny = None

    if currency_code and currency_code != "未知":
        try:
            exchange_rate_api = f"https://api.exchangerate-api.com/v4/latest/{currency_code}"
            rate_response = requests.get(exchange_rate_api)
            if rate_response.status_code == 200:
                rate_data = rate_response.json()
                exchange_rate_usd = rate_data["rates"].get("USD", None)
                exchange_rate_cny = rate_data["rates"].get("CNY", None)
            usd_rate_response = requests.get("https://api.exchangerate-api.com/v4/latest/USD")
            if usd_rate_response.status_code == 200:
                usd_rate_data = usd_rate_response.json()
                usd_to_cny = usd_rate_data["rates"].get("CNY", None)
        except requests.RequestException:
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
