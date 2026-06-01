import os
import re
import html
import requests


API_URL = "https://openapi.naver.com/v1/search/blog.json"


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()


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
        gathered = []
        start = 1

        while len(gathered) < max_results and start <= 1000:
            remain = max_results - len(gathered)
            display = min(100, remain)

            data = self.search(query=query, start=start, display=display, sort="sim")
            items = data.get("items", [])

            if not items:
                break

            gathered.extend(items)
            start += display

            if len(items) < display:
                break

        for idx, item in enumerate(gathered, start=1):
            bloggername = strip_html(item.get("bloggername", ""))
            bloggerlink = item.get("bloggerlink", "")
            title = strip_html(item.get("title", ""))

            if identifier_norm in normalize(bloggername):
                return {
                    "api_rank": idx,
                    "matched_field": "bloggername",
                    "matched_text": bloggername,
                    "title": title,
                    "bloggerlink": bloggerlink,
                    "items": gathered,
                }

            if identifier_norm in normalize(bloggerlink):
                return {
                    "api_rank": idx,
                    "matched_field": "bloggerlink",
                    "matched_text": bloggerlink,
                    "title": title,
                    "bloggerlink": bloggerlink,
                    "items": gathered,
                }

        return {
            "api_rank": None,
            "matched_field": "",
            "matched_text": "",
            "title": "",
            "bloggerlink": "",
            "items": gathered,
        }