from flask import Flask, render_template, request, jsonify  # 導入 Flask 核心模組，用於建立 Web 應用
from flask_httpauth import HTTPBasicAuth  # 導入 HTTP 基礎認證模組，用於用戶驗證
import requests  # 導入 requests 模組，用於發送 HTTP 請求
from bs4 import BeautifulSoup  # 導入 BeautifulSoup，用於解析 HTML
from datetime import datetime  # 導入 datetime，用於處理日期和時間
from werkzeug.security import generate_password_hash, check_password_hash  # 導入密碼加密和驗證工具
from typing import List, Dict, Optional, Tuple  # 導入類型提示，提升程式碼可讀性
from dotenv import load_dotenv  # 導入 dotenv，用於載入環境變數
import os  # 導入 os 模組，用於操作環境變數
from flask_caching import Cache  # 導入快取模組，提升效能
from flask_limiter import Limiter  # 導入速率限制模組，防止暴力破解
from flask_limiter.util import get_remote_address  # 導入工具函數，用於獲取客戶端 IP
from config import Config  # 導入自定義配置類，集中管理常量

# 載入環境變數，從 .env 文件中讀取敏感資訊
load_dotenv()
LINE_TOKEN = os.getenv('LINE_TOKEN')  # 從環境變數獲取 LINE API 的認證令牌
users = {  # 定義用戶字典，儲存用戶名和加密後的密碼
    os.getenv('USERNAME_1'): generate_password_hash(os.getenv('PASSWORD_1')),  # 第一個用戶
    os.getenv('USERNAME_2'): generate_password_hash(os.getenv('PASSWORD_2'))   # 第二個用戶
}

# 初始化 Flask 應用和相關模組
app = Flask(__name__, template_folder='templates')  # 創建 Flask 應用，指定模板資料夾為 'templates'
auth = HTTPBasicAuth()  # 初始化 HTTP 基礎認證
cache = Cache(app, config={'CACHE_TYPE': 'simple'})  # 初始化快取，使用簡單記憶體快取
limiter = Limiter(  # 初始化速率限制器，限制請求頻率
    get_remote_address,  # 使用客戶端 IP 作為限制依據
    app=app,  # 綁定 Flask 應用
    default_limits=["200 per day", "50 per hour"]  # 預設限制：每天200次，每小時50次
)

# 使用配置中的常量，從 config.py 中引入
LINE_API_URL = Config.LINE_API_URL  # LINE API 的推送訊息端點
SEASON_TO_MONTH = Config.SEASON_TO_MONTH  # 季節到月份的映射，用於生成查詢 URL
WEEKDAY_MAP = Config.WEEKDAY_MAP  # 中文星期到數字的映射，用於排序

@auth.verify_password
def verify_password(username: str, password: str) -> Optional[str]:
    """驗證用戶名和密碼
    Args:
        username: 輸入的用戶名
        password: 輸入的密碼
    Returns:
        如果驗證成功返回用戶名，否則返回 None
    """
    # 檢查用戶名是否存在且密碼匹配
    return username if username in users and check_password_hash(users[username], password) else None

def send_line_message(to: str, message_data: Dict) -> Tuple[Dict, int]:
    """發送 LINE 訊息到指定用戶
    Args:
        to: 訊息接收者的 ID
        message_data: 要發送的訊息內容字典
    Returns:
        Tuple 包含回應狀態字典和 HTTP 狀態碼
    """
    headers = {  # 定義 HTTP 請求頭
        'Content-Type': 'application/json',  # 指定內容類型為 JSON
        'Authorization': f'Bearer {LINE_TOKEN}'  # 使用 Bearer Token 進行認證
    }
    try:
        # 發送 POST 請求到 LINE API
        response = requests.post(LINE_API_URL, headers=headers, json=message_data)
        response.raise_for_status()  # 如果狀態碼不是 200 系列，拋出異常
        return {"status": "success"}, 200  # 成功時返回成功狀態和 200 碼
    except requests.RequestException as e:  # 捕獲所有 requests 相關異常
        return {"status": "error", "message": str(e)}, 500  # 失敗時返回錯誤訊息和 500 碼

@app.route('/send-line-message', methods=['POST'])
def handle_line_message():
    """處理前端發送的 LINE 訊息請求"""
    data = request.get_json()  # 從請求中獲取 JSON 資料
    # 驗證資料是否有效
    if not data or not isinstance(data, dict) or 'to' not in data:
        return jsonify({"status": "error", "message": "'to' field is required"}), 400  # 返回 400 錯誤
    return jsonify(*send_line_message(data['to'], data))  # 解包並返回 send_line_message 的結果

def parse_date_time(anime: Dict) -> Tuple[int, datetime]:
    """解析動畫的首播日期和時間，用於排序
    Args:
        anime: 包含動畫資訊的字典
    Returns:
        Tuple 包含星期數字和時間物件
    """
    try:
        # 從 WEEKDAY_MAP 獲取星期數字，無效時默認為 7（星期日）
        weekday = WEEKDAY_MAP.get(anime['premiere_date'], 7)
        # 將時間字符串轉為 datetime 物件
        premiere_time = datetime.strptime(anime['premiere_time'], "%H:%M")
        return weekday, premiere_time  # 返回星期和時間的元組
    except (ValueError, KeyError):  # 處理時間格式錯誤或鍵缺失
        return 7, datetime.max  # 返回最大值，確保無效資料排在最後

@cache.cached(timeout=3600, key_prefix=lambda: f"anime_{year}_{season}")
def fetch_anime_data(year: str, season: str) -> List[Dict]:
    print(f"Fetching data for year: {year}, season: {season}")
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]
    month = SEASON_TO_MONTH[season]
    url = f"https://acgsecrets.hk/bangumi/{year}{month:02d}/"
    print(f"Fetching URL: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')
        anime_data = soup.find('div', id='acgs-anime-icons')
        if not anime_data:
            return [{"error": "未找到任何動畫資料"}]
        anime_list = []
        for item in anime_data.find_all('div', class_='CV-search'):
            premiere_date = item.find('div', class_='day').text.strip() if item.find('div', class_='day') else "無首播日期"
            premiere_date = premiere_date.replace("星期", "") if "星期" in premiere_date else premiere_date
            anime_list.append({
                'bangumi_id': item.get('acgs-bangumi-data-id', "未知ID"),
                'anime_name': (item.find('div', class_='anime_name').text.strip() 
                              if item.find('div', class_='anime_name') else "無名稱"),
                'anime_image_url': (item.find('div', class_='overflow-hidden anime_bg')
                                  .img['src'] if item.find('div', class_='overflow-hidden anime_bg') 
                                  and item.find('div', class_='overflow-hidden anime_bg').img else "無圖片"),
                'premiere_date': premiere_date,
                'premiere_time': (item.find('div', class_='time').text.strip() 
                                if item.find('div', class_='time') else "無首播時間")
            })
        return sorted(anime_list, key=parse_date_time)
    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return [{"error": "無法從網站獲取資料，請檢查網站是否正確"}]

def get_current_season(month: int) -> str:
    """根據月份確定當前季節
    Args:
        month: 月份數字 (1-12)
    Returns:
        對應的季節名稱
    """
    if 1 <= month <= 3: return "冬"  # 1-3 月為冬季
    if 4 <= month <= 6: return "春"  # 4-6 月為春季
    if 7 <= month <= 9: return "夏"  # 7-9 月為夏季
    return "秋"  # 10-12 月為秋季

@app.route('/', methods=['GET', 'POST'])
@auth.login_required
@limiter.limit("5 per minute")
def index():
    now = datetime.now()
    years = [str(now.year + i) for i in range(1, 2)] + [str(now.year - i) for i in range(0, 8)]
    default_season = get_current_season(now.month)
    context = {
        'years': years,
        'sorted_anime_list': None,
        'error_message': None,
        'selected_season': default_season,
        'selected_year': str(now.year),
        'premiere_date': '全部'
    }
    if request.method == 'POST':
        context['selected_year'] = request.form.get('year', str(now.year))
        context['selected_season'] = request.form.get('season', default_season)
        context['premiere_date'] = request.form.get('premiere_date', '全部')
        print(f"Received form data: year={context['selected_year']}, season={context['selected_season']}, premiere_date={context['premiere_date']}")
        if not context['selected_year'].isdigit() or len(context['selected_year']) != 4:
            context['error_message'] = "請輸入有效的年份（例如：2024）"
        else:
            cache.delete(f"anime_{context['selected_year']}_{context['selected_season']}")  # 清除特定快取
            context['sorted_anime_list'] = fetch_anime_data(context['selected_year'], context['selected_season'])
            if context['premiere_date'] != "全部":
                context['sorted_anime_list'] = [
                    anime for anime in context['sorted_anime_list'] 
                    if anime.get('premiere_date') == context['premiere_date']
                ]
            if context['sorted_anime_list'] and 'error' in context['sorted_anime_list'][0]:
                context['error_message'] = context['sorted_anime_list'][0]['error']
                context['sorted_anime_list'] = None
    return render_template('index.html', **context)

if __name__ == '__main__':
    # 啟動 Flask 應用
    # 使用環境變數控制端口和調試模式，預設端口 5000，預設關閉 debug
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=os.getenv('FLASK_DEBUG', 'False') == 'True')