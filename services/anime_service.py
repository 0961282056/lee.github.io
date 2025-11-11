from typing import List, Dict, Tuple
from datetime import datetime
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests
import os
import json
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

# ------------------------------------------------------
# 初始化與設定
# ------------------------------------------------------
load_dotenv()

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# ------------------------------------------------------
# requests Session & Pool 設定（Render 免費版優化）
# ------------------------------------------------------
pool_size = 5  # 減少連線池大小，節省記憶體
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

# ------------------------------------------------------
# Cloudinary 設定（縮減連線池 + 上傳鎖）
# ------------------------------------------------------
cloudinary_adapter = HTTPAdapter(
    pool_connections=4,
    pool_maxsize=4,
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

cloudinary_lock = threading.Semaphore(1)

SEASON_TO_MONTH = Config.SEASON_TO_MONTH
WEEKDAY_MAP = Config.WEEKDAY_MAP

# ------------------------------------------------------
# 簡易快取（Render /tmp/ 目錄）
# ------------------------------------------------------
CACHE_FILE = "/tmp/anime_cache.json"

def load_local_cache() -> Dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_local_cache(data: Dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

# ------------------------------------------------------
# 日期與時間排序解析
# ------------------------------------------------------
def parse_date_time(anime: Dict) -> Tuple[int, float]:
    try:
        if anime['premiere_date'] == "無首播日期":
            return 8, float('inf')

        weekday = WEEKDAY_MAP.get(anime['premiere_date'], 7)
        if anime['premiere_time'] == "無首播時間":
            return weekday, 0.0

        match = re.match(r'(\d{1,2}):(\d{2})', anime['premiere_time'])
        if not match:
            raise ValueError(f"無效時間格式: {anime['premiere_time']}")

        hour, minute = int(match.group(1)), int(match.group(2))
        return weekday, hour + minute / 60.0

    except Exception as e:
        logger.warning(f"排序解析錯誤: {anime.get('premiere_date')} - {anime.get('premiere_time')}，錯誤: {e}")
        return 7, float('inf')

# ------------------------------------------------------
# 上傳圖片至 Cloudinary（含快取 + 鎖）
# ------------------------------------------------------
def upload_to_cloudinary(image_url: str, cache: Cache = None) -> str:
    if image_url == "無圖片":
        return "無圖片"

    local_cache = load_local_cache()
    url_cache_key = f"image_url_{hash(image_url)}"
    if url_cache_key in local_cache:
        return local_cache[url_cache_key]

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = session.get(image_url, timeout=6, headers=headers)
        response.raise_for_status()

        content_hash = hashlib.md5(response.content).hexdigest()
        public_id = f"anime_covers/{content_hash}"

        if f"cloudinary_{content_hash}" in local_cache:
            return local_cache[f"cloudinary_{content_hash}"]

        # 僅允許一次上傳行為
        with cloudinary_lock:
            time.sleep(random.uniform(0.1, 0.25))
            upload_result = cloudinary.uploader.upload(
                response.content,
                public_id=public_id,
                overwrite=True,
                invalidate=True,
                transformation=[
                    {"width": 300, "height": 300, "crop": "limit", "quality": 90}
                ]
            )

        url, _ = cloudinary.utils.cloudinary_url(
            upload_result['public_id'],
            fetch_format='jpg',
            quality=90,
            width=300,
            height=300,
            crop='limit'
        )

        local_cache[url_cache_key] = url
        local_cache[f"cloudinary_{content_hash}"] = url
        save_local_cache(local_cache)
        return url

    except Exception as e:
        logger.error(f"[ERROR] 上傳失敗: {image_url[:50]}..., 錯誤: {e}")
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
            premiere_time = f"{int(time_match.group(1)):02d}:{int(time_match.group(2)):02d}"

        if week_day:
            premiere_date = week_day

    image_tag = item.find('div', {'class': 'overflow-hidden anime_cover_image'})
    image_url = image_tag.img['src'] if image_tag and image_tag.img else "無圖片"
    anime_image_url = upload_to_cloudinary(image_url, cache)

    anime_name_elem = item.find('h3', {'class': 'entity_localized_name'})
    anime_name = anime_name_elem.get_text(strip=True) if anime_name_elem else "無名稱"

    story_elem = item.find('div', {'class': 'anime_story'})
    story = story_elem.get_text(strip=True) if story_elem else "無故事大綱"

    return {
        'bangumi_id': item.get('acgs-bangumi-data-id', "未知ID"),
        'anime_name': anime_name,
        'anime_image_url': anime_image_url,
        'premiere_date': premiere_date,
        'premiere_time': premiere_time,
        'story': story
    }

# ------------------------------------------------------
# 抓取整季動畫資料（Render 版）
# ------------------------------------------------------
def fetch_anime_data(year: str, season: str, cache: Cache = None) -> List[Dict]:
    if season not in SEASON_TO_MONTH:
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]

    local_cache = load_local_cache()
    full_cache_key = f"anime_{year}_{season}"
    if full_cache_key in local_cache:
        return local_cache[full_cache_key]

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = session.get(url, timeout=6, headers=headers)
        response.raise_for_status()

        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'lxml')

        anime_data = soup.find('div', id='acgs-anime-list')
        if not anime_data:
            return [{"error": "未找到任何動畫資料"}]

        anime_items = anime_data.find_all('div', class_='CV-search')
        anime_list = []

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(process_anime_item, item, cache) for item in anime_items]
            for future in as_completed(futures):
                try:
                    anime_list.append(future.result())
                except Exception as exc:
                    logger.warning(f"並行任務失敗: {exc}")

        sorted_list = sorted(anime_list, key=parse_date_time)
        local_cache[full_cache_key] = sorted_list
        save_local_cache(local_cache)

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
