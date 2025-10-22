from typing import List, Dict, Tuple
from datetime import datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests
import os
from flask_caching import Cache
from config import Config
import cloudinary  # 新增：Cloudinary SDK
import cloudinary.uploader  # 新增
from dotenv import load_dotenv  # 確保載入
import hashlib  # 新增：MD5 hash
import io       # 新增：BytesIO
from PIL import Image  # 選用：圖片驗證
from concurrent.futures import ThreadPoolExecutor, as_completed  # 新增：並行處理
from requests.adapters import HTTPAdapter  # 新增：連接池
from urllib3.util.retry import Retry       # 新增：重試策略
import time                               # 新增：微延遲
import threading                          # 新增：用於顯示 thread 名稱
import logging                           # 新增：日誌
import re

load_dotenv()  # 載入 .env

# --- 日誌設定 ---
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# 新增：全域 Session 與連接池設定
session = requests.Session()
retry_strategy = Retry(
    total=3,                    # 最多重試 3 次
    backoff_factor=0.5,         # 延遲成長（0.5s, 1s, 2s）
    status_forcelist=[429, 500, 502, 503, 504]  # 率限/伺服器錯誤重試
)
adapter = HTTPAdapter(
    pool_connections=20,        # 增到 20
    pool_maxsize=20,            # 增到 20
    max_retries=retry_strategy
)
session.mount("http://", adapter)
session.mount("https://", adapter)

# 新增：專門為 Cloudinary 設定更大的連接池 adapter（修復 pool size=1 問題）
cloudinary_adapter = HTTPAdapter(
    pool_connections=50,        # 更大池子，應付併發
    pool_maxsize=50,
    max_retries=retry_strategy
)
session.mount("https://api.cloudinary.com", cloudinary_adapter)  # 針對 Cloudinary API

# Cloudinary 配置（加優化）– 新增: 共享 session 解決連接池問題
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
    long_url_signature=True,  # 新增：長 URL 簽名，優化上傳
    secure=True,              # 新增：HTTPS 強制
    http_client=session       # 新增：共享全域 session，使用大連接池
)

SEASON_TO_MONTH = Config.SEASON_TO_MONTH
WEEKDAY_MAP = Config.WEEKDAY_MAP

def parse_date_time(anime: Dict) -> Tuple[int, float]:
    """
    解析動畫的首播日期和時間，用於排序。
    優化：不轉換深夜時間，直接將時間轉為浮點數 (e.g., "25:30" -> 25.5)，讓 >24 的自然排在正常時間之後。
    "無" 時間排到星期內最早 (0.0)；無日期排最後。
    注意：key 改為 (int, float)，以支援時間浮點比較。
    """
    try:
        if anime['premiere_date'] == "無首播日期":
            return 8, float('inf')  # 無日期排最後 (inf > 任何時間)
        weekday = WEEKDAY_MAP.get(anime['premiere_date'], 7)  # 預設 7 (未知)
        if anime['premiere_time'] == "無":
            return weekday, 0.0  # 無時間排到星期內最早
        
        # 解析時間為浮點數 (HH.MM)，支援 >24
        time_match = re.match(r'(\d{1,2}):(\d{2})', anime['premiere_time'])
        if not time_match:
            raise ValueError(f"無效時間格式: {anime['premiere_time']}")
        
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        time_float = hour + (minute / 60.0)  # e.g., "25:30" -> 25.5
        
        # 驗證 (允許 >24)
        if not (0 <= minute <= 59):
            raise ValueError(f"無效分鐘: {minute}")
        
        return weekday, time_float
        
    except (ValueError, KeyError) as e:
        # 記錄錯誤以診斷（生產環境可移除）
        logger.warning(f"排序解析錯誤: {anime.get('premiere_date', 'N/A')} - {anime.get('premiere_time', 'N/A')}, 錯誤: {e}")
        return 7, float('inf')  # fallback (未知排最後)

def upload_to_cloudinary(image_url: str, cache: Cache = None) -> str:
    """上傳圖片到 Cloudinary，返回永久 URL。強制上傳，除非快取命中。"""
    if image_url == "無圖片":
        return "無圖片"

    # 先用 image_url 作為 key 快取（避免重下載）
    url_cache_key = f"image_url_cache_{hash(image_url)}"
    cached_url = cache.get(url_cache_key) if cache else None
    if cached_url:
        return cached_url

    try:
        # 下載圖片到記憶體
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = session.get(image_url, timeout=5, headers=headers)
        response.raise_for_status()

        # 計算 MD5 hash 作為 public_id
        content_hash = hashlib.md5(response.content).hexdigest()
        public_id = f"anime_covers/{content_hash}"

        # 檢查內容 hash 快取（Cloudinary URL）
        content_cache_key = f"cloudinary_url_{content_hash}"
        cached_cloudinary_url = cache.get(content_cache_key) if cache else None
        if cached_cloudinary_url:
            cache.set(url_cache_key, cached_cloudinary_url, timeout=3600 * 24)  # 同步快取
            return cached_cloudinary_url

        # 強制上傳到 Cloudinary（移除 fallback，直接上傳）
        time.sleep(0.05)  # 增加微延遲到 0.05s 防率限（從 0.02 調整）

        upload_result = cloudinary.uploader.upload(
            response.content,
            public_id=public_id,
            overwrite=True,
            invalidate=True,
            transformation=[
                {
                    "width": 300,
                    "height": 300,
                    "crop": "limit",
                    "quality": 95
                }
            ]
        )

        # 生成 Cloudinary URL
        url, options = cloudinary.utils.cloudinary_url(
            upload_result['public_id'],
            fetch_format='jpg',
            quality=95,
            width=300,
            height=300,
            crop='limit'
        )

        # 快取結果
        if cache:
            cache.set(content_cache_key, url, timeout=3600 * 24)
            cache.set(url_cache_key, url, timeout=3600 * 24)

        return url

    except (requests.RequestException, Exception) as e:
        logger.error(f"[ERROR] 上傳失敗: {image_url[:50]}..., 錯誤: {e}")
        return image_url

def process_anime_item(item, cache: Cache = None) -> Dict:
    """
    單一動畫項目的處理邏輯（用於並行）。
    提取首播日期、時間、圖片 URL、名稱和故事大綱。
    """
    # 提取首播資訊（日期和時間）
    premiere_date_elem = item.find('div', {'class': 'time_today main_time'})
    premiere_date = "無首播日期"
    premiere_time = "無首播時間"
    
    if premiere_date_elem:
        text = premiere_date_elem.get_text(strip=True)
        
        # 提取星期（每週[一二三四五六日天]）
        week_match = re.search(r'每週([一二三四五六日天])', text)
        week_day = week_match.group(1) if week_match else None
        
        # 提取時間（\d{1,2}時\d{1,2}分）
        time_match = re.search(r'(\d{1,2})時(\d{1,2})分', text)
        if time_match:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            premiere_time = f"{hour:02d}:{minute:02d}"
        
        if week_day:
            premiere_date = week_day

    # 提取圖片 URL
    image_tag = item.find('div', {'class': 'overflow-hidden anime_cover_image'})
    image_url = image_tag.img['src'] if image_tag and image_tag.img else "無圖片"
    anime_image_url = upload_to_cloudinary(image_url, cache)

    # 提取動畫名稱
    anime_name_elem = item.find('h3', {'class': 'entity_localized_name'})
    anime_name = anime_name_elem.get_text(strip=True) if anime_name_elem else "無名稱"

    # 提取故事大綱
    story_elem = item.find('div', {'class': 'anime_story'})
    story = story_elem.get_text(strip=True) if story_elem else "無故事大綱"

    # 建構結果
    result = {
        'bangumi_id': item.get('acgs-bangumi-data-id', "未知ID"),
        'anime_name': anime_name,
        'anime_image_url': anime_image_url,
        'premiere_date': premiere_date,
        'premiere_time': premiere_time,
        'story': story
    }
    return result

def fetch_anime_data(year: str, season: str, cache: Cache = None) -> List[Dict]:
    """從網站爬取動畫資料，並排序。支援快取。"""
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]

    full_cache_key = f"anime_full_{year}_{season}"
    if cache and cache.get(full_cache_key):
        return cache.get(full_cache_key)

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = session.get(url, timeout=10, headers=headers)
        response.raise_for_status()

        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        anime_data = soup.find('div', id='acgs-anime-list')
        if not anime_data:
            return [{"error": "未找到任何動畫資料"}]

        anime_items = anime_data.find_all('div', class_='CV-search')

        # 並行處理（降低 max_workers 到 6，減少併發壓力）
        anime_list = []
        with ThreadPoolExecutor(max_workers=6) as executor:  # 從 10 降到 6
            future_to_item = {}
            for idx, item in enumerate(anime_items):
                future = executor.submit(process_anime_item, item, cache)
                future_to_item[future] = idx

            for future in as_completed(future_to_item):
                try:
                    result = future.result()
                    anime_list.append(result)
                except Exception as exc:
                    pass  # 忽略單項錯誤

        # 排序
        try:
            sorted_list = sorted(anime_list, key=parse_date_time)
        except Exception as e:
            sorted_list = anime_list

        if cache:
            cache.set(full_cache_key, sorted_list, timeout=3600)

        return sorted_list

    except requests.RequestException as e:
        return [{"error": "無法從網站獲取資料，請檢查網站是否正確"}]
    
def get_current_season(month: int) -> str:
    """根據月份返回當前季節。"""
    if 1 <= month <= 3: return "冬"
    if 4 <= month <= 6: return "春"
    if 7 <= month <= 9: return "夏"
    return "秋"