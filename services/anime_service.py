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
import logging  # 新增：日誌
from concurrent.futures import ThreadPoolExecutor, as_completed  # 新增：並行處理
from requests.adapters import HTTPAdapter  # 新增：連接池
from urllib3.util.retry import Retry       # 新增：重試策略
import time                               # 新增：微延遲
import threading                          # 新增：用於顯示 thread 名稱

load_dotenv()  # 載入 .env

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

# --- 日誌設定 ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cloudinary 配置（加優化）
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET'),
    long_url_signature=True,  # 新增：長 URL 簽名，優化上傳
    secure=True               # 新增：HTTPS 強制
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
    """上傳圖片到 Cloudinary，返回永久 URL。盡量與原圖一致。"""
    if image_url == "無圖片":
        return "無圖片"

    # 先用 image_url 作為 key 快取（避免重下載）
    url_cache_key = f"image_url_cache_{hash(image_url)}"  # 用 hash 避免長 key
    cached_url = cache.get(url_cache_key) if cache else None
    if cached_url:
        return cached_url

    try:
        # 先下載圖片到記憶體，加 User-Agent 防擋（用 session）
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = session.get(image_url, timeout=5, headers=headers)  # 用 session
        response.raise_for_status()

        # 條件 fallback - 若下載成功，直接用原 URL 避上傳 (加速首次)
        if response.status_code == 200 and not cached_url:
            cache.set(url_cache_key, image_url, timeout=0) if cache else None  # 永久快取原 URL
            return image_url

        # 計算 MD5 hash 作為 public_id，避免重複
        content_hash = hashlib.md5(response.content).hexdigest()
        public_id = f"anime_covers/{content_hash}"

        # 檢查內容 hash 快取
        content_cache_key = f"cloudinary_url_{content_hash}"
        cached_url = cache.get(content_cache_key) if cache else None
        if cached_url:
            cache.set(url_cache_key, cached_url, timeout=3600 * 24) if cache else None  # 同步到 URL cache
            return cached_url

        # 微延遲防率限/池滿
        time.sleep(0.02)

        # 上傳到 Cloudinary（最小處理）
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

        # 返回 URL
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
            cache.set(url_cache_key, url, timeout=3600 * 24)  # 雙層快取
        return url

    except (requests.RequestException, Exception) as e:
        logger.error(f"Cloudinary 上傳失敗: {image_url}, 錯誤: {e}")
        return image_url
    
def process_anime_item(item, cache: Cache = None) -> Dict:
    """單一動畫項目的處理邏輯（用於並行）。"""
    item_start = time.time()  # 新增：單項計時
    thread_name = threading.current_thread().name  # 新增：顯示 thread 名稱
    logger.info(f"開始處理單項: {item.get('acgs-bangumi-data-id', '未知ID')} (thread: {thread_name})")
    premiere_date_elem = item.find('div', class_='day')
    premiere_date = premiere_date_elem.text.strip().replace("星期", "") if premiere_date_elem else "無首播日期"

    image_tag = item.find('div', class_='overflow-hidden anime_bg')
    image_url = image_tag.img['src'] if image_tag and image_tag.img else "無圖片"
    anime_image_url = upload_to_cloudinary(image_url, cache)  # 呼叫上傳

    anime_name_elem = item.find('div', class_='anime_name')
    time_elem = item.find('div', class_='time')

    result = {
        'bangumi_id': item.get('acgs-bangumi-data-id', "未知ID"),
        'anime_name': anime_name_elem.text.strip() if anime_name_elem else "無名稱",
        'anime_image_url': anime_image_url,
        'premiere_date': premiere_date,
        'premiere_time': time_elem.text.strip() if time_elem else "無首播時間"
    }
    logger.info(f"單項處理完成: {result['anime_name'][:20]}... 時間: {time.time() - item_start:.2f}s (thread: {thread_name})")
    return result

def fetch_anime_data(year: str, season: str, cache: Cache = None) -> List[Dict]:
    """從網站爬取動畫資料，並排序。支援快取。"""
    overall_start = time.time()  # 新增：整體計時
    logger.info(f"開始查詢: {year} {season}")
    if season not in SEASON_TO_MONTH:
        logger.error(f"季節無效: {season}")
        return [{"error": "季節無效，請輸入有效季節（冬、春、夏、秋）"}]

    full_cache_key = f"anime_full_{year}_{season}"
    cache_check_start = time.time()  # 新增：快取檢查計時
    if cache and cache.get(full_cache_key):
        logger.info(f"快取命中: {year} {season}, 檢查時間: {time.time() - cache_check_start:.2f}s")
        return cache.get(full_cache_key)

    logger.info(f"無快取，開始爬取: {year} {season}")

    url = f"https://acgsecrets.hk/bangumi/{year}{SEASON_TO_MONTH[season]:02d}/"
    try:
        request_start = time.time()  # 新增：請求計時
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = session.get(url, timeout=10, headers=headers)  # 用 session 優化網站請求
        response.raise_for_status()
        logger.info(f"網站請求完成: {url}, 請求時間: {time.time() - request_start:.2f}s")

        parse_start = time.time()  # 新增：解析計時
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        anime_data = soup.find('div', id='acgs-anime-icons')
        if not anime_data:
            logger.warning("未找到 anime_data div")
            return [{"error": "未找到任何動畫資料"}]

        anime_items = anime_data.find_all('div', class_='CV-search')
        logger.info(f"HTML 解析完成: {len(anime_items)} 項, 解析時間: {time.time() - parse_start:.2f}s")

        # 並行處理（調到 10，啟用更高並行）
        process_start = time.time()  # 新增：處理計時
        anime_list = []
        item_count = 0
        active_futures = []  # 新增：追蹤活躍任務數，用於 log
        logger.info(f"開始並行處理: {len(anime_items)} 項, worker 數: 10, 開始時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        
        submit_start = time.time()
        with ThreadPoolExecutor(max_workers=10) as executor:  # 調到 10
            # 提交所有任務（簡化 log，只記錄總提交時間）
            future_to_item = {}
            for idx, item in enumerate(anime_items):
                future = executor.submit(process_anime_item, item, cache)
                future_to_item[future] = idx  # 存 index 方便 log
                active_futures.append(future)
            logger.info(f"所有 {len(anime_items)} 任務提交完成, 提交時間: {time.time() - submit_start:.2f}s")

            # 處理完成任務（保留完成 log 以證明並行）
            for future in as_completed(future_to_item):
                try:
                    result = future.result()
                    anime_list.append(result)
                    item_count += 1
                    idx = future_to_item[future]
                    complete_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    # 計算單項處理時間（從 future 內的計時，或近似）
                    process_time = time.time() - process_start  # 近似；實際單項在 process_anime_item 有 log
                    thread_name = threading.current_thread().name  # 完成時的 thread 名稱
                    logger.info(f"任務 {idx} 完成 (thread: {thread_name}), 處理時間: {process_time:.2f}s (近似), 完成時間: {complete_time}")
                    
                    # 更新活躍任務數
                    if future in active_futures:
                        active_futures.remove(future)
                    
                    # 進度 log：每 10 項一次，包含活躍數（簡化頻率）
                    if item_count % 10 == 0:
                        active_count = len(active_futures)
                        logger.info(f"已處理 {item_count} / {len(anime_items)} 項, 並行活躍任務: {active_count}, 當前時間: {time.time() - process_start:.2f}s")
                        
                except Exception as exc:
                    idx = future_to_item.get(future, '未知')
                    thread_name = threading.current_thread().name
                    logger.error(f"任務 {idx} 失敗 (thread: {thread_name}): {exc}")

        # 檢查剩餘活躍（應為 0）
        if active_futures:
            logger.warning(f"並行結束時仍有 {len(active_futures)} 活躍任務（異常）")
        
        logger.info(f"並行處理完成: {len(anime_list)} 項, 總時間: {time.time() - process_start:.2f}s (預期加速 ~4-5x with 10 workers)")

        # 排序
        sort_start = time.time()  # 新增：排序計時
        try:
            sorted_list = sorted(anime_list, key=parse_date_time)
        except Exception as e:
            logger.error(f"排序錯誤: {e}")
            sorted_list = anime_list
        logger.info(f"排序完成: 時間: {time.time() - sort_start:.2f}s")

        cache_set_start = time.time()  # 新增：快取設定計時
        if cache:
            cache.set(full_cache_key, sorted_list, timeout=3600)
            logger.info(f"快取設定完成: 時間: {time.time() - cache_set_start:.2f}s")

        logger.info(f"整體查詢完成: {year} {season}, 總時間: {time.time() - overall_start:.2f}s")
        return sorted_list

    except requests.RequestException as e:
        logger.error(f"請求失敗: {e}")
        return [{"error": "無法從網站獲取資料，請檢查網站是否正確"}]
    
def get_current_season(month: int) -> str:
    """根據月份返回當前季節。"""
    if 1 <= month <= 3: return "冬"
    if 4 <= month <= 6: return "春"
    if 7 <= month <= 9: return "夏"
    return "秋"