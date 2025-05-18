import os
import sys
import time
import random
import logging
import pickle
import uuid
import json
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# .env 로드
load_dotenv()

# === Configuration ===
INTERVAL = float(os.getenv('CHECK_INTERVAL', 60))
MAX_PAGES = int(os.getenv('MAX_PAGES', 5))
SEEN_IDS_PATH = os.getenv('SEEN_IDS_PATH', 'seen_ids.pkl')
NGL_USERNAME = os.getenv('NGL_USERNAME') or input('NGL 사용자명: ')

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

def load_seen_ids(path: str):
    if not os.path.isfile(path):
        return set(), True
    try:
        with open(path, 'rb') as f:
            return pickle.load(f), False
    except Exception:
        return set(), False


def save_seen_ids(seen_ids, path: str):
    try:
        with open(path, 'wb') as f:
            pickle.dump(seen_ids, f)
    except Exception as e:
        logging.error('seen_ids 저장 실패: %s', e, exc_info=True)


def device_id():
    return str(uuid.uuid4())


def fetch_ticket_listings(page: int):
    today = datetime.now().strftime('%Y-%m-%d')
    # 페이지 인자 적용
    url = (
        f'https://www.ticketbay.co.kr/product/5703/list/0?'
        f'start_perform_date={today}&sale_quantity=4&is_together=YES&page={page}'
    )
    headers = {
        'User-Agent': 'ticketbay-notifier/1.0',
        'Accept': 'text/html',
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logging.error('티켓 목록 조회 실패 (page=%d): %s', page, e, exc_info=True)
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    script = soup.find('script', id='__NEXT_DATA__')
    if not script or not script.string:
        logging.error('__NEXT_DATA__ 스크립트 없음 (page=%d)', page)
        return []

    try:
        data = json.loads(script.string)
        pageProps = data.get('props', {}).get('pageProps', {})
        listings = pageProps.get('listServer', {}).get('content', [])
        if not listings:
            logging.info('티켓 없음 또는 구조 변경 감지 (page=%d)', page)
        return listings
    except Exception as e:
        logging.error('JSON 파싱 실패 (page=%d): %s', page, e, exc_info=True)
        return []


def parse_item(raw: dict) -> dict:
    perform_name = raw.get('depth2_name')
    perform_at = raw.get('perform_date', '').replace('T', ' ')
    floor = raw.get('floor')
    area = raw.get('area')
    row = raw.get('seat_number')
    grade = raw.get('grade')
    addinfo = raw.get('addinfo') or ''
    seat_parts = [p for p in [floor, f"{area}구역", f"{row}열", grade] if p]
    seat_info = ' '.join(seat_parts)
    price = raw.get('price')
    category_id = raw.get('category_id')
    link = f'https://www.ticketbay.co.kr/product/{category_id}/list/0'
    return {
        'id': raw.get('id'),
        'performName': perform_name,
        'performAt': perform_at,
        'seatInfo': seat_info,
        'price': price,
        'link': link,
    }


def send_ngl_alert(item: dict):
    message = (
        f"🎫 공연: {item['performName']}\n"
        f"⏰ 일시: {item['performAt']}\n"
        f"💺 좌석: {item['seatInfo']}\n"
        f"💰 가격: {item['price']}원\n"
    )
    data = {
        'username': NGL_USERNAME,
        'question': message,
        'deviceId': device_id(),
        'gameSlug': '',
        'referrer': '',
    }
    headers = {
        'Host': 'ngl.link',
        'Accept': '*/*',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'User-Agent': 'ticketbay-notifier/1.0',
        'Origin': 'https://ngl.link',
        'Referer': f'https://ngl.link/{NGL_USERNAME}',
    }
    try:
        resp = requests.post('https://ngl.link/api/submit', headers=headers, data=data, timeout=10)
        resp.raise_for_status()
        logging.info('✅ NGL 알림 전송 성공: %s', item['id'])
    except requests.RequestException as e:
        logging.error('❌ NGL 알림 전송 실패: %s', e, exc_info=True)


def main():
    seen_ids, first_run = load_seen_ids(SEEN_IDS_PATH)
    logging.info('▶ 모니터링 시작 (기존 티켓 %d개)', len(seen_ids))
    try:
        while True:
            new_found = False
            for page in range(MAX_PAGES):
                listings = fetch_ticket_listings(page)
                if not listings:
                    break
                for raw in listings:
                    item = parse_item(raw)
                    tid = item['id']
                    if not tid or tid in seen_ids:
                        continue
                    send_ngl_alert(item)
                    seen_ids.add(tid)
                    new_found = True
            if new_found or first_run:
                save_seen_ids(seen_ids, SEEN_IDS_PATH)
                first_run = False
            sleep_time = max(1, INTERVAL + random.uniform(-5, 5))
            logging.info('다음 체크까지 %.1f초 대기...', sleep_time)
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        logging.info('👋 모니터링 종료')
        save_seen_ids(seen_ids, SEEN_IDS_PATH)

if __name__ == '__main__':
    main()
