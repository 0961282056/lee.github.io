from flask import Flask, render_template, request
import requests
from bs4 import BeautifulSoup
from datetime import datetime

app = app = Flask(__name__, template_folder='templates')

# 定義排序邏輯
def parse_date_time(anime):
    """根據日期與時間進行排序"""
    weekday_map = {'日': 7, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6}
    try:
        weekday = weekday_map.get(anime['premiere_date'], 7)  # 無效日期排最後
        premiere_time = datetime.strptime(anime['premiere_time'], "%H:%M")
        return (weekday, premiere_time)
    except ValueError:
        return (7, datetime.max)

# 定義函式來抓取資料
def fetch_anime_data(year, season):
    # 季節對應的月份
    season_to_month = {
        '冬': 1,  # 1月
        '春': 4,  # 4月
        '夏': 7,  # 7月
        '秋': 10  # 10月
    }

    # 檢查季節是否有效
    if season not in season_to_month:
        return "季節無效，請輸入有效季節（冬、春、夏、秋）"

    month = season_to_month[season]
    
    # 構建URL
    url = f"https://acgsecrets.hk/bangumi/{year}{month:02d}/"
    response = requests.get(url)
    
    # 強制設置編碼為 UTF-8，若網站使用其他編碼，請調整此處（例如：'big5' 或 'gbk'）    
    response.encoding = 'utf-8'  # 或試試 'big5' / 'gbk' 等編碼格式

    if response.status_code != 200:
        return "無法從網站獲取資料，請檢查網站是否正確"

    soup = BeautifulSoup(response.text, 'html.parser')
    anime_data = soup.find('div', id='acgs-anime-icons')
    if not anime_data:
        return "未找到任何動畫資料"

    anime_items = anime_data.find_all('div', class_='CV-search')
    anime_list = []
    for anime_item in anime_items:
        # 提取動畫詳細資訊
        bangumi_id = anime_item.get('acgs-bangumi-data-id')
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

    # 排序動畫列表
    sorted_anime_list = sorted(anime_list, key=parse_date_time)

    return sorted_anime_list if sorted_anime_list else "未找到任何動畫資料"

# 網頁路由，顯示表單和爬蟲結果
@app.route('/', methods=['GET', 'POST'])
def index():
    error_message = None
    sorted_anime_list = None
    current_year = datetime.now().year  # 取得當前年份
    years = [current_year + i for i in range(1, 2)] + [current_year - i for i in range(0, 21)]  # 生成年份選項
    
    if request.method == 'POST':
        year = request.form['year']
        season = request.form['season']
        premiere_date = request.form.get('premiere_date', '全部')
        
        # 確保輸入的年份是有效的
        if not year.isdigit() or len(year) != 4:
            error_message = "請輸入有效的年份（例如：2024）"
        else:
            sorted_anime_list = fetch_anime_data(year, season)
            
            # 根據 premiere_date 過濾結果
            if premiere_date != "全部":
                sorted_anime_list = [anime for anime in sorted_anime_list if anime['premiere_date'] == premiere_date]

            if isinstance(sorted_anime_list, str):  # 如果返回錯誤訊息，則顯示錯誤訊息
                error_message = sorted_anime_list

    return render_template('index.html', years=years, sorted_anime_list=sorted_anime_list, error_message=error_message, current_year=current_year)

if __name__ == '__main__':
    app.run(debug=True)
