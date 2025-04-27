# 1. 어떤 OS와 파이썬 버전을 쓸지 정해요 (가볍고 많이 쓰는 버전)
FROM python:3.11-slim

# 2. 로봇 안에서 작업할 폴더를 만들어요
WORKDIR /app

# 3. Playwright(크로미움 브라우저)가 돌아가려면 필요한 리눅스 부품들을 먼저 설치해요
#    (이 부분은 Playwright 공식 문서를 참고했어요)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    libdbus-1-3 \
    # apt-get 캐시 정리해서 용량 줄이기
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 4. 필요한 파이썬 부품 목록(requirements.txt)을 로봇 안으로 복사해요
COPY requirements.txt .

# 5. 파이썬 부품들을 설치해요
RUN pip install --no-cache-dir -r requirements.txt

# 6. Playwright에게 크로미움 브라우저를 설치하라고 시켜요 (리눅스 부품 의존성 포함 설치)
#    chromium 브라우저만 설치합니다. 다른 브라우저(firefox, webkit)가 필요하다면 추가하세요.
RUN playwright install --with-deps chromium

# 7. 우리 코드 전부(.)를 로봇 안(/app 폴더)으로 복사해요
#    현재 폴더의 모든 파일 (BlogScraper.py, app.py, requirements.txt 등)을 /app 폴더로 복사
COPY . .

# 8. 로봇이 켜지면 자동으로 실행할 명령어를 알려줘요 (app.py 실행)
#    Railway는 $PORT 환경 변수를 자동으로 설정해주고, Flask 앱이 이를 사용하도록 설정했습니다.
#    Gunicorn 같은 WSGI 서버를 사용하면 성능이 더 좋아지지만, 일단 간단하게 python으로 실행합니다.
CMD ["python", "app.py"]