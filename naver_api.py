import os
import re
import html
import requests


API_URL = "https://openapi.naver.com/v1/search/blog.json"


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()


def normalize_url(text: str) -> str:
    url = str(text or "").strip()
    url = re.sub(r"[?#].*$", "", url)
    return url.rstrip("/").lower()


def extract_blog_id(text: str) -> str:
    raw_text = str(text or "").strip().lower()
    query_match = re.search(r"[?&]blogid=([^&#]+)", raw_text)
    if query_match:
        return normalize(query_match.group(1))

    normalized_url = normalize_url(raw_text)
    match = re.search(r"(?:https?://)?(?:m\.)?blog\.naver\.com/([^/?#]+)", normalized_url)
    if not match:
        return ""

    blog_id = normalize(match.group(1))
    if blog_id.lower() in {"postview.naver", "postlist.naver"}:
        return ""
    return blog_id


def resolve_blog_id(identifier: str) -> str:
    """식별값이 네이버 블로그 URL 또는 원시 블로그 ID이면 ID를 반환한다."""
    url_blog_id = extract_blog_id(identifier)
    if url_blog_id:
        return url_blog_id

    candidate = normalize(identifier)
    if re.fullmatch(r"[a-z0-9._-]+", candidate):
        return candidate
    return ""


def extract_post_id(url: str) -> str:
    url = str(url or "").lower()
    match = re.search(r"blog\.naver\.com/[^/?#]+/(\d{5,})", url)
    if match:
        return match.group(1)
    match = re.search(r"logno=(\d{5,})", url)
    if match:
        return match.group(1)
    return ""


def strip_html(text: str) -> str:
    text = html.unescape(text or "")
    return re.sub(r"<.*?>", "", text)


class NaverBlogAPI:
    def __init__(self):
        self.client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()

        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 필요합니다."
            )

    def search(self, query: str, start: int = 1, display: int = 100, sort: str = "sim"):
        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }
        params = {
            "query": query,
            "display": display,
            "start": start,
            "sort": sort,
        }

        res = requests.get(API_URL, headers=headers, params=params, timeout=15)
        res.raise_for_status()
        return res.json()

    def find_target_rank(
        self,
        query: str,
        identifier: str,
        max_results: int = 100,
    ):
        identifier_norm = normalize(identifier)
        identifier_blog_id = resolve_blog_id(identifier)
        target_post_id = extract_post_id(identifier)
        gathered = []
        start = 1

        def build_result(idx, item, matched_field, matched_text, match_scope):
            title = strip_html(item.get("title", ""))
            bloggerlink = item.get("bloggerlink", "")
            post_link = item.get("link", "")
            return {
                "api_rank": idx,
                "matched_field": matched_field,
                "matched_text": matched_text,
                "match_scope": match_scope,
                "title": title,
                "bloggerlink": bloggerlink,
                "post_link": post_link,
                "post_id": extract_post_id(post_link),
                "target_post_id": target_post_id,
                "items": gathered,
            }

        while len(gathered) < max_results and start <= 1000:
            remain = max_results - len(gathered)
            display = min(100, remain)

            data = self.search(query=query, start=start, display=display, sort="sim")
            items = data.get("items", [])

            if not items:
                break

            for item in items:
                gathered.append(item)
                idx = len(gathered)
                bloggername = strip_html(item.get("bloggername", ""))
                bloggerlink = item.get("bloggerlink", "")
                post_link = item.get("link", "")
                post_id = extract_post_id(post_link)
                item_blog_id = extract_blog_id(post_link) or extract_blog_id(bloggerlink)

                if target_post_id:
                    if post_id == target_post_id:
                        return build_result(idx, item, "post_link", post_link, "post")
                    continue

                if identifier_blog_id and item_blog_id == identifier_blog_id:
                    return build_result(idx, item, "bloggerlink", bloggerlink, "blog")

                if not identifier_blog_id and identifier_norm and identifier_norm in normalize(bloggername):
                    return build_result(idx, item, "bloggername", bloggername, "blog")

                if not identifier_blog_id and identifier_norm and identifier_norm in normalize(bloggerlink):
                    return build_result(idx, item, "bloggerlink", bloggerlink, "blog")

            start += display

            if len(items) < display:
                break

        return {
            "api_rank": None,
            "matched_field": "",
            "matched_text": "",
            "match_scope": "post" if target_post_id else "",
            "title": "",
            "bloggerlink": "",
            "post_link": "",
            "post_id": "",
            "target_post_id": target_post_id,
            "items": gathered,
        }
