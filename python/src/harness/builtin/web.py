"""內建的網頁工具（toolset 名稱：web）。

設計參考 Claude Code 的 WebFetch：讀取一個網址，把 HTML 轉成好讀的純文字
（保留標題層級、清單與連結）交給 agent。送出請求會把網址（可能含有對話
裡的資料）傳到外部，所以預設需要使用者同意；信任的話可以在 harness.yaml
的 permissions.allow 加上 web_fetch。
"""

import html
import re
import urllib.parse

import aiohttp

from harness import tools

DEFAULT_MAX_CHARS = 20_000
MAX_DOWNLOAD_BYTES = 2_000_000
_TIMEOUT_SECONDS = 30
_USER_AGENT = "agent-harness"

_HIDDEN = re.compile(
    r"<(script|style|noscript|template|svg|head|title)\b[\s\S]*?</\1\s*>",
    re.IGNORECASE,
)
_LINK = re.compile(
    r"""<a\b[^>]*?\bhref\s*=\s*["']([^"']*)["'][^>]*>([\s\S]*?)</a\s*>""",
    re.IGNORECASE,
)
_HEADING = re.compile(r"<h([1-6])\b[^>]*>", re.IGNORECASE)
_BLOCK = re.compile(
    r"</?(p|div|section|article|header|footer|main|nav|aside|h[1-6]|ul|ol|li|"
    r"tr|table|blockquote|pre|hr|dl|dt|dd|figure|figcaption|form)\b[^>]*>",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]*>")
_TITLE = re.compile(r"<title\b[^>]*>([\s\S]*?)</title\s*>", re.IGNORECASE)
_TEXT_TYPE = re.compile(r"[+/](json|xml|javascript|x-yaml|yaml)$")


def _absolute_url(href: str, base: str | None) -> str | None:
    """把連結轉成完整網址；不是 http(s) 連結時回傳 None。"""
    try:
        url = urllib.parse.urljoin(base or "", href)
    except ValueError:
        return None
    if urllib.parse.urlsplit(url).scheme in ("http", "https"):
        return url
    return None


def html_to_text(source: str, base: str | None = None) -> str:
    """把 HTML 轉成純文字。

    拿掉 script / style 等不顯示的內容，標題轉成 `#`，清單項目轉成
    `- `，連結轉成 `[文字](網址)`。

    Args:
        source: 網頁原始碼。
        base: 網頁網址，用來把相對連結轉成完整網址。

    Returns:
        整理過的純文字。
    """

    def link(match: re.Match[str]) -> str:
        label = _TAG.sub("", match.group(2)).strip()
        target = _absolute_url(html.unescape(match.group(1)), base)
        return f"[{label}]({target})" if label and target else match.group(2)

    text = re.sub(r"<!--[\s\S]*?-->", "", source)
    text = _HIDDEN.sub("", text)
    text = _LINK.sub(link, text)
    text = _HEADING.sub(lambda match: f"\n\n{'#' * int(match.group(1))} ", text)
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _BLOCK.sub("\n", text)
    text = html.unescape(_TAG.sub("", text))
    lines = [
        re.sub(r"[ \t\f\v ]+", " ", line).strip() for line in text.split("\n")
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_title(source: str) -> str:
    """取出 HTML 的 <title>。

    Args:
        source: 網頁原始碼。

    Returns:
        標題文字；沒有標題時為空字串。
    """
    match = _TITLE.search(source)
    if not match:
        return ""
    return html.unescape(_TAG.sub("", match.group(1))).strip()


def is_text_type(content_type: str) -> bool:
    """判斷內容類型是不是可以當成文字讀的。

    Args:
        content_type: 不含參數的 MIME 類型，例如 text/html。

    Returns:
        是文字內容（或伺服器沒有說明類型）時為 True。
    """
    return (
        content_type == ""
        or content_type.startswith("text/")
        or _TEXT_TYPE.search(content_type) is not None
    )


@tools.tool(needs_approval=True)
async def web_fetch(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """讀取一個網址的內容。

    HTML 會轉成純文字（保留標題、清單與連結），JSON 與純文字原樣回傳。
    用來查文件、讀使用者給的連結；不支援需要登入的頁面。

    Args:
        url: 完整網址，必須以 http:// 或 https:// 開頭。
        max_chars: 最多回傳幾個字元，內容更長時會截斷。
    """
    if not 100 <= max_chars <= 100_000:
        raise tools.ToolError("max_chars 必須介於 100 到 100000 之間")
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme not in ("http", "https"):
        raise tools.ToolError(f"只支援 http 與 https 網址：{url}")
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5",
    }
    try:
        # trust_env 讓請求走 HTTPS_PROXY 等環境變數設定的代理伺服器。
        async with (
            aiohttp.ClientSession(
                timeout=timeout, headers=headers, trust_env=True
            ) as session,
            session.get(url, allow_redirects=True) as response,
        ):
            if response.status >= 400:
                reason = response.reason or ""
                raise tools.ToolError(
                    f"伺服器回應 {response.status} {reason}".strip()
                )
            # 不用 response.content_type：伺服器沒給類型時它會當成二進位檔。
            content_type = (
                (response.headers.get("Content-Type") or "")
                .split(";")[0]
                .strip()
                .lower()
            )
            if not is_text_type(content_type):
                raise tools.ToolError(
                    f"不支援的內容類型：{content_type}（只能讀取文字內容）"
                )
            raw = await response.content.read(MAX_DOWNLOAD_BYTES)
            charset = response.charset or "utf-8"
            final_url = str(response.url)
    except TimeoutError as error:
        raise tools.ToolError(
            f"無法讀取 {url}：超過 {_TIMEOUT_SECONDS} 秒沒有回應"
        ) from error
    except aiohttp.ClientError as error:
        raise tools.ToolError(f"無法讀取 {url}：{error}") from error
    try:
        source = raw.decode(charset, errors="replace")
    except LookupError:
        source = raw.decode("utf-8", errors="replace")
    is_html = "html" in content_type
    body = html_to_text(source, final_url) if is_html else source.strip()
    header = [f"網址：{final_url}"]
    title = html_title(source) if is_html else ""
    if title:
        header.append(f"標題：{title}")
    content = body or "（沒有文字內容）"
    if len(content) > max_chars:
        omitted = len(content) - max_chars
        note = f"[內容過長，後面還有 {omitted} 個字元沒有顯示]"
        content = f"{content[:max_chars]}\n{note}"
    return "\n".join(header) + "\n\n" + content


TOOLS = [web_fetch]
