from typing import List, Dict, Tuple
from datetime import datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests
import os
from flask_caching import Cache
from config import Config
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv
import hashlib
import io
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
import threading
import logging
import re
import random

# 載入環境變數
load_dotenv()

# --- 日誌設定 ---
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# --- 全域 requests Session 與連接池設定 ---
pool_size = max(10, os.cpu_count() * 2)
retry_strategy = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504]
)
adapter = HTTPAdapter(
    pool_connections=pool_size,
    pool_maxsize=pool_size,
    max_retries=retry_strategy
)
session = requests.Session()
session.mount("http://", adapter)
session.mount("https://", adapter)

# --- Cloudinary 設定 ---
cloudinary_adapter = HTTPAdapter(
    pool_connections=20,   # 從 50 降為 20，符合 4 workers 實際負載
    pool_maxsize=20,
    max_retries=retry_strategy
)
session.mount("https://api.cloudinary.com", cloudinary_adapter)

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
    long_url_signature=True,
    secure=True,
    http_client=session
)

# --- 常數 ---
SEASON_TO_MONTH = Config.SEASON_TO_MONTH
WEEKDAY_MAP = Config.WEEKDAY_MAP


# ------------------------------------------------------
# 日期與時間排序解析
# ------------------------------------------------------
def parse_date_time(anime: Dict) -> Tuple[int, float]:
    try:
        if anime['premiere_date'] == "無首播日期":
            return 8, float('inf')

        weekday = WEEKDAY_MAP.get(anime['premiere_date'], 7)
        if anime['premiere_time'] == "無":
            return weekday, 0.0

        time_match = re.match(r'(\d{1,2}):(\d{2})', anime['premiere_time'])
        if not time_match:
            raise ValueError(f"無效時間格式: {anime['premiere_time']}")

        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        if not (0 <= minute <= 59):
            raise ValueError(f"無效分鐘: {minute}")

        time_float = hour + (minute / 60.0)
        return weekday, time_float

    except (ValueError, KeyError) as e:
        logger.warning(f"排序解析錯誤: {anime.get('premiere_date', 'N/A')} - {anime.get('premiere_time', 'N/A')}, 錯誤: {e}")
        return 7, float('inf')


# ------------------------------------------------------
# 上傳圖片至 Cloudinary（含快取與防重上傳）
# ------------------------------------------------------
def upload_to_cloudinary(image_url: str, cache: Cache = None) -> str:
    if image_url == "無圖片":
        return "無圖片"

    # 快取鍵
    url_cache_key = f"image_url_cache_{hash(image_url)}"
    cached_url = cache.get(url_cache_key) if cache else None
    if cached_url:
        return cached_url

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = session.get(image_url, timeout=5, headers=headers)
        response.raise_for_status()

        content_hash = hashlib.md5(response.content).hexdigest()
        public_id = f"anime_covers/{content_hash}"

        content_cache_key = f"cloudinary_url_{content_hash}"
        cached_cloudinary_url = cache.get(content_cache_key) if cache else None
        if cached_cloudinary_url:
            cache.set(url_cache_key, cached_cloudinary_url, timeout=3600 * 24)
            return cached_cloudinary_url

        # 防止多執行緒同時上傳同一圖片
        lock_key = f"upload_lock_{content_hash}"
        if cache and cache.get(lock_key):
            time.sleep(0.5)
            cached = cache.get(content_cache_key)
            if cached:
                return cached
        if cache:
            cache.set(lock_key, True, timeout=10)

        # 隨機微延遲
        time.sleep(random.uniform(0.03, 0.08))

        upload_result = cloudinary.uploader.upload(
            response.content,
            public_id=public_id,
            overwrite=True,
            invalidate=True,
            transformation=[
                {"width": 300, "height": 300, "crop": "limit", "quality": 95}
            ]
        )

        url, options = cloudinary.utils.cloudinary_url(
            upload_result['public_id'],
            fetch_format='jpg',
            quality=95,
            width=300,
            height=300,
            crop='limit'
        )

        if cache:
            cache.set(content_cache_key, url, timeout=3600 * 24)
            cache.set(url_cache_key, url, timeout=3600 * 24)

        return url

    except (requests.RequestException, Exception) as e:
        logger.error(f"[ERROR] 上傳失敗: {image_url[:50]}..., 錯誤: {e}")
        if cache:
            cache.set(url_cache_key, image_url, timeout=3600)
        return image_url


# ------------------------------------------------------
# 處理單一動畫項目
# ------------------------------------------------------
def process_anime_item(item, cache: Cache = None) -> Dict:
    premiere_date_elem = item.find('div', {'class': 'time_today main_time'})
    premiere_date = "無首播日期"
    premiere_time = "無首播時間"

    if premiere_date_elem:
        text = premiere_date_elem.get_text(strip=True)
        week_match = re.search(r'每週([一二三四五六日天])', text)
        week_day = week_match.group(1) if week_match else None

        time_match = re.search(r'(\d{1,2})時(\d{1,2})分', text)
        if time_match:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            premiere_time = f"{hour:02d}:{minute:02d}"

        if week_day:
            premiere_date = week_day

    image_tag = item.find('div', {'class': 'overflow-hidden anime_cover_image'})
    image_url = image_tag.img['src'] if image_tag and image_tag.img else "無圖片"
    anime_image_url = upload_to_cloudinary(image_url, cache)

    anime_name_elem = item.find('h3', {'class': 'entity_localized_name'})
    anime_name = anime_name_elem.get_text(strip=True) if anime_name_elem else "無名稱"

    story_elem = item.find('div', {'class': 'anime_story'})
    story = story_elem.get_text(strip=True) if story_elem else "無故事大綱"

    result = {
        'bangumi_id': item.get('acgs-bangumi-data-id', "未知ID"),
        'anime_name': anime_name,
        'anime_image_url': anime_image_url,
        'premiere_date': premiere_date,
        'premiere_time': premiere_time,
        'story': story
    }
    return result


# ------------------------------------------------------
# 主流程：抓取整季動畫資料
# ------------------------------------------------------
def fetch_anime_data(year: str, season: str, cache: Cache = None) -> List[Dict]:
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]

    full_cache_key = f"anime_full_{year}_{season}"
    cached = cache.get(full_cache_key) if cache else None
    if cached:
        return cached

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = session.get(url, timeout=10, headers=headers)
        response.raise_for_status()

        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        anime_data = soup.find('div', id='acgs-anime-list')
        if not anime_data:
            return [{"error": "未找到任何動畫資料"}]

        anime_items = anime_data.find_all('div', class_='CV-search')

        anime_list = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_item = {
                executor.submit(process_anime_item, item, cache): idx
                for idx, item in enumerate(anime_items)
            }

            for future in as_completed(future_to_item):
                try:
                    result = future.result()
                    anime_list.append(result)
                except Exception as exc:
                    logger.warning(f"並行任務失敗: {exc}")

        try:
            sorted_list = sorted(anime_list, key=parse_date_time)
        except Exception as e:
            logger.warning(f"排序失敗，回退未排序: {e}")
            sorted_list = anime_list

        if cache:
            cache.set(full_cache_key, sorted_list, timeout=3600)

        return sorted_list

    except requests.RequestException as e:
        logger.error(f"爬取失敗: {e}")
        return [{"error": "無法從網站獲取資料，請檢查網站是否正確"}]


# ------------------------------------------------------
# 依月份判斷季節
# ------------------------------------------------------
def get_current_season(month: int) -> str:
    if 1 <= month <= 3:
        return "冬"
    if 4 <= month <= 6:
        return "春"
    if 7 <= month <= 9:
        return "夏"
    return "秋"
