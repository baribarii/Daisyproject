import os
from flask import Flask, request, jsonify
import threading # 스크래핑을 백그라운드에서 돌리기 위한 임시방편
import asyncio
# BlogScraper.py 파일 안에 있는 main 함수를 가져옵니다.
# 만약 BlogScraper.py의 main 함수 이름이 다르다면 바꿔주세요.
try:
    # BlogScraper.py의 main 함수를 다른 이름으로 가져와서 충돌 방지
    from BlogScraper import main as run_scraper_main
except ImportError:
    print("오류: BlogScraper.py 파일을 찾을 수 없거나 main 함수가 없습니다.")
    # 실제 main 함수가 없다면, 아래 run_scraper_main 호출 부분을 수정해야 합니다.
    def run_scraper_main():
        print("스크래퍼 main 함수를 찾을 수 없어 실행할 수 없습니다.")
        return {"error": "스크래퍼 함수 없음"}


app = Flask(__name__)

# 스크래핑 상태를 간단히 저장 (서버 재시작하면 초기화됨 - 임시방편)
scrape_status = "idle" # 상태: idle, running, completed, error
scrape_result_data = None

# 스크래핑을 별도 스레드에서 실행하는 함수
def run_scrape_task():
    global scrape_status, scrape_result_data
    print("백그라운드 스크래핑 작업 시작...")
    scrape_status = "running"
    scrape_result_data = None # 이전 결과 초기화
    try:
        # 새 asyncio 이벤트 루프에서 스크래퍼 실행
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # run_scraper_main() 함수가 스크랩 결과를 반환한다고 가정
        result = loop.run_until_complete(run_scraper_main())
        loop.close()

        scrape_result_data = result # 결과 저장
        scrape_status = "completed"
        print("스크래핑 작업 완료.")

        # TODO: 여기서 완료된 결과를 Replit으로 보내는 코드 추가 필요 (requests 사용)
        # 예: requests.post(os.environ.get("REPLIT_CALLBACK_URL"), json=result)

    except Exception as e:
        print(f"스크래핑 작업 중 오류 발생: {e}")
        scrape_status = "error"
        scrape_result_data = {"error": str(e)}

# app.py 파일에 추가할 내용
@app.route('/') # 루트 주소 ('/')로 GET 요청이 오면
def home():
    # 간단한 환영 메시지를 반환해요.
    return "안녕하세요! 데이지 스크래퍼 서버가 작동 중입니다. 😊"

# 외부(Replit)에서 스크래핑을 시작하라고 요청하는 주소
@app.route('/scrape', methods=['POST'])
def start_scraping():
    global scrape_status
    if scrape_status == "running":
        return jsonify({"message": "이미 스크래핑이 진행 중입니다."}), 409 # Conflict

    # 백그라운드에서 스크래핑 시작
    # threading은 가장 간단한 방법이지만, 요청이 많아지면 문제될 수 있음
    thread = threading.Thread(target=run_scrape_task)
    thread.start()

    return jsonify({"message": "스크래핑 작업을 시작했습니다."}), 202 # Accepted

# 스크래핑 상태를 확인하는 주소
@app.route('/status', methods=['GET'])
def get_status():
    global scrape_status
    return jsonify({"status": scrape_status})

# 스크래핑 결과를 확인하는 주소 (완료 후)
@app.route('/result', methods=['GET'])
def get_result():
    global scrape_status, scrape_result_data
    if scrape_status == "completed":
        return jsonify(scrape_result_data)
    elif scrape_status == "error":
        return jsonify(scrape_result_data), 500 # Internal Server Error (or other code)
    else:
        return jsonify({"message": "스크래핑이 아직 완료되지 않았거나 시작되지 않았습니다."}), 202 # Accepted (or 404 Not Found)

if __name__ == '__main__':
    # 서버 실행 (Railway는 보통 8080 포트를 좋아해요)
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)