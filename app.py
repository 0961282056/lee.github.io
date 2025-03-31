from flask import Flask, render_template, request, jsonify
from flask_httpauth import HTTPBasicAuth
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from typing import List, Dict, Optional, Tuple

# 常量定義區
LINE_API_URL = 'https://api.line.me/v2/bot/message/push'  # LINE API 的推送訊息端點
LINE_TOKEN = 'tKoRRRQUP+AbMeuxU5QueF3IKdkD51bun+e3Ji1IL8SAFcoFqZMFmXfXiv+hX36Iz/U5ivj8Sze4uV47Voi/1ISoZ+tYppO5oPgjOl2GVwVY8IYjLhIUNEsEISbz8l0qClAwr35wwmFx7WEHO0Ua/gdB04t89/1O/w1cDnyilFU='  # LINE Bot 的認證令牌
SEASON_TO_MONTH = {'冬': 1, '春': 4, '夏': 7, '秋': 10}  # 季節到月份的映射，用於生成動畫查詢URL
WEEKDAY_MAP = {'日': 7, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6}  # 中文星期到數字的映射，用於排序

# 初始化 Flask 應用和認證模組
app = Flask(__name__, template_folder='templates')  # 指定模板資料夾為 'templates'
auth = HTTPBasicAuth()  # 初始化 HTTP 基礎認證

# 用戶認證資料
users = {
    "0961282056": generate_password_hash("0961282056"),  # 用戶名和加密後的密碼
    "user": generate_password_hash("user")
}

@auth.verify_password
def verify_password(username: str, password: str) -> Optional[str]:
    """驗證用戶名和密碼
    Args:
        username: 輸入的用戶名
        password: 輸入的密碼
    Returns:
        如果驗證成功返回用戶名，否則返回 None
    """
    return username if username in users and check_password_hash(users[username], password) else None

def send_line_message(to: str, message_data: Dict) -> Tuple[Dict, int]:
    """發送 LINE 訊息到指定用戶
    Args:
        to: 訊息接收者的 ID
        message_data: 要發送的訊息內容字典
    Returns:
        Tuple 包含回應狀態字典和 HTTP 狀態碼
    """
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_TOKEN}'  # 使用 Bearer Token 進行認證
    }
    
    try:
        response = requests.post(LINE_API_URL, headers=headers, json=message_data)  # 發送 POST 請求
        response.raise_for_status()  # 如果狀態碼不是 200 系列，拋出異常
        return {"status": "success"}, 200
    except requests.RequestException as e:  # 捕獲所有 requests 相關的異常
        return {"status": "error", "message": str(e)}, 500

@app.route('/send-line-message', methods=['POST'])
def handle_line_message():
    """處理來自前端的 LINE 訊息發送請求"""
    data = request.get_json()  # 獲取 JSON 格式的請求資料
    
    # 檢查資料是否有效
    if not data or not isinstance(data, dict) or 'to' not in data:
        return jsonify({"status": "error", "message": "'to' field is required"}), 400
        
    return jsonify(*send_line_message(data['to'], data))  # 解包並回傳結果

def parse_date_time(anime: Dict) -> Tuple[int, datetime]:
    """解析動畫的首播日期和時間，用於排序
    Args:
        anime: 包含動畫資訊的字典
    Returns:
        Tuple 包含星期數字和時間物件
    """
    try:
        weekday = WEEKDAY_MAP.get(anime['premiere_date'], 7)  # 默認為星期日（7）如果日期無效
        premiere_time = datetime.strptime(anime['premiere_time'], "%H:%M")  # 將時間字符串轉為 datetime 物件
        return weekday, premiere_time
    except (ValueError, KeyError):  # 處理時間格式錯誤或鍵缺失的情況
        return 7, datetime.max  # 返回最大值確保排在最後

def fetch_anime_data(year: str, season: str) -> List[Dict]:
    """從指定網站抓取動畫資料並排序
    Args:
        year: 要查詢的年份
        season: 要查詢的季節（冬、春、夏、秋）
    Returns:
        排序後的動畫資料列表，或錯誤訊息列表
    """
    if season not in SEASON_TO_MONTH:  # 驗證季節是否有效
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]
    
    month = SEASON_TO_MONTH[season]  # 將季節轉換為對應月份
    url = f"https://acgsecrets.hk/bangumi/{year}{month:02d}/"  # 構建目標 URL
    
    try:
        response = requests.get(url, timeout=10)  # 設置 10 秒超時
        response.raise_for_status()  # 檢查請求是否成功
        response.encoding = 'utf-8'  # 強制設置 UTF-8 編碼
        
        soup = BeautifulSoup(response.text, 'html.parser')  # 解析 HTML
        anime_data = soup.find('div', id='acgs-anime-icons')  # 查找動畫資料區塊
        
        if not anime_data:  # 如果沒找到資料
            return [{"error": "未找到任何動畫資料"}]
            
        anime_list = []
        for item in anime_data.find_all('div', class_='CV-search'):  # 遍歷每個動畫項目
            anime_list.append({
                'bangumi_id': item.get('acgs-bangumi-data-id', "未知ID"),  # 獲取動畫 ID
                'anime_name': (item.find('div', class_='anime_name').text.strip() 
                              if item.find('div', class_='anime_name') else "無名稱"),  # 動畫名稱
                'anime_image_url': (item.find('div', class_='overflow-hidden anime_bg')
                                  .img['src'] if item.find('div', class_='overflow-hidden anime_bg') 
                                  and item.find('div', class_='overflow-hidden anime_bg').img else "無圖片"),  # 圖片 URL
                'premiere_date': (item.find('div', class_='day').text.strip() 
                                if item.find('div', class_='day') else "無首播日期"),  # 首播日期
                'premiere_time': (item.find('div', class_='time').text.strip() 
                                if item.find('div', class_='time') else "無首播時間")  # 首播時間
            })
        
        return sorted(anime_list, key=parse_date_time)  # 按日期時間排序後返回
        
    except requests.RequestException:  # 處理網絡請求失敗
        return [{"error": "無法從網站獲取資料，請檢查網站是否正確"}]

def get_current_season(month: int) -> str:
    """根據當前月份確定季節
    Args:
        month: 月份數字 (1-12)
    Returns:
        對應的季節名稱
    """
    if 1 <= month <= 3: return "冬"
    if 4 <= month <= 6: return "春"
    if 7 <= month <= 9: return "夏"
    return "秋"

@app.route('/', methods=['GET', 'POST'])
@auth.login_required
def index():
    """處理主頁面的 GET 和 POST 請求，顯示動畫查詢表單和結果"""
    now = datetime.now()  # 獲取當前時間
    years = [str(now.year + i) for i in range(1, 2)] + [str(now.year - i) for i in range(0, 8)]  # 生成年份選項（前7年+當前+明年）
    default_season = get_current_season(now.month)  # 根據當前月份設置默認季節
    
    # 初始化渲染上下文
    context = {
        'years': years,
        'sorted_anime_list': None,  # 動畫列表初始為空
        'error_message': None,  # 錯誤訊息初始為空
        'selected_season': default_season,  # 預設選中當前季節
        'selected_year': str(now.year),  # 預設選中當前年份
        'premiere_date': '全部'  # 預設顯示所有首播日期
    }
    
    if request.method == 'POST':  # 處理表單提交
        context['selected_year'] = request.form.get('year', str(now.year))  # 獲取選擇的年份
        context['selected_season'] = request.form.get('season', default_season)  # 獲取選擇的季節
        context['premiere_date'] = request.form.get('premiere_date', '全部')  # 獲取選擇的首播日期
        
        if not context['selected_year'].isdigit() or len(context['selected_year']) != 4:  # 驗證年份格式
            context['error_message'] = "請輸入有效的年份（例如：2024）"
        else:
            context['sorted_anime_list'] = fetch_anime_data(context['selected_year'], context['selected_season'])
            if context['premiere_date'] != "全部":  # 過濾特定首播日期
                context['sorted_anime_list'] = [
                    anime for anime in context['sorted_anime_list'] 
                    if anime.get('premiere_date') == context['premiere_date']
                ]
            if context['sorted_anime_list'] and 'error' in context['sorted_anime_list'][0]:  # 檢查是否有錯誤
                context['error_message'] = context['sorted_anime_list'][0]['error']
                context['sorted_anime_list'] = None
    
    return render_template('index.html', **context)  # 渲染模板並傳遞上下文

if __name__ == '__main__':
    # 啟動 Flask 應用
    app.run(debug=True, host='0.0.0.0', port=5000)  # debug 模式，監聽所有 IP，端口 5000