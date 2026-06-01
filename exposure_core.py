from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
import html
import json
import os
from pathlib import Path
import re
import time
from typing import Iterable
from urllib import parse, request
import xml.etree.ElementTree as ET

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    PlaywrightTimeoutError = Exception
    sync_playwright = None


APP_DIR = Path(__file__).resolve().parent
SELECTORS_PATH = APP_DIR / "selectors.json"
VALID_BROWSER_MODES = {"auto", "chrome", "chromium"}
MAX_SEARCH_SCROLLS = 6
EMPTY_SCAN_LIMIT = 2
SCROLL_AMOUNT = 1800
DEFAULT_LIMIT = 30
HTTP_SEARCH_TIMEOUT = 10


@dataclass
class BlogTarget:
    raw: str
    blog_id: str


@dataclass
class BlogPost:
    blog_id: str
    title: str
    url: str
    post_id: str
    published: str = ""


@dataclass
class ExposureResult:
    blog_id: str
    title: str
    url: str
    post_id: str
    published: str
    status: str
    rank: int | None
    matched_url: str = ""
    note: str = ""


class ExposureCancelled(Exception):
    pass


def get_app_base_dir() -> Path:
    if getattr(__import__("sys"), "frozen", False):
        return Path(__import__("sys").executable).resolve().parent
    return APP_DIR


def configure_playwright_browsers() -> None:
    base_dir = get_app_base_dir()
    bundled = base_dir / "browsers"
    if bundled.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled))


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def normalize_url(text: str) -> str:
    url = html.unescape(str(text or "").strip())
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/").lower()


def extract_blog_id(text: str) -> str:
    value = html.unescape(str(text or "").strip())
    parsed = parse.urlparse(value if "://" in value else f"https://{value}")
    qs = parse.parse_qs(parsed.query)
    for key in ("blogId", "blogid"):
        if qs.get(key):
            return str(qs[key][0]).strip()

    match = re.search(r"(?:https?://)?(?:m\.)?blog\.naver\.com/([^/?#]+)", value, re.I)
    if match:
        candidate = match.group(1).strip()
        if candidate.lower() not in {"postview.naver", "bloglist.naver"}:
            return candidate

    if re.fullmatch(r"[A-Za-z0-9._-]{2,}", value):
        return value
    return ""


def extract_post_id(text: str) -> str:
    value = html.unescape(str(text or ""))
    parsed = parse.urlparse(value if "://" in value else f"https://{value}")
    qs = parse.parse_qs(parsed.query)
    for key in ("logNo", "logno"):
        if qs.get(key):
            return re.sub(r"\D", "", str(qs[key][0]))

    match = re.search(r"blog\.naver\.com/[^/?#]+/(\d{5,})", value, re.I)
    if match:
        return match.group(1)
    return ""


def parse_targets(raw_text: str) -> list[BlogTarget]:
    seen: set[str] = set()
    targets: list[BlogTarget] = []
    for line in str(raw_text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        blog_id = extract_blog_id(raw)
        if not blog_id:
            continue
        key = blog_id.lower()
        if key in seen:
            continue
        seen.add(key)
        targets.append(BlogTarget(raw=raw, blog_id=blog_id))
    return targets


def _format_pub_date(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        return dt.strftime("%Y.%m.%d")
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value).strftime("%Y.%m.%d")
    except Exception:
        return value[:10].replace("-", ".")


def _clean_title(value: str) -> str:
    cleaned = html.unescape(str(value or ""))
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def fetch_recent_posts(blog_id: str, limit: int = DEFAULT_LIMIT) -> list[BlogPost]:
    cleaned_id = str(blog_id or "").strip()
    if not cleaned_id:
        raise ValueError("blog_id가 비어 있습니다.")

    rss_url = f"https://rss.blog.naver.com/{parse.quote(cleaned_id)}.xml"
    req = request.Request(
        rss_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            )
        },
        method="GET",
    )
    with request.urlopen(req, timeout=15) as response:
        xml_text = response.read().decode("utf-8", errors="replace")

    root = ET.fromstring(xml_text)
    posts: list[BlogPost] = []
    seen_ids: set[str] = set()
    for item in root.findall(".//item"):
        title = _clean_title(item.findtext("title", ""))
        link = html.unescape(str(item.findtext("link", "") or "")).strip()
        post_id = extract_post_id(link)
        published = _format_pub_date(item.findtext("pubDate", ""))
        if not title or not link:
            continue
        dedupe_key = post_id or normalize_url(link)
        if dedupe_key in seen_ids:
            continue
        seen_ids.add(dedupe_key)
        posts.append(
            BlogPost(
                blog_id=cleaned_id,
                title=title,
                url=link,
                post_id=post_id,
                published=published,
            )
        )
        if len(posts) >= limit:
            break

    return posts


def _extract_post_ids_from_search_html(raw_html: str) -> list[str]:
    decoded = html.unescape(str(raw_html or ""))
    for _ in range(2):
        decoded = parse.unquote(decoded)

    found: list[str] = []
    patterns = [
        r"blog\.naver\.com/[A-Za-z0-9._-]+/(\d{5,})",
        r"[?&]logNo=(\d{5,})",
        r"[?&]logno=(\d{5,})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, decoded, re.I):
            post_id = match.group(1)
            if post_id not in found:
                found.append(post_id)
    return found


def _quick_check_post_html(post: BlogPost) -> ExposureResult | None:
    if not post.post_id:
        return None

    url = f"https://search.naver.com/search.naver?ssc=tab.blog.all&sm=tab_jum&query={parse.quote(post.title)}"
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        method="GET",
    )

    try:
        with request.urlopen(req, timeout=HTTP_SEARCH_TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    decoded = parse.unquote(html.unescape(raw))
    ids = _extract_post_ids_from_search_html(decoded)
    if post.post_id in ids:
        return ExposureResult(
            blog_id=post.blog_id,
            title=post.title,
            url=post.url,
            post_id=post.post_id,
            published=post.published,
            status="노출",
            rank=ids.index(post.post_id) + 1,
            matched_url=post.url,
            note="빠른 확인(logNo)",
        )

    if "blog.naver.com" in decoded or "sp_nblog_" in decoded or "view_wrap" in decoded:
        return ExposureResult(
            blog_id=post.blog_id,
            title=post.title,
            url=post.url,
            post_id=post.post_id,
            published=post.published,
            status="누락",
            rank=None,
            note="빠른 확인: 첫 화면 logNo 미발견",
        )

    return None


def _load_selector_config() -> dict:
    config = {
        "blog_card_selector": "[data-template-id=\"ugcItem\"]",
        "channel_selector": ".sds-comps-profile-info-title-text",
        "title_selector_candidates": [
            "a.title_link",
            ".title_area a[href]",
            "a.api_txt_lines.total_tit",
            "a[data-testid='title']",
            ".sds-comps-text-type-headline1 a[href]",
            ".title_group a[href]",
        ],
        "ignored_link_labels": ["keep", "공유", "더보기", "열기", "닫기", "이전", "다음", "댓글", "좋아요"],
    }
    path = get_app_base_dir() / "selectors.json"
    if not path.exists():
        path = SELECTORS_PATH
    if not path.exists():
        return config

    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            loaded = json.loads(path.read_text(encoding=encoding))
            break
        except UnicodeDecodeError:
            continue
        except Exception:
            return config
    else:
        return config

    if isinstance(loaded, dict):
        for key in ("blog_card_selector", "channel_selector"):
            value = str(loaded.get(key, "")).strip()
            if value:
                config[key] = value
        for key in ("title_selector_candidates", "ignored_link_labels"):
            value = loaded.get(key)
            if isinstance(value, list):
                cleaned = [str(item).strip() for item in value if str(item).strip()]
                if cleaned:
                    config[key] = cleaned
    return config


class NaverExposureChecker:
    def __init__(self, browser_mode: str = "auto", headless: bool = True):
        if sync_playwright is None:
            raise RuntimeError("Playwright가 설치되어 있지 않습니다.")
        configure_playwright_browsers()
        self.browser_mode = str(browser_mode or "auto").strip().lower()
        if self.browser_mode not in VALID_BROWSER_MODES:
            self.browser_mode = "auto"
        self.headless = headless
        self.selector_config = _load_selector_config()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _attempt_modes(self) -> list[str]:
        if self.browser_mode == "chrome":
            return ["chrome"]
        if self.browser_mode == "chromium":
            return ["chromium"]
        return ["chrome", "chromium"]

    def start(self) -> None:
        if self.page is not None:
            return

        self.playwright = sync_playwright().start()
        last_error = None
        for mode in self._attempt_modes():
            try:
                kwargs = {"headless": self.headless}
                if mode == "chrome":
                    kwargs["channel"] = "chrome"
                self.browser = self.playwright.chromium.launch(**kwargs)
                self.context = self.browser.new_context(
                    viewport={"width": 1600, "height": 1200},
                    locale="ko-KR",
                    color_scheme="light",
                )
                self.page = self.context.new_page()
                return
            except Exception as exc:
                last_error = exc
                try:
                    if self.browser:
                        self.browser.close()
                except Exception:
                    pass
                self.browser = None

        try:
            self.playwright.stop()
        except Exception:
            pass
        raise RuntimeError(f"브라우저 실행 실패: {last_error}")

    def close(self) -> None:
        for obj in (self.context, self.browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def _go_to_blog_search(self, query: str) -> None:
        assert self.page is not None
        url = f"https://search.naver.com/search.naver?ssc=tab.blog.all&sm=tab_jum&query={parse.quote(query)}"
        self.page.goto(url, wait_until="domcontentloaded", timeout=12000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=800)
        except PlaywrightTimeoutError:
            pass
        self.page.wait_for_timeout(450)

    def _visible_cards(self) -> list[dict]:
        assert self.page is not None
        blog_card_selector = self.selector_config["blog_card_selector"]
        channel_selector = self.selector_config["channel_selector"]
        title_selectors = self.selector_config["title_selector_candidates"]
        ignored_labels = self.selector_config["ignored_link_labels"]

        try:
            return self.page.evaluate(
                """
                ([blogCardSelector, channelSelector, titleSelectors, ignoredLabels]) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, '').toLowerCase();
                    const isIgnored = (value) => {
                        const normalized = normalize(value);
                        if (!normalized) return true;
                        return ignoredLabels.some((label) => normalized === normalize(label) || normalized.includes(normalize(label)));
                    };
                    const candidates = Array.from(document.querySelectorAll(blogCardSelector));
                    const cards = candidates.length
                        ? candidates
                        : Array.from(document.querySelectorAll('li.bx, .api_subject_bx, .total_wrap, [class*="item"]'));

                    return cards
                        .map((card, index) => {
                            const rect = card.getBoundingClientRect();
                            if (rect.bottom < -120 || rect.top > window.innerHeight + 180) return null;

                            const channelCandidates = Array.from(card.querySelectorAll(channelSelector))
                                .map((el) => (el.textContent || '').trim())
                                .filter(Boolean);

                            const links = Array.from(card.querySelectorAll('a[href]'))
                                .map((anchor) => {
                                    const text = (anchor.innerText || anchor.textContent || '').trim();
                                    const title = (anchor.getAttribute('title') || '').trim();
                                    return {
                                        text,
                                        title,
                                        label: title || text,
                                        href: anchor.href || anchor.getAttribute('href') || ''
                                    };
                                })
                                .filter((item) => item.href || item.label);

                            let titleNode = null;
                            for (const selector of titleSelectors) {
                                titleNode = card.querySelector(selector);
                                if (titleNode) break;
                            }

                            const titleFromNode = titleNode
                                ? ((titleNode.getAttribute('title') || titleNode.innerText || titleNode.textContent || '').trim())
                                : '';
                            const usefulLink = links.find((item) => item.label && !isIgnored(item.label));
                            const title = titleFromNode || (usefulLink ? usefulLink.label : '');
                            const primary = (titleNode && (titleNode.href || titleNode.getAttribute('href'))) || (usefulLink ? usefulLink.href : '');

                            return {
                                dom_index: index,
                                y: rect.top,
                                title,
                                primary_link: primary || '',
                                hrefs: links.map((item) => item.href).filter(Boolean),
                                link_texts: links.map((item) => item.label).filter(Boolean),
                                channel_candidates: channelCandidates,
                                text: (card.innerText || '').trim()
                            };
                        })
                        .filter(Boolean)
                        .sort((a, b) => a.y - b.y);
                }
                """,
                [blog_card_selector, channel_selector, title_selectors, ignored_labels],
            )
        except Exception:
            return []

    def check_post(self, post: BlogPost, cancel_event=None) -> ExposureResult:
        if cancel_event is not None and cancel_event.is_set():
            raise ExposureCancelled("사용자 요청으로 중단")

        quick_result = _quick_check_post_html(post)
        if quick_result is not None:
            return quick_result

        if self.page is None:
            self.start()

        target_post_id = post.post_id
        target_blog_id = normalize(post.blog_id)
        target_title = normalize(post.title)
        self._go_to_blog_search(post.title)

        seen_cards: set[str] = set()
        running_rank = 0
        empty_count = 0

        for scroll_index in range(MAX_SEARCH_SCROLLS + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise ExposureCancelled("사용자 요청으로 중단")

            cards = self._visible_cards()
            new_count = 0
            for card in cards:
                key = "|".join(
                    [
                        normalize(card.get("title", "")),
                        normalize_url(card.get("primary_link", "")),
                        normalize(card.get("text", ""))[:80],
                    ]
                )
                if not key or key in seen_cards:
                    continue
                seen_cards.add(key)
                new_count += 1
                running_rank += 1

                hrefs = [card.get("primary_link", "")] + list(card.get("hrefs", []) or [])
                hrefs_norm = [normalize_url(href) for href in hrefs if href]
                post_ids = {extract_post_id(href) for href in hrefs_norm}
                blog_ids = {normalize(extract_blog_id(href)) for href in hrefs_norm}
                post_ids.discard("")
                blog_ids.discard("")

                if target_post_id and target_post_id in post_ids:
                    return ExposureResult(
                        blog_id=post.blog_id,
                        title=post.title,
                        url=post.url,
                        post_id=post.post_id,
                        published=post.published,
                        status="노출",
                        rank=running_rank,
                        matched_url=next((href for href in hrefs_norm if target_post_id in href), ""),
                        note="logNo 일치",
                    )

                card_title = normalize(card.get("title", ""))
                if not target_post_id and target_blog_id in blog_ids and target_title and target_title in card_title:
                    return ExposureResult(
                        blog_id=post.blog_id,
                        title=post.title,
                        url=post.url,
                        post_id=post.post_id,
                        published=post.published,
                        status="노출",
                        rank=running_rank,
                        matched_url=hrefs_norm[0] if hrefs_norm else "",
                        note="blogId+제목 일치",
                    )

            empty_count = empty_count + 1 if new_count == 0 else 0
            if empty_count >= EMPTY_SCAN_LIMIT:
                break
            if scroll_index < MAX_SEARCH_SCROLLS:
                assert self.page is not None
                self.page.mouse.wheel(0, SCROLL_AMOUNT)
                self.page.wait_for_timeout(500)

        return ExposureResult(
            blog_id=post.blog_id,
            title=post.title,
            url=post.url,
            post_id=post.post_id,
            published=post.published,
            status="누락",
            rank=None,
            note="검색 결과에서 logNo 미발견",
        )


def collect_posts_for_targets(targets: Iterable[BlogTarget], limit: int, progress=None) -> list[BlogPost]:
    posts: list[BlogPost] = []
    for target in targets:
        if progress:
            progress(f"{target.blog_id} 최근 글 수집 중")
        blog_posts = fetch_recent_posts(target.blog_id, limit=limit)
        posts.extend(blog_posts)
        time.sleep(0.2)
    return posts
