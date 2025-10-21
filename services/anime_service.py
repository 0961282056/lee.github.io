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

load_dotenv()  # 載入 .env

# --- 日誌設定 ---
logging.basicConfig(level=logging.INFO)
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

def parse_date_time(anime: Dict) -> Tuple[int, datetime]:
    """解析動畫的首播日期和時間，用於排序。"""
    try:
        if anime['premiere_date'] == "無首播日期":
            return 7, datetime.max
        weekday = WEEKDAY_MAP.get(anime['premiere_date'], 7)
        premiere_time = datetime.strptime(anime['premiere_time'], "%H:%M")
        return weekday, premiere_time
    except (ValueError, KeyError):
        return 7, datetime.max

def upload_to_cloudinary(image_url: str, cache: Cache = None) -> str:
    """上傳圖片到 Cloudinary，返回永久 URL。強制上傳，除非快取命中。"""
    logger.info(f"[DEBUG] 開始上傳圖片: {image_url[:50]}...")
    if image_url == "無圖片":
        logger.info(f"[DEBUG] 無圖片，返回 '無圖片'")
        return "無圖片"

    # 先用 image_url 作為 key 快取（避免重下載）
    url_cache_key = f"image_url_cache_{hash(image_url)}"
    cached_url = cache.get(url_cache_key) if cache else None
    logger.info(f"[DEBUG] URL 快取檢查: 命中={bool(cached_url)}")
    if cached_url:
        logger.info(f"[DEBUG] URL 快取命中，返回: {cached_url[:50]}...")
        return cached_url

    try:
        # 下載圖片到記憶體
        logger.info(f"[DEBUG] 開始下載圖片: {image_url[:50]}...")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = session.get(image_url, timeout=5, headers=headers)
        response.raise_for_status()
        logger.info(f"[DEBUG] 下載成功，狀態碼: {response.status_code}")

        # 計算 MD5 hash 作為 public_id
        logger.info(f"[DEBUG] 開始計算 hash...")
        content_hash = hashlib.md5(response.content).hexdigest()
        public_id = f"anime_covers/{content_hash}"
        logger.info(f"[DEBUG] Hash 完成: {content_hash[:16]}..., public_id: {public_id}")

        # 檢查內容 hash 快取（Cloudinary URL）
        content_cache_key = f"cloudinary_url_{content_hash}"
        cached_cloudinary_url = cache.get(content_cache_key) if cache else None
        logger.info(f"[DEBUG] 內容快取檢查: 命中={bool(cached_cloudinary_url)}")
        if cached_cloudinary_url:
            cache.set(url_cache_key, cached_cloudinary_url, timeout=3600 * 24)  # 同步快取
            logger.info(f"[DEBUG] 內容快取命中，返回 Cloudinary URL: {cached_cloudinary_url[:50]}...")
            return cached_cloudinary_url

        # 強制上傳到 Cloudinary（移除 fallback，直接上傳）
        logger.info(f"[DEBUG] 進入上傳流程...")
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
        logger.info(f"[DEBUG] 上傳成功: {upload_result.get('public_id', 'N/A')}")

        # 生成 Cloudinary URL
        url, options = cloudinary.utils.cloudinary_url(
            upload_result['public_id'],
            fetch_format='jpg',
            quality=95,
            width=300,
            height=300,
            crop='limit'
        )
        logger.info(f"[DEBUG] Cloudinary URL 生成: {url[:50]}...")

        # 快取結果
        if cache:
            cache.set(content_cache_key, url, timeout=3600 * 24)
            cache.set(url_cache_key, url, timeout=3600 * 24)
            logger.info(f"[DEBUG] 快取設定完成")

        logger.info(f"[DEBUG] 上傳流程結束，返回 Cloudinary URL")
        return url

    except (requests.RequestException, Exception) as e:
        logger.error(f"[ERROR] 上傳失敗: {image_url[:50]}..., 錯誤: {e}")
        logger.info(f"[DEBUG] 返回原 URL 作為 fallback")
        return image_url
    
def process_anime_item(item, cache: Cache = None) -> Dict:
    """單一動畫項目的處理邏輯（用於並行）。"""
    premiere_date_elem = item.find('div', class_='day')
    premiere_date = premiere_date_elem.text.strip().replace("星期", "") if premiere_date_elem else "無首播日期"

    image_tag = item.find('div', class_='overflow-hidden anime_bg')
    image_url = image_tag.img['src'] if image_tag and image_tag.img else "無圖片"
    anime_image_url = upload_to_cloudinary(image_url, cache)

    # 最終 URL 類型檢查
    if "cloudinary" in anime_image_url.lower():
        url_type = "Cloudinary (上傳成功)"
    else:
        url_type = "原 URL (fallback/錯誤)"
    logger.info(f"[DEBUG] 最終 URL: {image_url[:50]}... -> {anime_image_url[:50]}... (類型: {url_type})")

    anime_name_elem = item.find('div', class_='anime_name')
    time_elem = item.find('div', class_='time')

    result = {
        'bangumi_id': item.get('acgs-bangumi-data-id', "未知ID"),
        'anime_name': anime_name_elem.text.strip() if anime_name_elem else "無名稱",
        'anime_image_url': anime_image_url,
        'premiere_date': premiere_date,
        'premiere_time': time_elem.text.strip() if time_elem else "無首播時間"
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

        anime_data = soup.find('div', id='acgs-anime-icons')
        if not anime_data:
            return [{"error": "未找到任何動畫資料"}]

        anime_items = anime_data.find_all('div', class_='CV-search')

        # 並行處理（降低 max_workers 到 6，減少併發壓力）
        anime_list = []
        parallel_start_time = time.time()  # 新增：記錄並行處理開始時間
        logger.info(f"[PARALLEL] 開始並行處理 {len(anime_items)} 筆資料，max_workers=6")
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
        parallel_end_time = time.time()  # 新增：記錄並行處理結束時間
        parallel_duration = parallel_end_time - parallel_start_time
        logger.info(f"[PARALLEL] 並行處理完成，處理 {len(anime_list)} 筆資料，耗時 {parallel_duration:.2f} 秒")

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