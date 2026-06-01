import json
import os
import re
import sys
import time
import threading
from urllib.parse import quote

import pandas as pd

from browser import NaverBrowser
from naver_api import NaverBlogAPI
from report import save_report


def get_app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_BASE_DIR = get_app_base_dir()
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(APP_BASE_DIR, "browsers")

# app.py에서 주입하는 일시정지 이벤트 (없으면 계속 진행)
_PAUSE_EVENT = None
_STOP_EVENT = None

try:
    from PIL import Image

    PIL_OK = True
except ImportError:
    PIL_OK = False
    print("경고: Pillow 설치가 필요합니다. 'python -m pip install pillow' 실행 후 다시 시도하세요.")


OUTPUT_DIR = os.path.join(APP_BASE_DIR, "output")
CAPTURE_DIR = os.path.join(OUTPUT_DIR, "captures")
REPORT_PATH = os.path.join(OUTPUT_DIR, "rank_report.xlsx")
INPUT_FILE = os.path.join(APP_BASE_DIR, "input.csv")
CONFIG_FILES = [
    os.path.join(APP_BASE_DIR, "config.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
]
SELECTOR_FILES = [
    os.path.join(APP_BASE_DIR, "selectors.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "selectors.json"),
]

MAX_SCROLL = 80
SEARCHBAR_CROP = 68
SEARCHBAR_SCAN_LIMIT = 120
EL_TARGET_Y = 190
CARD_TOP_PADDING = 4
CARD_BOTTOM_PADDING = 24
RESULT_CARD_GAP = 8
CONTENT_SIDE_PADDING = 24
EMPTY_SCAN_LIMIT = 8
SCROLL_OVERLAP_RATIO = 0.35

BLOG_CARD_SELECTOR = '[data-template-id="ugcItem"]'
CHANNEL_SELECTOR = ".sds-comps-profile-info-title-text"
VALID_BROWSER_MODES = {"auto", "chrome", "chromium"}
TITLE_SELECTOR_CANDIDATES = [
    "a.title_link",
    ".title_area a[href]",
    "a.api_txt_lines.total_tit",
    "a[data-testid='title']",
    ".sds-comps-text-type-headline1 a[href]",
    ".title_group a[href]",
]
IGNORED_LINK_LABELS = [
    "keep에저장",
    "keep",
    "공유",
    "더보기",
    "접기",
    "열기",
    "이전",
    "다음",
    "댓글",
    "좋아요",
]

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CAPTURE_DIR, exist_ok=True)


EXCLUDE_PATTERNS = [
    r"^\d+분\s*전$",
    r"^\d+시간\s*전$",
    r"^\d+일\s*전$",
    r"^\d+주\s*전$",
    r"^\d+개월\s*전$",
    r"^\d{4}\.\d{2}\.\d{2}\.$",
    r"^\d+$",
]

EXCLUDE_EXACT = {
    "NAVER",
    "블로그",
    "전체",
    "이미지",
    "카페",
    "동영상",
    "지식iN",
    "인플루언서",
    "뉴스",
    "VIEW",
    "검색",
    "관련도순",
    "최신순",
    "더보기",
    "공유",
    "옵션",
}


class SearchCancelled(Exception):
    pass


def _load_selector_config():
    config = {
        "blog_card_selector": BLOG_CARD_SELECTOR,
        "channel_selector": CHANNEL_SELECTOR,
        "title_selector_candidates": list(TITLE_SELECTOR_CANDIDATES),
        "ignored_link_labels": list(IGNORED_LINK_LABELS),
    }

    for path in SELECTOR_FILES:
        if not os.path.exists(path):
            continue

        loaded = None
        for encoding in ("utf-8-sig", "utf-8", "cp949"):
            try:
                with open(path, "r", encoding=encoding) as file:
                    loaded = json.load(file)
                break
            except UnicodeDecodeError:
                continue
            except Exception:
                loaded = None
                break

        if not isinstance(loaded, dict):
            continue

        blog_card_selector = str(loaded.get("blog_card_selector", "")).strip()
        channel_selector = str(loaded.get("channel_selector", "")).strip()
        title_selector_candidates = loaded.get("title_selector_candidates")
        ignored_link_labels = loaded.get("ignored_link_labels")

        if blog_card_selector:
            config["blog_card_selector"] = blog_card_selector
        if channel_selector:
            config["channel_selector"] = channel_selector
        if isinstance(title_selector_candidates, list):
            cleaned = [str(item).strip() for item in title_selector_candidates if str(item).strip()]
            if cleaned:
                config["title_selector_candidates"] = cleaned
        if isinstance(ignored_link_labels, list):
            cleaned = [str(item).strip() for item in ignored_link_labels if str(item).strip()]
            if cleaned:
                config["ignored_link_labels"] = cleaned
        break

    return config


_SELECTOR_CONFIG = _load_selector_config()
BLOG_CARD_SELECTOR = _SELECTOR_CONFIG["blog_card_selector"]
CHANNEL_SELECTOR = _SELECTOR_CONFIG["channel_selector"]
TITLE_SELECTOR_CANDIDATES = _SELECTOR_CONFIG["title_selector_candidates"]
IGNORED_LINK_LABELS = _SELECTOR_CONFIG["ignored_link_labels"]


def normalize(text):
    return re.sub(r"\s+", "", str(text or "")).lower()


def normalize_url(text):
    url = str(text or "").strip()
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/").lower()


def extract_blog_id(text):
    normalized_url = normalize_url(text)
    match = re.search(r"(?:https?://)?(?:m\.)?blog\.naver\.com/([^/?#]+)", normalized_url)
    return normalize(match.group(1)) if match else ""


def extract_post_id(url):
    """네이버 블로그 포스트 고유 번호(logNo) 추출.

    지원 형식:
      - blog.naver.com/blogid/1234567890
      - blog.naver.com/PostView.naver?blogId=xxx&logNo=1234567890
    """
    url = str(url or "").lower()
    m = re.search(r"blog\.naver\.com/[^/?#]+/(\d{5,})", url)
    if m:
        return m.group(1)
    m = re.search(r"logno=(\d{5,})", url)
    if m:
        return m.group(1)
    return ""


def safe_filename(text, fallback="untitled", keep_spaces=False, max_length=80):
    value = str(text or "").strip()
    if not keep_spaces:
        value = re.sub(r"\s+", "", value)
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    value = re.sub(r"_+", "_", value).strip(" ._")
    if not value:
        value = fallback
    return value[:max_length].rstrip(" ._") or fallback


def load_json_config():
    for path in CONFIG_FILES:
        if not os.path.exists(path):
            continue

        for encoding in ("utf-8-sig", "utf-8", "cp949"):
            try:
                with open(path, "r", encoding=encoding) as fp:
                    return json.load(fp)
            except UnicodeDecodeError:
                continue
            except Exception:
                break

    return {}


def get_browser_mode():
    env_mode = os.getenv("BLOG_BROWSER_MODE", "").strip().lower()
    if env_mode in VALID_BROWSER_MODES:
        return env_mode

    config = load_json_config()
    config_mode = str(config.get("browser_mode", "")).strip().lower()
    if config_mode in VALID_BROWSER_MODES:
        return config_mode

    return "auto"


def get_browser_attempt_modes(browser_mode):
    if browser_mode == "chrome":
        return ["chrome"]
    if browser_mode == "chromium":
        return ["chromium"]
    return ["chrome", "chromium"]


def wait_if_paused():
    while _PAUSE_EVENT is not None and not _PAUSE_EVENT.is_set():
        if _STOP_EVENT is not None and _STOP_EVENT.is_set():
            raise SearchCancelled("사용자 요청으로 중단")
        time.sleep(0.1)

    if _STOP_EVENT is not None and _STOP_EVENT.is_set():
        raise SearchCancelled("사용자 요청으로 중단")


def controlled_sleep(seconds, interval=0.1):
    end_time = time.time() + max(0.0, float(seconds))
    while True:
        wait_if_paused()
        remaining = end_time - time.time()
        if remaining <= 0:
            return
        time.sleep(min(interval, remaining))


def resolve_searchbar_crop(img):
    width, height = img.size
    scan_limit = min(SEARCHBAR_SCAN_LIMIT, height, SEARCHBAR_CROP + 20)
    best_row = None
    best_count = 0

    # Search for the horizontal green underline in the Naver header.
    for y in range(scan_limit):
        green_count = 0
        for x in range(0, width, 2):
            r, g, b = img.getpixel((x, y))
            if g >= 140 and r <= 120 and b <= 160:
                green_count += 1

        if green_count > best_count:
            best_count = green_count
            best_row = y

    if best_row is not None and best_count >= max(30, width // 12):
        return max(SEARCHBAR_CROP - 8, best_row + 1)

    return SEARCHBAR_CROP


def is_valid_channel(text):
    candidate = str(text or "").strip()
    if not candidate or len(candidate) < 2:
        return False
    if candidate in EXCLUDE_EXACT:
        return False
    return not any(re.match(pattern, candidate) for pattern in EXCLUDE_PATTERNS)


def pick_channel_name(candidates):
    for candidate in candidates or []:
        if is_valid_channel(candidate):
            return str(candidate).strip()
    return ""


def build_card_key(card):
    parts = [
        normalize(card.get("title")),
        normalize_url(card.get("primary_link")),
        normalize(card.get("channel_name")),
    ]
    parts = [part for part in parts if part]
    if parts:
        return "|".join(parts)
    return normalize(card.get("text"))[:200]


def build_match_context(identifier, api_result, query=""):
    bloggerlink = api_result.get("bloggerlink", "")
    post_link = api_result.get("post_link", "")
    return {
        "identifier": identifier,
        "identifier_norm": normalize(identifier),
        "api_title": api_result.get("title", ""),
        "api_title_norm": normalize(api_result.get("title", "")),
        "api_matched_text": api_result.get("matched_text", ""),
        "api_matched_norm": normalize(api_result.get("matched_text", "")),
        "api_field": api_result.get("matched_field", ""),
        "bloggerlink": bloggerlink,
        "bloggerlink_norm": normalize_url(bloggerlink),
        "blogger_id_norm": extract_blog_id(bloggerlink),
        "post_link": post_link,
        "post_link_id": extract_post_id(post_link),
        "query_norm": normalize(query),
    }


def get_visible_blog_cards(page):
    try:
        raw_cards = page.evaluate(
            f"""
            () => {{
                const normalize = (value) => String(value || '').replace(/\\s+/g, '').toLowerCase();
                const ignoredLabels = {IGNORED_LINK_LABELS!r};
                const titleSelectors = {TITLE_SELECTOR_CANDIDATES!r};
                const isIgnoredLabel = (value) => {{
                    const normalized = normalize(value);
                    if (!normalized) return true;
                    return ignoredLabels.some((label) => normalized === label || normalized.includes(label));
                }};

                const cards = Array.from(document.querySelectorAll('{BLOG_CARD_SELECTOR}'));
                return cards
                    .map((card, index) => {{
                        const rect = card.getBoundingClientRect();
                        if (rect.bottom < -120 || rect.top > window.innerHeight + 120) {{
                            return null;
                        }}

                        const channelCandidates = Array.from(
                            card.querySelectorAll('{CHANNEL_SELECTOR}')
                        )
                            .map((el) => (el.textContent || '').trim())
                            .filter(Boolean);

                        const links = Array.from(card.querySelectorAll('a[href]'))
                            .map((anchor) => {{
                                const linkRect = anchor.getBoundingClientRect();
                                const text = (anchor.innerText || anchor.textContent || '').trim();
                                const title = (anchor.getAttribute('title') || '').trim();
                                const label = title || text;
                                return {{
                                    text,
                                    title,
                                    label,
                                    href: anchor.href || anchor.getAttribute('href') || '',
                                    y: linkRect.top,
                                    class_name: anchor.className || '',
                                }};
                            }})
                            .filter((item) => item.href || item.label);

                        let titleNode = null;
                        for (const selector of titleSelectors) {{
                            titleNode = card.querySelector(selector);
                            if (titleNode) break;
                        }}

                        const scoredLinks = links
                            .map((item) => {{
                                let score = 0;
                                const normalizedLabel = normalize(item.label);
                                const normalizedHref = String(item.href || '').toLowerCase();

                                if (!isIgnoredLabel(item.label)) score += 20;
                                if (item.title && !isIgnoredLabel(item.title)) score += 8;
                                if (item.text && !isIgnoredLabel(item.text)) score += 5;
                                if ((item.label || '').length >= 8) score += 10;
                                if ((item.label || '').length >= 16) score += 8;
                                if (normalizedHref.includes('blog.naver.com')) score += 10;
                                if (normalizedHref.includes('/redirect/')) score -= 3;
                                if (normalizedHref.includes('keep') || normalizedHref.includes('share')) score -= 30;
                                if (normalizedHref.startsWith('javascript:')) score -= 50;
                                if (channelCandidates.some((name) => normalize(name) === normalizedLabel)) score -= 12;
                                if (/keep|save|공유|댓글|좋아요/.test(item.class_name || '')) score -= 20;
                                if (isIgnoredLabel(item.label)) score -= 100;

                                return {{ ...item, score }};
                            }})
                            .sort((a, b) => b.score - a.score);

                        const selectorTitle = titleNode
                            ? ((titleNode.getAttribute('title') || titleNode.innerText || titleNode.textContent || '').trim())
                            : '';

                        const titleCandidate =
                            (selectorTitle && !isIgnoredLabel(selectorTitle) && {{
                                label: selectorTitle,
                                href: titleNode.href || titleNode.getAttribute('href') || '',
                            }}) ||
                            scoredLinks.find((item) => item.score > 0) ||
                            {{}};

                        return {{
                            dom_index: index,
                            y: rect.top,
                            title: (titleCandidate.label || '').trim(),
                            primary_link: titleCandidate.href || (scoredLinks[0] ? scoredLinks[0].href : ''),
                            hrefs: links.map((item) => item.href).filter(Boolean),
                            link_texts: links
                                .map((item) => item.label || '')
                                .filter(Boolean),
                            channel_candidates: channelCandidates,
                            text: (card.innerText || '').trim(),
                        }};
                    }})
                    .filter(Boolean)
                    .sort((a, b) => a.y - b.y);
            }}
            """
        )
    except Exception:
        return []

    cards = []
    for raw_card in raw_cards:
        raw_card["channel_name"] = pick_channel_name(raw_card.get("channel_candidates", []))
        raw_card["key"] = build_card_key(raw_card)
        cards.append(raw_card)
    return cards


def match_blog_card(card, match_context):
    channel_norm = normalize(card.get("channel_name"))
    title_norm = normalize(card.get("title"))
    text_norm = normalize(card.get("text"))
    link_texts_norm = [normalize(text) for text in card.get("link_texts", []) if text]
    hrefs_norm = [normalize_url(href) for href in card.get("hrefs", []) if href]
    blog_ids = {extract_blog_id(href) for href in hrefs_norm}
    blog_ids.discard("")

    api_title_norm = match_context["api_title_norm"]
    bloggerlink_norm = match_context["bloggerlink_norm"]
    blogger_id_norm = match_context["blogger_id_norm"]
    api_matched_norm = match_context["api_matched_norm"]
    identifier_norm = match_context["identifier_norm"]
    post_link_id = match_context.get("post_link_id", "")
    query_norm = match_context.get("query_norm", "")

    # 카드 자체의 primary_link (포스트 URL) 기준으로 블로그 계정 추출
    # hrefs 전체가 아닌 primary_link만 사용 → 협찬 포스트가 업체 블로그에
    # 링크를 달아도 해당 포스트의 계정은 다른 블로그이므로 오매칭 방지
    primary_link_norm = normalize_url(card.get("primary_link", ""))
    primary_blog_id = extract_blog_id(primary_link_norm)
    primary_post_id = extract_post_id(primary_link_norm)

    # ① URL/ID 기반 (신뢰도 최상 - 먼저 확인)
    # Fix #2: API가 특정 포스트 URL을 반환한 경우 포스트 ID까지 일치해야 캡처
    # → 같은 블로그라도 다른 키워드 포스트를 잘못 캡처하는 오매칭 방지
    if bloggerlink_norm and primary_link_norm and (
        primary_link_norm == bloggerlink_norm
        or primary_link_norm.startswith(bloggerlink_norm + "/")
    ):
        if post_link_id and primary_post_id:
            if post_link_id == primary_post_id:
                return "bloggerlink"
            # 블로그는 맞지만 포스트가 다름 → 스킵하고 계속 탐색
        else:
            return "bloggerlink"

    if blogger_id_norm and primary_blog_id and blogger_id_norm == primary_blog_id:
        if post_link_id and primary_post_id:
            if post_link_id == primary_post_id:
                return "blogger_id"
            # 블로그는 맞지만 포스트가 다름 → 스킵하고 계속 탐색
        else:
            return "blogger_id"

    # ② 채널명 기반
    # Fix #1: API가 타겟 블로그를 찾지 못해 bloggerlink가 없을 때,
    # 채널명만으로 매칭하면 부분 포함 키워드("삼성동골프" ↔ "삼성동골프레슨")의
    # 다른 포스트를 잘못 캡처할 수 있음.
    # → 카드 제목·본문에 검색 키워드가 포함된 경우에만 매칭 허용
    def _keyword_in_card():
        if not query_norm:
            return True  # 키워드 확인 불가 시 허용
        return query_norm in title_norm or query_norm in text_norm

    if api_matched_norm and channel_norm and api_matched_norm in channel_norm:
        if not bloggerlink_norm or _keyword_in_card():
            return "matched_channel"

    if identifier_norm and channel_norm and identifier_norm in channel_norm:
        if not bloggerlink_norm or _keyword_in_card():
            return "identifier_channel"

    # ③ 제목 기반 - 반드시 채널명으로 대상 블로그 신원 확인
    # (방문 후기 블로그는 포스트 제목에 업체명이 포함돼도 채널명이 다르므로 제외됨)
    def _channel_confirms():
        if not channel_norm:
            return False
        for target in (api_matched_norm, identifier_norm):
            if target and (target in channel_norm or channel_norm in target):
                return True
        return False

    if api_title_norm and (api_title_norm == title_norm or api_title_norm in title_norm):
        if _channel_confirms():
            return "api_title"

    if api_title_norm and api_title_norm in text_norm:
        if _channel_confirms():
            return "api_title_text"

    # ④ 링크 텍스트 기반 (채널명 확인 필요 - 협찬/마케팅 블로그 제외)
    if api_matched_norm and any(api_matched_norm in text for text in link_texts_norm):
        if _channel_confirms():
            return "matched_link_text"

    if identifier_norm and any(identifier_norm in text for text in link_texts_norm):
        if _channel_confirms():
            return "identifier_link_text"

    # ⑤ 블로그 ID 기반 (URL에서 추출 - 채널명 불필요)
    if identifier_norm and identifier_norm in blog_ids:
        return "identifier_blog_id"

    return None


def inject_spacer(page):
    page.evaluate(
        """
        () => {
            if (document.getElementById('__cap_spacer__')) return;
            const spacer = document.createElement('div');
            spacer.id = '__cap_spacer__';
            spacer.style.cssText =
                'height:600px;display:block;visibility:hidden;pointer-events:none;flex-shrink:0;';
            const container =
                document.querySelector('.sds-comps-unified-search-list') ||
                document.querySelector('#main_pack') ||
                document.querySelector('.main_pack') ||
                document.body;
            container.insertBefore(spacer, container.firstChild);
        }
        """
    )


def remove_spacer(page):
    page.evaluate(
        """
        () => {
            const spacer = document.getElementById('__cap_spacer__');
            if (spacer) spacer.remove();
        }
        """
    )


def _capture_target_payload(card):
    return {
        "dom_index": card.get("dom_index"),
        "title": card.get("title", ""),
        "channel_name": card.get("channel_name", ""),
        "primary_link": normalize_url(card.get("primary_link", "")),
        "target_y": EL_TARGET_Y,
    }


def _resolve_capture_coords(page, target):
    return page.evaluate(
        f"""
        (target) => {{
            const normalize = (value) => String(value || '').replace(/\\s+/g, '').toLowerCase();
            const normalizeUrl = (value) =>
                String(value || '')
                    .trim()
                    .replace(/[?#].*$/, '')
                    .replace(/\\/+$/, '')
                    .toLowerCase();

            const cards = Array.from(document.querySelectorAll('{BLOG_CARD_SELECTOR}'));
            let card = Number.isInteger(target.dom_index) ? cards[target.dom_index] : null;

            if (!card) {{
                let bestScore = -1;
                for (const candidate of cards) {{
                    const channelTexts = Array.from(
                        candidate.querySelectorAll('{CHANNEL_SELECTOR}')
                    )
                        .map((el) => (el.textContent || '').trim())
                        .filter(Boolean)
                        .join(' ');
                    const links = Array.from(candidate.querySelectorAll('a[href]')).map((anchor) => {{
                        const title = (anchor.getAttribute('title') || '').trim();
                        const text = (anchor.innerText || anchor.textContent || '').trim();
                        return {{
                            href: anchor.href || anchor.getAttribute('href') || '',
                            text: title || text,
                        }};
                    }});

                    const title =
                        links.find((item) => item.text && item.text.length >= 6)?.text ||
                        (candidate.innerText || '').trim();

                    let score = 0;
                    if (target.title && normalize(title).includes(normalize(target.title))) score += 8;
                    if (
                        target.channel_name &&
                        normalize(channelTexts).includes(normalize(target.channel_name))
                    ) {{
                        score += 5;
                    }}
                    if (
                        target.primary_link &&
                        links.some((item) => {{
                            const href = normalizeUrl(item.href);
                            return href && (href.includes(target.primary_link) || target.primary_link.includes(href));
                        }})
                    ) {{
                        score += 6;
                    }}

                    if (score > bestScore) {{
                        bestScore = score;
                        card = candidate;
                    }}
                }}
            }}

            if (!card) return null;

            card.scrollIntoView({{ block: 'start' }});
            const currentTop = card.getBoundingClientRect().top;
            window.scrollBy(0, currentTop - target.target_y);

            const dpr = window.devicePixelRatio || 1;
            const rect = card.getBoundingClientRect();
            return {{
                cardLeft: Math.round(rect.left * dpr),
                cardRight: Math.round(rect.right * dpr),
                cardTop: Math.round(rect.top * dpr),
                cardBottom: Math.round(rect.bottom * dpr) + {CARD_BOTTOM_PADDING},
            }};
        }}
        """,
        target,
    )


def capture_clean(page, card, capture_path):
    target = _capture_target_payload(card)
    tmp_path = capture_path + "_tmp.png"

    try:
        inject_spacer(page)
        controlled_sleep(0.3)

        coords = _resolve_capture_coords(page, target)
        controlled_sleep(1.0)
        page.mouse.move(0, 0)
        controlled_sleep(0.2)
        page.screenshot(path=tmp_path, full_page=False)
    finally:
        remove_spacer(page)

    if not PIL_OK or not coords:
        os.replace(tmp_path, capture_path)
        return

    img = Image.open(tmp_path).convert("RGB")
    width, height = img.size
    searchbar_crop = resolve_searchbar_crop(img)
    card_left = max(0, coords["cardLeft"] - CONTENT_SIDE_PADDING)
    card_right = min(width, coords["cardRight"] + CONTENT_SIDE_PADDING)
    card_top = coords["cardTop"]
    card_bottom = min(coords["cardBottom"], height)

    print(
        f"   카드 영역: x={card_left}px~{card_right}px / y={card_top}px~{card_bottom}px"
    )

    if (
        card_top <= searchbar_crop
        or card_top >= height
        or card_top >= card_bottom
        or card_left >= card_right
    ):
        print("   카드 좌표가 비정상이라 전체 화면을 저장합니다.")
        img.save(capture_path)
        os.remove(tmp_path)
        return

    gap = RESULT_CARD_GAP
    searchbar = img.crop((card_left, 0, card_right, searchbar_crop))
    crop_start = max(card_top - CARD_TOP_PADDING, searchbar_crop)
    card_crop = img.crop((card_left, crop_start, card_right, card_bottom))

    result_width = searchbar.width
    result_height = searchbar.height + gap + card_crop.height
    result = Image.new("RGB", (result_width, result_height), (255, 255, 255))
    result.paste(searchbar, (0, 0))
    result.paste(card_crop, (0, searchbar.height + gap))
    result.save(capture_path)

    os.remove(tmp_path)
    print("   캡처 완료")


def block_new_tabs(context):
    def on_page(page):
        print(f"  팝업/새 탭 차단: {page.url[:80]}")
        try:
            page.close()
        except Exception:
            pass

    context.on("page", on_page)


def get_or_create_browser(mode, browsers, browser_errors, headless=True):
    if mode in browsers:
        return browsers[mode]

    if mode in browser_errors:
        raise browser_errors[mode]

    try:
        browser = NaverBrowser(headless=headless, mode=mode)
        block_new_tabs(browser.context)
        print(f"브라우저 준비 완료: {mode}")
        browsers[mode] = browser
        return browser
    except Exception as exc:
        browser_errors[mode] = exc
        raise


def go_to_blog_tab(browser, keyword):
    wait_if_paused()
    browser.page.goto(
        f"https://search.naver.com/search.naver?ssc=tab.blog.all&sm=tab_jum&query={quote(keyword)}",
        wait_until="domcontentloaded",
    )
    controlled_sleep(2.5)
    print("  블로그 탭 URL 직접 이동 완료")


def describe_card(card):
    title = card.get("title", "").strip()
    channel = card.get("channel_name", "").strip()
    if title and channel:
        return f"{title} / {channel}"
    return title or channel or "(텍스트 없음)"


def find_rank_for_keyword(browser, api, keyword, identifier, capture_dir=None, capture_basename=None):
    wait_if_paused()
    api_result = api.find_target_rank(query=keyword, identifier=identifier, max_results=100)
    wait_if_paused()
    api_rank = api_result["api_rank"]
    match_context = build_match_context(identifier, api_result, query=keyword)
    capture_dir = capture_dir or CAPTURE_DIR
    capture_basename = capture_basename or safe_filename(keyword, fallback="키워드없음")

    print(f"[API 정보] {keyword} -> {api_rank if api_rank else '미확인'}")
    if api_result.get("title"):
        print(f"  API 제목: {api_result['title']}")
    if api_result.get("matched_text"):
        print(f"  API 매칭값({api_result['matched_field']}): {api_result['matched_text']}")

    go_to_blog_tab(browser, keyword)

    seen_cards = set()
    running_rank = 0
    empty_count = 0
    viewport_height = browser.get_viewport_height()
    scroll_step = max(500, int(viewport_height * (1 - SCROLL_OVERLAP_RATIO)))
    print(f"  스크롤 간격: {scroll_step}px (viewport={viewport_height}px)")

    for scroll_idx in range(MAX_SCROLL + 1):
        wait_if_paused()
        cards = get_visible_blog_cards(browser.page)
        print(f"\n=== scan {scroll_idx} | 카드 {len(cards)}개 ===")
        for card in cards:
            print(f"  y={card['y']:4.0f}  {describe_card(card)}")

        matched_card = None
        matched_reason = None
        new_count = 0

        for card in cards:
            wait_if_paused()
            key = card.get("key")
            if not key or key in seen_cards:
                continue

            seen_cards.add(key)
            running_rank += 1
            new_count += 1

            reason = match_blog_card(card, match_context)
            print(f"  [{running_rank}위] {describe_card(card)}")
            if reason:
                matched_card = card
                matched_reason = reason
                break

        if matched_card:
            matched_name = matched_card.get("channel_name") or api_result.get("matched_text", "")
            matched_text = matched_card.get("title") or matched_name
            print(f"\n발견! 화면 {running_rank}위 / 기준={matched_reason}")

            capture_rank = running_rank
            if isinstance(api_rank, int):
                capture_rank = min(api_rank, running_rank)

            os.makedirs(capture_dir, exist_ok=True)
            filename = f"{capture_basename}_{capture_rank}.png"
            capture_path = os.path.join(capture_dir, filename)
            capture_clean(browser.page, matched_card, capture_path)

            return {
                "api_rank": api_rank,
                "live_rank": running_rank,
                "note": f"성공({matched_reason})",
                "capture_path": capture_path,
                "matched_name": matched_name,
                "matched_text": matched_text,
                "api_title": api_result["title"],
                "api_field": api_result["matched_field"],
            }

        empty_count = empty_count + 1 if new_count == 0 else 0
        if empty_count >= EMPTY_SCAN_LIMIT:
            print(f"  새 카드가 {EMPTY_SCAN_LIMIT}회 연속 없어 탐색을 종료합니다.")
            break

        if scroll_idx < MAX_SCROLL:
            wait_if_paused()
            browser.page.mouse.wheel(0, scroll_step)
            controlled_sleep(1.8)

    return {
        "api_rank": api_rank,
        "live_rank": None,
        "note": "화면 미발견",
        "capture_path": "",
        "matched_name": "",
        "matched_text": "",
        "api_title": api_result["title"],
        "api_field": api_result["matched_field"],
    }


def find_rank_with_browser_modes(
    api,
    keyword,
    identifier,
    browser_mode,
    browsers,
    browser_errors,
    capture_dir,
    capture_basename,
    headless=True,
):
    attempt_modes = get_browser_attempt_modes(browser_mode)
    attempted = []
    last_result = None
    last_error = None

    for mode in attempt_modes:
        attempted.append(mode)
        try:
            browser = get_or_create_browser(
                mode, browsers=browsers, browser_errors=browser_errors, headless=headless
            )
            result = find_rank_for_keyword(
                browser,
                api,
                keyword,
                identifier,
                capture_dir=capture_dir,
                capture_basename=capture_basename,
            )
            result["used_browser"] = mode
            last_result = result

            if result["live_rank"] is not None:
                if browser_mode == "auto" and len(attempted) > 1:
                    result["note"] = f"{result['note']} | auto재시도={mode}"
                return result

            if browser_mode != "auto":
                return result

            print(f"  auto 재시도 예정: {mode}에서 화면 미발견")
        except Exception as exc:
            last_error = exc
            if browser_mode != "auto":
                raise
            print(f"  auto 재시도 예정: {mode} 실행 실패 ({type(exc).__name__}: {exc})")

    if last_result is not None:
        if browser_mode == "auto" and len(attempted) > 1:
            last_result["note"] = f"{last_result['note']} | auto시도={' > '.join(attempted)}"
        return last_result

    if last_error is not None:
        raise last_error

    raise RuntimeError("사용 가능한 브라우저가 없습니다.")


def main():
    if not os.path.exists(INPUT_FILE):
        print("input.csv 파일이 없습니다.")
        return

    df = pd.read_csv(INPUT_FILE)
    required_columns = ["업체명", "키워드", "식별값"]
    for column in required_columns:
        if column not in df.columns:
            print(f"'{column}' 컬럼이 없습니다.")
            return

    api = NaverBlogAPI()
    browser_mode = get_browser_mode()
    browsers = {}
    browser_errors = {}
    print(f"브라우저 모드: {browser_mode}")

    results = []
    error_rows = []
    undetected_rows = []
    undetected = []

    try:
        for _, row in df.iterrows():
            company = str(row["업체명"]).strip()
            keyword = str(row["키워드"]).strip()
            identifier = str(row["식별값"]).strip()
            safe_company = safe_filename(company, fallback="업체명없음")
            safe_keyword = safe_filename(keyword, fallback="키워드없음")
            company_dir = os.path.join(CAPTURE_DIR, safe_company)
            os.makedirs(company_dir, exist_ok=True)

            try:
                wait_if_paused()
                print(f"\n[검색 시작] {keyword} / {identifier}")
                result = find_rank_with_browser_modes(
                    api=api,
                    keyword=keyword,
                    identifier=identifier,
                    browser_mode=browser_mode,
                    browsers=browsers,
                    browser_errors=browser_errors,
                    capture_dir=company_dir,
                    capture_basename=safe_keyword,
                    headless=True,
                )
                rank_text = result["live_rank"] if result["live_rank"] is not None else "미노출"

                final_capture = result["capture_path"] or ""

                if rank_text == "미노출":
                    undetected.append(f"{company} / {keyword}")

                api_rank_text = result["api_rank"] if result["api_rank"] is not None else "미확인"
                print(
                    f"[결과] {keyword} -> API {api_rank_text} / 화면 {rank_text} / 브라우저 {result.get('used_browser', '')} / 매칭 {result.get('matched_name', '')}"
                )
                row_result = {
                    "업체명": company,
                    "키워드": keyword,
                    "식별값": identifier,
                    "API순위": result["api_rank"] if result["api_rank"] is not None else "",
                    "화면순위": rank_text,
                    "매칭텍스트": result["matched_text"],
                    "API매칭필드": result["api_field"],
                    "API제목": result["api_title"],
                    "캡처파일": final_capture,
                    "사용브라우저": result.get("used_browser", ""),
                    "비고": result["note"],
                }
                results.append(row_result)
                if rank_text == "미노출":
                    undetected_rows.append(dict(row_result))
            except SearchCancelled:
                print("[중단] 사용자 요청으로 검색이 중단되었습니다.")
                break
            except Exception as exc:
                note = f"오류: {type(exc).__name__}: {exc}"
                print(f"[오류] {keyword} -> {note}")
                error_row = {
                    "업체명": company,
                    "키워드": keyword,
                    "식별값": identifier,
                    "API순위": "",
                    "화면순위": "오류",
                    "매칭텍스트": "",
                    "API매칭필드": "",
                    "API제목": "",
                    "캡처파일": "",
                    "사용브라우저": "",
                    "비고": note,
                }
                results.append(error_row)
                error_rows.append(error_row)
    finally:
        for browser in browsers.values():
            try:
                browser.close()
            except Exception:
                pass

    save_report(results, REPORT_PATH, error_rows=error_rows, undetected_rows=undetected_rows)
    print(f"\n완료: {REPORT_PATH}")

    if undetected:
        print("__UNDETECTED_START__")
        for item in undetected:
            print(f"  - {item}")
        print("__UNDETECTED_END__")


if __name__ == "__main__":
    main()
