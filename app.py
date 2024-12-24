from flask import Flask, render_template, request, jsonify
from flask_httpauth import HTTPBasicAuth
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# 初始化 Flask 應用程式與 HTTP 認證
app = Flask(__name__, template_folder='templates')
auth = HTTPBasicAuth()

# 設置用戶名與加密後的密碼（使用安全的密碼雜湊存儲）
users = {
    "0961282056": generate_password_hash("0961282056"),
    "user": generate_password_hash("user")
}

# 驗證用戶名與密碼
@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users.get(username), password):
        return username
    return None

# LINE API 發送訊息的路由
LINE_API_URL = 'https://api.line.me/v2/bot/message/push'
LINE_TOKEN = 'tKoRRRQUP+AbMeuxU5QueF3IKdkD51bun+e3Ji1IL8SAFcoFqZMFmXfXiv+hX36Iz/U5ivj8Sze4uV47Voi/1ISoZ+tYppO5oPgjOl2GVwVY8IYjLhIUNEsEISbz8l0qClAwr35wwmFx7WEHO0Ua/gdB04t89/1O/w1cDnyilFU='  # 替換為你的 channel token

@app.route('/send-line-message', methods=['POST'])
def send_line_message():
    data = request.json  # 從前端接收的資料

    # 檢查是否提供了有效的 'to' 屬性
    if not data.get('to'):
        return jsonify({"status": "error", "message": "'to' field is missing or invalid"}), 400

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_TOKEN}'
    }

    # 發送請求到 LINE API
    response = requests.post(LINE_API_URL, headers=headers, json=data)

    if response.status_code == 200:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": response.text}), 500


# 定義排序邏輯，根據首播日期與時間排序
def parse_date_time(anime):
    weekday_map = {'日': 7, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6}
    try:
        # 將中文星期轉換為對應的數字，無效日期默認排最後
        weekday = weekday_map.get(anime['premiere_date'], 7)
        # 解析首播時間，格式為 HH:MM
        premiere_time = datetime.strptime(anime['premiere_time'], "%H:%M")
        return (weekday, premiere_time)
    except ValueError:
        # 無效時間的情況
        return (7, datetime.max)

# 抓取動畫數據的函式
def fetch_anime_data(year, season):
    # 季節對應的月份
    season_to_month = {
        '冬': 1,
        '春': 4,
        '夏': 7,
        '秋': 10
    }

    # 檢查季節是否有效
    if season not in season_to_month:
        return "季節無效，請輸入有效季節（冬、春、夏、秋）"

    month = season_to_month[season]

    # 構建目標 URL
    url = f"https://acgsecrets.hk/bangumi/{year}{month:02d}/"
    response = requests.get(url)

    # 設定正確的編碼格式
    response.encoding = 'utf-8'

    # 確認請求是否成功
    if response.status_code != 200:
        return "無法從網站獲取資料，請檢查網站是否正確"

    # 解析 HTML
    soup = BeautifulSoup(response.text, 'html.parser')
    anime_data = soup.find('div', id='acgs-anime-icons')
    if not anime_data:
        return "未找到任何動畫資料"

    # 提取動畫詳細資訊
    anime_items = anime_data.find_all('div', class_='CV-search')
    anime_list = []
    for anime_item in anime_items:
        bangumi_id = anime_item.get('acgs-bangumi-data-id', "未知ID")
        anime_name = anime_item.find('div', class_='anime_name').text.strip() if anime_item.find('div', class_='anime_name') else "無名稱"
        anime_image_url = anime_item.find('div', class_='overflow-hidden anime_bg').img['src'] if anime_item.find('div', class_='overflow-hidden anime_bg') and anime_item.find('div', class_='overflow-hidden anime_bg').img else "無圖片"
        premiere_date = anime_item.find('div', class_='day').text.strip() if anime_item.find('div', class_='day') else "無首播日期"
        premiere_time = anime_item.find('div', class_='time').text.strip() if anime_item.find('div', class_='time') else "無首播時間"

        anime_list.append({
            'bangumi_id': bangumi_id,
            'anime_name': anime_name,
            'anime_image_url': anime_image_url,
            'premiere_date': premiere_date,
            'premiere_time': premiere_time
        })

    # 根據日期和時間排序
    sorted_anime_list = sorted(anime_list, key=parse_date_time)

    return sorted_anime_list if sorted_anime_list else "未找到任何動畫資料"

# 網頁首頁路由，顯示表單與爬蟲結果
@app.route('/', methods=['GET', 'POST'])
@auth.login_required
def index():
    error_message = None
    sorted_anime_list = None
    current_year = datetime.now().year
    current_month = datetime.now().month

    years = [current_year + i for i in range(1, 2)] + [current_year - i for i in range(0, 8)]

    if 1 <= current_month <= 3:
        default_season = "冬"
    elif 4 <= current_month <= 6:
        default_season = "春"
    elif 7 <= current_month <= 9:
        default_season = "夏"
    else:
        default_season = "秋"

    selected_season = default_season
    selected_year = str(current_year)  # 預設選中當前年份
    premiere_date = '全部'  # 預設選擇所有首播日期

    if request.method == 'POST':
        selected_year = request.form['year']
        selected_season = request.form['season']
        premiere_date = request.form.get('premiere_date', '全部')  
        
        if not selected_year.isdigit() or len(selected_year) != 4:
            error_message = "請輸入有效的年份（例如：2024）"
        else:
            sorted_anime_list = fetch_anime_data(selected_year, selected_season)

            if premiere_date != "全部":
                sorted_anime_list = [anime for anime in sorted_anime_list if anime['premiere_date'] == premiere_date]

            if isinstance(sorted_anime_list, str):
                error_message = sorted_anime_list

    return render_template(
        'index.html',
        years=years,
        sorted_anime_list=sorted_anime_list,
        error_message=error_message,
        current_year=current_year,
        default_season=default_season,
        selected_season=selected_season,
        selected_year=selected_year,
        premiere_date=premiere_date
    )

# 啟動 Flask 應用程式
if __name__ == '__main__':
    app.run(debug=True)
