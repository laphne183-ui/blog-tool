import unittest

from main import API_MAX_RESULTS_DEFAULT, build_match_context, extract_blog_id, match_blog_card
from naver_api import NaverBlogAPI, resolve_blog_id


def make_card(blog_id="soso2226", channel_name="법무법인이일"):
    post_url = f"https://blog.naver.com/{blog_id}/223123456789"
    return {
        "channel_name": channel_name,
        "title": "법인체납 시 발생하는 불이익과 대응 방법",
        "text": f"{channel_name} 법인체납 시 발생하는 불이익과 대응 방법",
        "link_texts": [channel_name],
        "hrefs": [post_url],
        "primary_link": post_url,
    }


class BlogIdentifierTests(unittest.TestCase):
    def test_api_default_searches_to_naver_limit(self):
        self.assertEqual(API_MAX_RESULTS_DEFAULT, 1000)

    def test_resolve_raw_blog_id(self):
        self.assertEqual(resolve_blog_id("soso2226"), "soso2226")

    def test_resolve_full_blog_url(self):
        self.assertEqual(
            resolve_blog_id("https://blog.naver.com/soso2226"),
            "soso2226",
        )

    def test_extract_postview_blog_id(self):
        url = "https://blog.naver.com/PostView.naver?blogId=soso2226&logNo=223123456789"
        self.assertEqual(extract_blog_id(url), "soso2226")

    def test_company_name_is_not_mistaken_for_blog_id(self):
        self.assertEqual(resolve_blog_id("법무법인이일"), "")

    def test_raw_id_matches_screen_card_without_api_result(self):
        context = build_match_context("soso2226", {}, query="법인체납")
        self.assertEqual(match_blog_card(make_card(), context), "identifier_blog_id")

    def test_full_url_matches_screen_card_without_api_result(self):
        context = build_match_context(
            "https://blog.naver.com/soso2226",
            {},
            query="법인체납",
        )
        self.assertEqual(match_blog_card(make_card(), context), "identifier_blog_id")

    def test_postview_card_url_matches_raw_id(self):
        postview_url = (
            "https://blog.naver.com/PostView.naver?"
            "blogId=soso2226&logNo=223123456789"
        )
        card = make_card()
        card["hrefs"] = [postview_url]
        card["primary_link"] = postview_url
        context = build_match_context("soso2226", {}, query="법인체납")
        self.assertEqual(match_blog_card(card, context), "identifier_blog_id")

    def test_legacy_company_name_still_matches_channel(self):
        context = build_match_context("법무법인이일", {}, query="법인체납")
        self.assertEqual(match_blog_card(make_card(), context), "identifier_channel")

    def test_other_blog_does_not_match_id(self):
        context = build_match_context("soso2226", {}, query="법인체납")
        self.assertIsNone(match_blog_card(make_card(blog_id="someone_else"), context))

    def test_api_finds_raw_id_beyond_300_and_stops_on_match(self):
        api = object.__new__(NaverBlogAPI)
        calls = []

        def fake_search(query, start=1, display=100, sort="sim"):
            calls.append(start)
            items = []
            for rank in range(start, start + display):
                blog_id = "soso2226" if rank == 819 else f"other_{rank}"
                items.append(
                    {
                        "title": f"결과 {rank}",
                        "bloggername": f"블로그 {rank}",
                        "bloggerlink": f"https://blog.naver.com/{blog_id}",
                        "link": f"https://blog.naver.com/{blog_id}/223000{rank:04d}",
                    }
                )
            return {"items": items}

        api.search = fake_search
        result = api.find_target_rank("법인체납", "soso2226", max_results=1000)

        self.assertEqual(result["api_rank"], 819)
        self.assertEqual(calls, [1, 101, 201, 301, 401, 501, 601, 701, 801])


if __name__ == "__main__":
    unittest.main()
