from flask import Flask, render_template, request
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime
import os
import logging  # 新增：日誌
from dotenv import load_dotenv  # 載入 .env

from config import Config
from services.anime_service import fetch_anime_data, get_current_season  # 從 services import

# 載入環境變數
load_dotenv()

# --- 日誌設定 ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 環境設置 ---
app = Flask(__name__, template_folder='templates', static_folder='static')
cache = Cache(app, config={'CACHE_TYPE': 'simple'})
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"])

# --- 常數和配置 ---
SEASON_TO_MONTH = Config.SEASON_TO_MONTH
WEEKDAY_MAP = Config.WEEKDAY_MAP

# --- 路由 ---
@app.route('/', methods=['GET', 'POST'])
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

        # 驗證輸入
        if not context['selected_year'].isdigit() or len(context['selected_year']) != 4:
            context['error_message'] = "請輸入有效的年份（例如：2024）"
        elif context['selected_season'] not in ['冬', '春', '夏', '秋']:
            context['error_message'] = "請選擇有效的季節"
        else:
            logger.info(f"查詢 {context['selected_year']} {context['selected_season']} 動畫資料")  # 日誌
            cache_key = f"anime_{context['selected_year']}_{context['selected_season']}"
            cache.delete(cache_key)
            anime_list = fetch_anime_data(context['selected_year'], context['selected_season'], cache)  # 傳入 cache

            if anime_list and 'error' in anime_list[0]:
                context['error_message'] = anime_list[0]['error']
                logger.error(f"爬蟲錯誤: {context['error_message']}")
            else:
                if context['premiere_date'] != "全部":
                    anime_list = [a for a in anime_list if a.get('premiere_date') == context['premiere_date']]
                context['sorted_anime_list'] = anime_list
                logger.info(f"成功載入 {len(anime_list)} 筆動畫資料")

    return render_template('index.html', **context)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=os.getenv('FLASK_DEBUG', 'False') == 'True')