from flask import Flask, render_template, request, jsonify
from flask_httpauth import HTTPBasicAuth
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from datetime import datetime
import requests
import os

from config import Config
from typing import List, Dict, Optional, Tuple

# --- 環境設置 ---
load_dotenv()

# --- 應用程式初始化 ---
app = Flask(__name__, template_folder='templates', static_folder='static')
auth = HTTPBasicAuth()
cache = Cache(app, config={'CACHE_TYPE': 'simple'})
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"])

# --- 使用者憑證 ---
users = {
    os.getenv('USERNAME_1'): generate_password_hash(os.getenv('PASSWORD_1')),
    os.getenv('USERNAME_2'): generate_password_hash(os.getenv('PASSWORD_2'))
}

# --- 常數和配置 ---
IMAGE_DIR = os.path.join('static', 'images')
os.makedirs(IMAGE_DIR, exist_ok=True)
SEASON_TO_MONTH = Config.SEASON_TO_MONTH
WEEKDAY_MAP = Config.WEEKDAY_MAP

# --- 身份驗證 ---
@auth.verify_password
def verify_password(username: str, password: str) -> Optional[str]:
    if username in users and check_password_hash(users[username], password):
        return username
    return None

# --- 工具函數 ---
def parse_date_time(anime: Dict) -> Tuple[int, datetime]:
    try:
        weekday = WEEKDAY_MAP.get(anime['premiere_date'], 7)
        premiere_time = datetime.strptime(anime['premiere_time'], "%H:%M")
        return weekday, premiere_time
    except (ValueError, KeyError):
        return 7, datetime.max

def download_image(image_url: str) -> str:
    try:
        parsed_url = urlparse(image_url)
        filename = os.path.basename(parsed_url.path)
        local_path = os.path.join(IMAGE_DIR, filename)

        if os.path.exists(local_path):
            return f"/static/images/{filename}"

        response = requests.get(image_url, timeout=5)
        response.raise_for_status()
        with open(local_path, 'wb') as f:
            f.write(response.content)
        return f"/static/images/{filename}"

    except requests.RequestException as e:
        print(f"圖片下載失敗: {image_url}, 錯誤: {e}")
        return "無圖片"

@cache.cached(timeout=3600, key_prefix=lambda: f"anime_{year}_{season}")
def fetch_anime_data(year: str, season: str) -> List[Dict]:
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
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
            premiere_date = item.find('div', class_='day')
            premiere_date = premiere_date.text.strip().replace("星期", "") if premiere_date else "無首播日期"

            image_tag = item.find('div', class_='overflow-hidden anime_bg')
            image_url = image_tag.img['src'] if image_tag and image_tag.img else "無圖片"
            anime_image_url = download_image(image_url) if image_url != "無圖片" else "無圖片"

            anime_list.append({
                'bangumi_id': item.get('acgs-bangumi-data-id', "未知ID"),
                'anime_name': item.find('div', class_='anime_name').text.strip() if item.find('div', class_='anime_name') else "無名稱",
                'anime_image_url': anime_image_url,
                'premiere_date': premiere_date,
                'premiere_time': item.find('div', class_='time').text.strip() if item.find('div', class_='time') else "無首播時間"
            })

        return sorted(anime_list, key=parse_date_time)

    except requests.RequestException as e:
        print(f"請求失敗: {e}")
        return [{"error": "無法從網站獲取資料，請檢查網站是否正確"}]

def get_current_season(month: int) -> str:
    if 1 <= month <= 3: return "冬"
    if 4 <= month <= 6: return "春"
    if 7 <= month <= 9: return "夏"
    return "秋"

# --- 路由 ---
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

        if not context['selected_year'].isdigit() or len(context['selected_year']) != 4:
            context['error_message'] = "請輸入有效的年份（例如：2024）"
        else:
            cache.delete(f"anime_{context['selected_year']}_{context['selected_season']}")
            anime_list = fetch_anime_data(context['selected_year'], context['selected_season'])

            if anime_list and 'error' in anime_list[0]:
                context['error_message'] = anime_list[0]['error']
            else:
                if context['premiere_date'] != "全部":
                    anime_list = [a for a in anime_list if a.get('premiere_date') == context['premiere_date']]
                context['sorted_anime_list'] = anime_list

    return render_template('index.html', **context)

# --- 入口點 ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=os.getenv('FLASK_DEBUG', 'False') == 'True')