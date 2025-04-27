import asyncio
import re
import json
import traceback
import os  # <--- 환경 변수 사용을 위해 import
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

# — replit.db 폴백(Fallback) 설정 —
try:
    from replit import db
    use_replit_db = True
    print("✅ replit.db 모듈 감지: Replit DB에 저장합니다.")
except ModuleNotFoundError:
    use_replit_db = False
    print("⚠️ replit.db 모듈 없음: 로컬 JSON 파일(blog_posts.json)에 저장합니다.")

# --- 스크래핑 설정값 ---
MAX_CONCURRENT_PAGES = 5
REQUEST_BLOCKING_ENABLED = True
BLOCKED_RESOURCE_TYPES = ["image", "font", "media", "stylesheet"]
BLOCKED_DOMAINS = [
    "google-analytics.com", "googlesyndication.com", "googletagmanager.com",
    "googletagservices.com", "doubleclick.net", "naver.com/ad",
    "pagead2.googlesyndication.com", "analytics.naver.com", "crto.net",
    "acecounter.com", "facebook.net", "adnxs.com", "instagram.com"
]

# --- 네이버 관련 설정 ---
NAVER_LOGIN_URL    = "https://nid.naver.com/nidlogin.login"
NAVER_LOGIN_DOMAIN = "nid.naver.com"
MY_BLOG_ALIAS_URL  = "https://blog.naver.com/MyBlog.naver"
EXPORT_URL_TPL     = "https://admin.blog.naver.com/{}/config/postexport"
IFRAME_SELECTOR    = "#papermain"
POST_ROW_SELECTOR  = "#post_list_body tr[class*='postlist']"
PAGE_LINK_SELECTOR = "a.page"
NEXT_GROUP_SELECTOR= "a.page:has-text('다음')"

# 테스트를 위한 수집 제한 설정
MAX_POSTS_TO_COLLECT = 15
# MAX_POSTS_TO_COLLECT = None

# timeouts (밀리초)
SHORT_TO = 15_000
MID_TO   = 40_000
LONG_TO  = 90_000

async def block_unnecessary_requests(route):
    """네트워크 요청을 가로채 불필요한 리소스를 차단하는 함수"""
    request = route.request
    resource_type = request.resource_type
    url = request.url

    if resource_type in BLOCKED_RESOURCE_TYPES:
        try: await route.abort()
        except PlaywrightError: pass
        return

    if any(domain in url for domain in BLOCKED_DOMAINS):
        try: await route.abort()
        except PlaywrightError: pass
        return

    try: await route.continue_()
    except PlaywrightError: pass

async def scrape_single_post(context, meta, idx, total_posts, semaphore):
    """단일 블로그 포스트의 본문을 스크래핑하는 비동기 함수 (iframe 우선 탐색)"""
    async with semaphore:
        print(f"  [{idx}/{total_posts}] 시작: {meta['url']}")
        content = ""
        p2 = None
        data = {**meta, "content": "추출 시작 전"}

        try:
            p2 = await context.new_page()
            if REQUEST_BLOCKING_ENABLED:
                await p2.route("**/*", block_unnecessary_requests)

            await p2.goto(meta["url"], timeout=LONG_TO, wait_until="domcontentloaded")

            content_found = False
            extracted_content = ""

            # --- iframe 우선 탐색 ---
            try:
                main_iframe_locator = p2.locator('#mainFrame')
                await main_iframe_locator.wait_for(state="visible", timeout=MID_TO)
                print(f"  ✓ [{idx}/{total_posts}] #mainFrame iframe 발견. 내부 확인 중...")

                frame = main_iframe_locator.frame_locator(':scope')
                iframe_content_locator = frame.locator('#postViewArea, .se-main-container')
                await iframe_content_locator.first.wait_for(state="visible", timeout=MID_TO)

                iframe_post_view = frame.locator('#postViewArea')
                if await iframe_post_view.count() > 0:
                    extracted_content = await iframe_post_view.first.inner_text(timeout=SHORT_TO)
                    if extracted_content.strip():
                        print(f"  ✓ [{idx}/{total_posts}] iframe 추출 성공 (#postViewArea)")
                        content_found = True

                if not content_found:
                     iframe_main_container = frame.locator('.se-main-container')
                     if await iframe_main_container.count() > 0:
                        extracted_content = await iframe_main_container.first.inner_text(timeout=SHORT_TO)
                        if extracted_content.strip():
                            print(f"  ✓ [{idx}/{total_posts}] iframe 추출 성공 (.se-main-container)")
                            content_found = True

                if not content_found:
                     print(f"  ⚠️ [{idx}/{total_posts}] iframe 내에서 콘텐츠 요소(#postViewArea, .se-main-container) 찾기 실패")


            except PlaywrightTimeoutError:
                 print(f"  ⚠️ [{idx}/{total_posts}] iframe 또는 내부 콘텐츠 로딩 타임아웃.")
            except Exception as e:
                 # iframe이 아예 없는 경우 등 오류 발생 가능
                 print(f"  ⚠️ [{idx}/{total_posts}] iframe 처리 중 오류: {e}")


            # --- JavaScript 최후 시도 (iframe 우선 확인) ---
            if not content_found:
                print(f"  [{idx}/{total_posts}] JavaScript 추출 시도...")
                try:
                    js_content = await p2.evaluate("""
                        () => {
                            let content = '';
                            const iframe = document.querySelector('#mainFrame');
                            if (iframe && iframe.contentDocument) {
                                const iframePostView = iframe.contentDocument.querySelector('#postViewArea');
                                if (iframePostView && iframePostView.innerText.trim()) { content = iframePostView.innerText; }
                                if (!content.trim()) {
                                    const iframeMainContainer = iframe.contentDocument.querySelector('.se-main-container');
                                    if (iframeMainContainer && iframeMainContainer.innerText.trim()) { content = iframeMainContainer.innerText; }
                                }
                            }
                            if (!content.trim()) {
                                const postView = document.querySelector('#postViewArea');
                                if (postView && postView.innerText.trim()) { content = postView.innerText; }
                            }
                            if (!content.trim()) {
                                const mainContainer = document.querySelector('.se-main-container');
                                if (mainContainer && mainContainer.innerText.trim()) { content = mainContainer.innerText; }
                            }
                            return content;
                        }
                    """, timeout=SHORT_TO)

                    if js_content and js_content.strip():
                        extracted_content = js_content
                        content_found = True
                        print(f"  ✓ [{idx}/{total_posts}] JavaScript 추출 성공")
                    else:
                        print(f"  ⚠️ [{idx}/{total_posts}] JavaScript로도 콘텐츠 찾지 못함.")
                except Exception as e:
                    print(f"  ⚠️ [{idx}/{total_posts}] JavaScript 실행 중 오류: {e}")

            # --- 최종 결과 처리 ---
            if content_found:
                content = extracted_content.strip()
                data["content"] = content
            else:
                print(f"  ❌ [{idx}/{total_posts}] 모든 방법 실패: {meta['url']}")
                data["content"] = "본문 내용 추출 실패"
                try:
                    screenshot_path = f'debug_post_{meta["logNo"]}.png' # 로컬 저장 경로
                    # Railway 같은 환경에서는 파일 시스템 쓰기가 제한될 수 있으므로 오류 처리
                    await p2.screenshot(path=screenshot_path, full_page=True)
                    print(f"  → [{idx}/{total_posts}] 디버깅 스크린샷 저장: {screenshot_path}")
                except Exception as ss_err:
                    print(f"  ⚠️ [{idx}/{total_posts}] 스크린샷 저장 실패: {ss_err}")

            print(f"  [{idx}/{total_posts}] 완료: {meta['url']}")
            await p2.close()
            return data

        except PlaywrightError as pe:
            print(f"❌ [{idx}/{total_posts}] Playwright 오류: {pe} | URL: {meta['url']}")
            data["content"] = f"Playwright 오류로 인한 추출 실패: {str(pe)[:100]}"
            if p2 and not p2.is_closed():
                try: await p2.close()
                except Exception: pass
            return data
        except Exception as e:
            print(f"❌ [{idx}/{total_posts}] 예기치 않은 오류: {e} | URL: {meta['url']}")
            data["content"] = f"오류로 인한 추출 실패: {str(e)[:100]}"
            if p2 and not p2.is_closed():
                try: await p2.close()
                except Exception: pass
            return data
        finally:
            await asyncio.sleep(0.2)


async def main():
    all_meta = []
    pw = None
    browser = None
    # final_posts_data를 try 블록 전에 초기화
    final_posts_data = []

    try:
        # --- Proxy Configuration Logic ---
        proxy_server_env = os.environ.get("PROXY_SERVER")
        proxy_username_env = os.environ.get("PROXY_USERNAME")
        proxy_password_env = os.environ.get("PROXY_PASSWORD")

        proxy_config = None # 기본값: 프록시 없음

        if proxy_server_env: # 환경 변수가 있으면 클라우드로 간주
            print("☁️ 클라우드 환경 감지됨. 환경 변수에서 프록시 설정 로드 중...")
            proxy_config = {
                "server": proxy_server_env,
                "username": proxy_username_env,
                "password": proxy_password_env
            }
            if not proxy_username_env or not proxy_password_env:
                 print("⚠️ 경고: PROXY_USERNAME 또는 PROXY_PASSWORD 환경 변수가 없습니다.")
        else: # 환경 변수가 없으면 로컬로 간주하고 하드코딩된 값 사용
            print("🏠 로컬 환경 감지됨. 하드코딩된 프록시 설정 사용 중...")
            proxy_config = {
                "server": "http://168.199.145.165:6423",
                "username": "zqdduggo",
                "password": "r0i4xzlefyox"
            }

        if proxy_config:
             print(f"   - 프록시 서버: {proxy_config['server']}")
        else:
             print("   - 프록시 사용 안 함.")
        # --- End of Proxy Configuration Logic ---

        pw      = await async_playwright().start()
        # --- 클라우드 배포 시 headless=True로 변경 권장 ---
        browser = await pw.chromium.launch(
            headless=True, # Railway 배포 시 True로 변경!
            slow_mo=50,     # 배포 시 0 또는 제거 권장
            proxy=proxy_config
        )
        context = await browser.new_context(
             viewport={"width":1280,"height":800},
             user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
        )
        page    = await context.new_page()

        # --- 네이버 로그인 (수동 처리 부분 - 실제 서버에서는 다른 방식 필요) ---
        # 실제 서버에서는 사용자가 직접 상호작용할 수 없으므로,
        # 이 부분은 API 요청으로 ID/PW를 받거나, 미리 저장된 세션/쿠키를 사용하는 방식으로 변경해야 함.
        # 여기서는 로컬 실행 시 수동 로그인을 가정.
        print("🔑 네이버 로그인 페이지 열기...")
        await page.goto(NAVER_LOGIN_URL, timeout=LONG_TO)
        print("👉 헤드리스 모드에서는 자동 로그인이 구현되어야 합니다.")
        print("   (현재 코드는 수동 로그인을 가정하므로 클라우드 실행 시 이 부분에서 멈출 수 있습니다.)")
        print("   (서버 환경에서는 ID/PW를 직접 입력하거나 쿠키/토큰을 사용하는 로직 필요)")
        # 로그인 완료 대기 (URL 변경 감지)
        await page.wait_for_url(lambda url: NAVER_LOGIN_DOMAIN not in url and "naver.com" in url, timeout=LONG_TO)
        print("✅ 로그인 성공 (또는 로그인된 세션 감지됨).")

        # 2) blogId 추출
        print("📝 내 블로그로 이동하여 blogId 추출...")
        await page.goto(MY_BLOG_ALIAS_URL, timeout=LONG_TO)
        await page.wait_for_load_state("networkidle", timeout=MID_TO)
        m = re.search(r"(?:blog|admin\.blog)\.naver\.com/([^/?&#]+)", page.url)
        if not m:
            iframe_url = await page.evaluate("() => document.querySelector('#mainFrame')?.src")
            if iframe_url: m = re.search(r"blog\.naver\.com/([^/?&#]+)", iframe_url)
            if not m: raise RuntimeError("❌ blogId 추출 실패: URL 및 iframe에서 패턴 불일치")
        blog_id = m.group(1)
        print(f"✅ blogId: {blog_id}")

        # 3) 글 저장 페이지로 이동 (메타 정보 수집용)
        export_url = EXPORT_URL_TPL.format(blog_id)
        print(f"🔗 글 저장 페이지로 이동: {export_url}")
        await page.goto(export_url, timeout=LONG_TO)
        await page.wait_for_load_state("networkidle", timeout=MID_TO)

        # 4) iframe 내부 프레임 얻기
        print("iframe 요소 대기 중…")
        iframe_el = await page.wait_for_selector(IFRAME_SELECTOR, state="attached", timeout=LONG_TO)
        frame     = await iframe_el.content_frame()
        if not frame: raise RuntimeError("❌ iframe.content_frame() 실패")
        print("✅ iframe 내부 프레임 획득 완료.")

        # 5) 메타 정보 수집 함수 (내부 정의)
        async def scrape_meta_page():
            nonlocal all_meta, blog_id
            await asyncio.sleep(0.5)
            try:
                page_meta_data = await frame.evaluate("""
                    (args) => {
                        const selector = args[0];
                        const blogId = args[1];
                        const rows = document.querySelectorAll(selector);
                        const data = [];
                        rows.forEach(row => {
                            const logno = row.getAttribute('logno');
                            const dateEl = row.querySelector('td.tc span.num.add_date');
                            const titleLink = row.querySelector('span.txt.title a');
                            let url = null;
                            if (titleLink) { url = titleLink.href; }
                            else if (logno && blogId) { url = `https://blog.naver.com/${blogId}/${logno}`; }
                            data.push({
                                logno: logno,
                                date: dateEl ? dateEl.textContent.trim() : null,
                                title: titleLink ? titleLink.innerText.trim() : '제목 없음',
                                url: url
                            });
                        });
                        return data;
                    }
                """, [POST_ROW_SELECTOR, blog_id])

                print(f"  - 현재 페이지에서 {len(page_meta_data)}개의 행 데이터 발견 (evaluate)")
                found_new = 0
                for item in page_meta_data:
                    logno = item.get('logno')
                    date_str = item.get('date')
                    title = item.get('title', '제목 없음')
                    url = item.get('url')
                    if not logno: continue
                    if not date_str: date_str = "날짜 없음"
                    if not url: url = f"https://blog.naver.com/{blog_id}/{logno}"
                    if not any(p["logNo"] == logno for p in all_meta):
                        all_meta.append({"logNo": logno, "title": title, "url": url, "date": date_str})
                        print(f"    ✓ 수집: {logno} - {title[:30]}...")
                        found_new += 1
                        if MAX_POSTS_TO_COLLECT and len(all_meta) >= MAX_POSTS_TO_COLLECT:
                            print(f"🛑 수집 제한 도달: {MAX_POSTS_TO_COLLECT}개")
                            return True
                print(f"  - 새로운 메타 {found_new}개 추가됨.")
                return False
            except PlaywrightTimeoutError as te:
                print(f"  ❌ evaluate 실행 중 타임아웃 발생: {te}")
                return False
            except Exception as e:
                print(f"  ❌ 메타 정보 스크래핑 중 오류 (evaluate): {e}")
                traceback.print_exc()
                return False

        # — 메타 정보 스크래핑 시작 및 페이지네이션 —
        print("🚀 메타 정보 스크래핑 시작...")
        try:
            await frame.locator(POST_ROW_SELECTOR).first.wait_for(state="attached", timeout=MID_TO)
            print("  - 첫 페이지 로딩 확인됨.")
        except PlaywrightTimeoutError:
            print("⚠️ 첫 페이지 로딩 실패 또는 게시글 없음.")

        should_stop = await scrape_meta_page()

        if not should_stop:
            group = 1
            current_page = 1
            while True:
                if MAX_POSTS_TO_COLLECT and len(all_meta) >= MAX_POSTS_TO_COLLECT:
                    print(f"🛑 수집 제한 도달로 페이지네이션 중단: {MAX_POSTS_TO_COLLECT}개")
                    break
                page_numbers_texts = await frame.locator(PAGE_LINK_SELECTOR).all_inner_texts()
                number_pages = []
                for text in page_numbers_texts:
                    if text.isdigit():
                        page_num = int(text)
                        if page_num > current_page: number_pages.append(page_num)
                number_pages.sort()
                for page_num in number_pages:
                    if MAX_POSTS_TO_COLLECT and len(all_meta) >= MAX_POSTS_TO_COLLECT: break
                    print(f"➡️ 그룹{group} 페이지 {page_num} 스크래핑...")
                    try:
                        await frame.locator(f"{PAGE_LINK_SELECTOR} >> text='{page_num}'").first.click()
                        await frame.locator(POST_ROW_SELECTOR).first.wait_for(state="attached", timeout=MID_TO)
                        current_page = page_num
                        should_stop = await scrape_meta_page()
                        if should_stop: break
                    except Exception as e:
                        print(f"⚠️ 페이지 {page_num} 이동/처리 실패: {e}")
                if should_stop or (MAX_POSTS_TO_COLLECT and len(all_meta) >= MAX_POSTS_TO_COLLECT): break
                next_btn = frame.locator(NEXT_GROUP_SELECTOR)
                if await next_btn.count() == 0:
                    print("🎉 모든 페이지 그룹 스크래핑 완료.")
                    break
                print(f"➡️ 그룹 {group} 끝 → '다음' 클릭하여 다음 그룹 진입...")
                try:
                    await next_btn.first.click()
                    await frame.locator(POST_ROW_SELECTOR).first.wait_for(state="attached", timeout=MID_TO)
                    group += 1
                    next_group_pages = await frame.locator(f"{PAGE_LINK_SELECTOR}").all_inner_texts()
                    numeric_pages = [int(p) for p in next_group_pages if p.isdigit()]
                    current_page = min(numeric_pages) if numeric_pages else current_page + 1
                    print(f"  - 그룹 {group} 진입, 현재 페이지: {current_page}")
                    should_stop = await scrape_meta_page()
                    if should_stop: break
                except Exception as e:
                    print(f"⚠️ '다음' 그룹 이동 또는 로딩 실패: {e}")
                    break
        print(f"\n📋 메타 총 {len(all_meta)}개 수집 완료.")

        # 6) 본문 내용 동시 스크래핑 및 저장
        if not all_meta:
             print("ℹ️ 수집된 메타 정보가 없어 본문 스크래핑을 건너<0xEB><0x9C><0x85>니다.")
        else:
            print(f"\n🚀 {len(all_meta)}개 포스트 본문 동시 스크래핑 시작 (최대 동시: {MAX_CONCURRENT_PAGES}개)...")
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
            tasks = []
            total_posts = len(all_meta)
            for idx, meta in enumerate(all_meta, start=1):
                task = asyncio.create_task(scrape_single_post(context, meta, idx, total_posts, semaphore))
                tasks.append(task)
            results = await asyncio.gather(*tasks, return_exceptions=True)
            print("\n✅ 모든 본문 스크래핑 작업 완료. 결과 처리 중...")

            # --- final_posts_data 리스트에 결과 저장 ---
            successful_count = 0
            failed_count = 0
            for i, result in enumerate(results):
                meta_info = all_meta[i] if i < len(all_meta) else {"logNo": "N/A", "title": "Unknown"}
                if isinstance(result, Exception):
                    print(f"  - 심각한 오류 발생(gather): {result} - 연관 메타: {meta_info}")
                    failed_count += 1
                    error_data = {**meta_info, "content": f"스크래핑 작업 오류: {str(result)[:100]}"}
                    final_posts_data.append(error_data) # 실패 데이터도 포함
                elif isinstance(result, dict):
                    final_posts_data.append(result) # 성공/실패 결과 dict 포함
                    if "추출 실패" in result.get("content", "") or "오류로 인한" in result.get("content", ""):
                        failed_count += 1
                    else: successful_count += 1
                else:
                    print(f"  - 알 수 없는 결과 타입: {type(result)} - 연관 메타: {meta_info}")
                    failed_count += 1
                    unknown_data = {**meta_info, "content": "알 수 없는 결과 타입"}
                    final_posts_data.append(unknown_data)
            print(f"📊 스크래핑 결과: 성공 {successful_count}개, 실패 {failed_count}개")

            # 7) 최종 데이터 저장 (선택적 - app.py가 결과를 받아 처리할 것이므로 주석 처리 가능)
            if not use_replit_db: # 로컬 파일 저장은 로컬 테스트 시에만 의미 있음
                output_filename = "blog_posts.json"
                try:
                    with open(output_filename, "w", encoding="utf-8") as f:
                        json.dump(final_posts_data, f, ensure_ascii=False, indent=2)
                    print(f"✅ (로컬 테스트용) {output_filename}에 {len(final_posts_data)}개 포스트 저장 완료.")
                except IOError as io_err:
                    print(f"❌ 로컬 파일 저장 실패 ({output_filename}): {io_err}")

        # --- !!! 성공 시 반환 로직을 try 블록 끝으로 이동 !!! ---
        print(f"BlogScraper.py: 총 {len(final_posts_data)}개 포스트 데이터 반환")
        return final_posts_data # <--- 스크래핑 결과를 반환해야 함!

    except RuntimeError as err:
        print(f"💥 실행 중 오류: {err}"); traceback.print_exc()
        return [] # 오류 시 빈 리스트 반환
    except PlaywrightError as pe:
        print(f"💥 Playwright 관련 오류 발생: {pe}"); traceback.print_exc()
        return [] # 오류 시 빈 리스트 반환
    except Exception as e:
        print(f"💥 예상치 못한 치명적 오류 발생: {e}"); traceback.print_exc()
        return [] # 오류 시 빈 리스트 반환
    finally:
        # --- finally 블록: 성공하든 실패하든 항상 실행됨 ---
        print("🔄 스크래핑 리소스 정리 중...")
        if browser and not browser.is_closed():
            try:
                await browser.close()
                print("  - 브라우저 닫힘.")
            except Exception as close_err:
                print(f"  ⚠️ 브라우저 닫기 오류: {close_err}")
        if pw:
            try:
                await pw.stop()
                print("  - Playwright 프로세스 중지됨.")
            except Exception as stop_err:
                 print(f"  ⚠️ Playwright 중지 오류: {stop_err}")
        print("🏁 스크래핑 리소스 정리 완료.")

if __name__ == "__main__":
    # 이 파일이 직접 실행될 때 (테스트용)
    print("스크립트 직접 실행 시작 (테스트 모드)")
    results = asyncio.run(main())
    print(f"\n스크립트 직접 실행 완료. 결과({len(results)}개 포스트) 확인.")
    # 테스트 결과 간단히 출력
    if results:
        print("\n--- 수집된 포스트 (일부) ---")
        for i, post in enumerate(results[:3]): # 처음 3개만 출력
            print(f"  {i+1}. 제목: {post.get('title', 'N/A')[:30]}...")
            print(f"     날짜: {post.get('date', 'N/A')}")
            print(f"     내용: {post.get('content', '')[:50]}...")