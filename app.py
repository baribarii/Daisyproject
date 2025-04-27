# app.py
import os
import uuid # Job ID 생성용
import threading
import asyncio
import json
import time
from flask import Flask, request, jsonify, Response, stream_with_context
import requests
from dotenv import load_dotenv # .env 파일 로딩용 (로컬 테스트)

# .env 파일 로드 (Railway 환경 변수가 우선 적용됨)
load_dotenv()

# 로깅 설정 (Railway 로그 확인을 위해)
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 스크래핑 함수 import (파일 이름 및 함수 이름 확인)
try:
    from BlogScraper import main as run_actual_scraper
except ImportError:
    logger.error("BlogScraper.py 또는 main 함수를 찾을 수 없습니다!")
    async def run_actual_scraper(): # 임시 함수
        await asyncio.sleep(1)
        return [{"error": "스크래퍼 모듈 로드 실패"}]

app = Flask(__name__)

# --- 상태 및 결과 저장을 위한 임시 메모리 저장소 ---
# 주의: 서버 재시작 시 모든 데이터가 사라집니다!
# 실제 서비스에서는 Redis, DB 등으로 교체해야 합니다.
scrape_jobs = {} # job_id를 키로 사용

# --- 스크래핑 백그라운드 작업 함수 ---
def run_scrape_task(job_id):
    global scrape_jobs
    logger.info(f"[{job_id}] 백그라운드 스크래핑 작업 시작.")

    # 상태 업데이트: 진행 중
    scrape_jobs[job_id] = {"status": "running", "message": "스크래핑 초기화 중...", "progress": 5, "result": None}

    try:
        # --- 상태 업데이트: 로그인 단계 (예시) ---
        scrape_jobs[job_id]["message"] = "네이버 로그인 처리 중..."
        scrape_jobs[job_id]["progress"] = 10
        # 여기서 BlogScraper.py 내부의 로그인 관련 로직이 실행된다고 가정
        # 실제 진행률 업데이트는 BlogScraper.py 수정 필요

        # --- asyncio 이벤트 루프 생성 및 스크래퍼 실행 ---
        # 참고: Flask + Threading + Asyncio는 복잡할 수 있음. FastAPI가 더 적합.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 실제 스크래핑 함수 호출
        # BlogScraper.py의 main()이 최종 결과 리스트를 반환해야 함
        scrape_result_data = loop.run_until_complete(run_actual_scraper())
        loop.close()

        # --- 상태 업데이트: 완료 ---
        if isinstance(scrape_result_data, list):
            scrape_jobs[job_id].update({
                "status": "completed",
                "message": f"스크래핑 완료 ({len(scrape_result_data)}개 포스트 수집)",
                "progress": 100,
                "result": scrape_result_data # 결과를 임시 저장소에 저장
            })
            logger.info(f"[{job_id}] 스크래핑 완료. 결과 저장됨.")

            # --- 결과 Replit으로 전송 ---
            replit_callback_url = os.environ.get("REPLIT_CALLBACK_URL")
            replit_secret_key = os.environ.get("REPLIT_SECRET_KEY")

            if replit_callback_url and replit_secret_key:
                logger.info(f"[{job_id}] 스크래핑 결과 Replit으로 전송 시도...")
                try:
                    payload = {
                        # 'user_id': scrape_jobs[job_id].get('user_id'), # 필요하다면 user_id도 포함
                        'job_id': job_id,
                        'result': scrape_result_data
                    }
                    headers = {
                        'Content-Type': 'application/json',
                        'X-Scraper-Secret': replit_secret_key
                    }
                    response = requests.post(replit_callback_url, json=payload, headers=headers, timeout=30)
                    if response.status_code == 200:
                        logger.info(f"[{job_id}] 결과 전송 성공: {response.status_code}")
                    else:
                        logger.error(f"[{job_id}] 결과 전송 실패: {response.status_code} - {response.text[:100]}")
                except Exception as callback_err:
                    logger.error(f"[{job_id}] 결과 전송 중 오류: {callback_err}")
            else:
                logger.warning(f"[{job_id}] Replit 콜백 URL 또는 Secret Key가 설정되지 않아 결과 전송 생략.")

        else:
            raise Exception("스크래퍼 함수가 유효한 리스트 결과를 반환하지 않았습니다.")

    except Exception as e:
        error_message = f"스크래핑 작업 중 오류: {str(e)}"
        logger.error(f"[{job_id}] {error_message}")
        # traceback.print_exc() # 상세 오류 로깅 필요시 주석 해제
        scrape_jobs[job_id].update({
            "status": "error",
            "message": error_message,
            "progress": -1,
            "result": None
        })

# --- API 엔드포인트 ---

@app.route('/')
def home():
    """서버 상태 확인용 기본 엔드포인트"""
    return "안녕하세요! 데이지 스크래퍼 서버가 작동 중입니다. 😊"

@app.route('/start-scrape', methods=['POST'])
def start_scrape_endpoint():
    """Replit 앱으로부터 스크래핑 시작 요청을 받습니다."""
    global scrape_jobs
    # 요청 데이터에서 user_id 가져오기 (선택적)
    # user_id = request.json.get('user_id')

    # 고유 작업 ID 생성
    job_id = str(uuid.uuid4())
    logger.info(f"스크래핑 요청 수신. Job ID 생성: {job_id}")

    # 작업 상태 초기화
    scrape_jobs[job_id] = {"status": "pending", "message": "스크래핑 대기 중...", "progress": 0, "result": None}
    # if user_id: scrape_jobs[job_id]['user_id'] = user_id # 필요시 사용자 ID 저장

    # 백그라운드 스레드에서 스크래핑 작업 시작
    thread = threading.Thread(target=run_scrape_task, args=(job_id,))
    thread.daemon = True # 메인 스레드 종료 시 함께 종료
    thread.start()

    # Replit 앱에는 작업 ID와 함께 수락되었음을 알림
    return jsonify({"message": "스크래핑 작업이 시작되었습니다.", "job_id": job_id}), 202

@app.route('/status/<job_id>')
def status_endpoint(job_id):
    """특정 작업 ID의 진행 상태를 SSE(Server-Sent Events)로 스트리밍합니다."""
    global scrape_jobs
    logger.info(f"[{job_id}] 상태 확인 요청 수신")

    def event_stream():
        last_status_json = None
        while True:
            job_info = scrape_jobs.get(job_id)
            if not job_info:
                # 작업 ID가 유효하지 않은 경우
                error_data = {"status": "error", "message": "유효하지 않은 작업 ID입니다.", "progress": -1}
                yield f"data: {json.dumps(error_data)}\n\n"
                logger.warning(f"[{job_id}] 유효하지 않은 작업 ID로 상태 확인 시도")
                break

            current_status = {k: v for k, v in job_info.items() if k != 'result'} # 결과 데이터 제외
            current_status_json = json.dumps(current_status)

            # 상태가 변경되었을 때만 전송
            if current_status_json != last_status_json:
                yield f"data: {current_status_json}\n\n"
                last_status_json = current_status_json
                logger.debug(f"[{job_id}] 상태 업데이트 전송: {current_status}")

            # 작업이 완료되거나 오류 발생 시 스트림 종료
            if job_info["status"] in ["completed", "error"]:
                logger.info(f"[{job_id}] 상태 스트림 종료 (상태: {job_info['status']})")
                break

            # 2초 간격으로 확인
            time.sleep(2)

    # SSE 응답 반환
    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

@app.route('/result/<job_id>', methods=['GET'])
def get_result_endpoint(job_id):
    """(선택적) 완료된 작업의 결과를 직접 가져오는 엔드포인트"""
    global scrape_jobs
    job_info = scrape_jobs.get(job_id)

    if not job_info:
        return jsonify({"error": "유효하지 않은 작업 ID입니다."}), 404

    if job_info["status"] == "completed":
        return jsonify({"job_id": job_id, "status": "completed", "result": job_info.get("result")})
    elif job_info["status"] == "error":
        return jsonify({"job_id": job_id, "status": "error", "message": job_info.get("message")}), 500
    else:
        return jsonify({"job_id": job_id, "status": job_info.get("status"), "message": "작업이 아직 진행 중입니다."}), 202

if __name__ == '__main__':
    # Railway는 PORT 환경 변수를 사용. 로컬 테스트 시 기본 8080 사용.
    port = int(os.environ.get('PORT', 8080))
    # debug=True는 로컬 테스트 시에만 유용. 실제 배포 시에는 False 권장.
    app.run(host='0.0.0.0', port=port, debug=False)