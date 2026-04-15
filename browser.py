from urllib.parse import quote
from playwright.sync_api import sync_playwright


class NaverBrowser:
    def __init__(self, headless=False, mode="chromium"):
        self.mode = str(mode or "chromium").strip().lower()
        if self.mode not in {"chrome", "chromium"}:
            raise ValueError(f"지원하지 않는 브라우저 모드입니다: {self.mode}")

        self.playwright = sync_playwright().start()
        launch_kwargs = {"headless": headless}
        if self.mode == "chrome":
            launch_kwargs["channel"] = "chrome"

        self.browser = self.playwright.chromium.launch(**launch_kwargs)
        self.context = self.browser.new_context(
            viewport={"width": 1600, "height": 1200},
            locale="ko-KR",
            color_scheme="light"
        )
        self.page = self.context.new_page()

    def search_keyword(self, keyword: str):
        url = f"https://search.naver.com/search.naver?query={quote(keyword)}"
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2500)

    def click_blog_tab(self) -> bool:
        candidates = [
            self.page.get_by_role("link", name="블로그"),
            self.page.locator("a").filter(has_text="블로그"),
            self.page.locator("button").filter(has_text="블로그"),
        ]

        for locator in candidates:
            try:
                count = locator.count()
            except Exception:
                count = 0

            if count == 0:
                continue

            for i in range(count):
                try:
                    el = locator.nth(i)
                    el.scroll_into_view_if_needed()
                    self.page.wait_for_timeout(300)

                    try:
                        el.click(timeout=3000)
                    except Exception:
                        try:
                            el.click(timeout=3000, force=True)
                        except Exception:
                            handle = el.element_handle()
                            if handle:
                                self.page.evaluate("(node) => node.click()", handle)

                    self.page.wait_for_timeout(2500)
                    return True
                except Exception:
                    continue

        return False

    def scroll_down(self, amount=2200):
        self.page.mouse.wheel(0, amount)
        self.page.wait_for_timeout(1800)

    def get_viewport_height(self):
        try:
            return int(self.page.evaluate("() => window.innerHeight || 1200"))
        except Exception:
            return 1200

    def screenshot(self, path: str):
        self.page.screenshot(path=path, full_page=False)

    def get_visible_texts(self):
        script = """
        () => {
            function isVisible(el) {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return false;
                if (rect.bottom < 0 || rect.right < 0) return false;
                if (rect.top > window.innerHeight || rect.left > window.innerWidth) return false;
                return true;
            }

            const results = [];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
                acceptNode(node) {
                    const text = (node.textContent || '').trim();
                    if (!text) return NodeFilter.FILTER_REJECT;
                    const parent = node.parentElement;
                    if (!parent) return NodeFilter.FILTER_REJECT;
                    if (!isVisible(parent)) return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                }
            });

            let node;
            while ((node = walker.nextNode())) {
                const text = (node.textContent || '').trim();
                const range = document.createRange();
                range.selectNodeContents(node);
                const rect = range.getBoundingClientRect();

                if (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    rect.bottom >= 0 &&
                    rect.right >= 0 &&
                    rect.top <= window.innerHeight &&
                    rect.left <= window.innerWidth
                ) {
                    results.push({
                        text,
                        x: rect.left,
                        y: rect.top,
                        width: rect.width,
                        height: rect.height
                    });
                }
            }

            return results;
        }
        """
        try:
            return self.page.evaluate(script)
        except Exception:
            return []

    def close(self):
        self.context.close()
        self.browser.close()
        self.playwright.stop()
