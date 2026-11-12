"""內建的網頁工具（toolset 名稱：web）。

設計參考 Claude Code 的 WebFetch：讀取一個網址，把 HTML 轉成好讀的純文字
（保留標題層級、清單與連結）交給 agent。送出請求會把網址（可能含有對話
裡的資料）傳到外部，所以預設需要使用者同意；信任的話可以在 harness.yaml
的 permissions.allow 加上 web_fetch。

網頁版多人使用時改用 guarded_fetch_tool：不能讀本機、內網與雲端主機的
中繼資料服務（見 blocked_address）。
"""

import asyncio
import dataclasses
import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request

import aiohttp
import aiohttp.abc

from harness import tools

DEFAULT_MAX_CHARS = 20_000
MAX_DOWNLOAD_BYTES = 2_000_000
_TIMEOUT_SECONDS = 30
_USER_AGENT = "agent-harness"
_MAX_REDIRECTS = 10
_REDIRECTS = (301, 302, 303, 307, 308)

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
    return await _fetch(url, max_chars, guard=False)


async def _guarded_fetch(url: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    return await _fetch(url, max_chars, guard=True)


def guarded_fetch_tool() -> tools.Tool:
    """名稱、說明與參數都和 web_fetch 相同，但不能讀內部網路的版本。

    給網頁版多人使用時用（見 harness.web.workspace）：伺服器通常在內網
    裡，不能讓成員（或被網頁內容誘導的 agent）透過它讀取本機、內網服務
    或雲端主機的中繼資料（169.254.169.254）。每一次轉址都重新檢查，
    連線時也只連到檢查過的位址（避免 DNS 在檢查後換掉）。

    Returns:
        新的工具。
    """
    return dataclasses.replace(web_fetch, func=_guarded_fetch)


def blocked_address(address: str) -> bool:
    """這個 IP 是不是內部網路的位址（不能讓 guarded_fetch_tool 連線）。

    擋下 loopback、私有網路（RFC 1918）、link-local（含 169.254.169.254）、
    IPv6 unique-local、CGNAT（100.64.0.0/10）、未指定位址（0.0.0.0、::）
    與群播等所有不是公開網際網路的位址；IPv4 對應的 IPv6 位址
    （::ffff:127.0.0.1）照 IPv4 判斷。看不懂的位址也擋下。

    Args:
        address: IP 位址（IPv6 可以帶 %scope）。

    Returns:
        要擋下時為 True。
    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


class _Blocked(OSError):
    """連線的位址在內部網路。"""


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """解析網址的主機時再檢查一次位址，連線只會連到檢查過的 IP。

    只檢查要讀取的主機（hosts）；經過代理伺服器時，代理伺服器本身可以
    在內網。
    """

    def __init__(self) -> None:
        self.hosts: set[str] = set()
        self._inner = aiohttp.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await self._inner.resolve(host, port, family)
        if host.lower() in self.hosts:
            for item in results:
                if blocked_address(item["host"]):
                    raise _Blocked(_blocked_message(host, item["host"]))
        return results

    async def close(self) -> None:
        await self._inner.close()


def _blocked_message(host: str, address: str) -> str:
    return (
        f"不能讀取內部網路的位址：{host}（{address}）。為了安全，這裡不允許"
        "讀取本機、內網或雲端主機中繼資料的網址，請改用公開網路上的網址；"
        "確實需要時請管理者在 harness.yaml 設定 web: "
        "{allowPrivateNetworks: true}"
    )


async def _check_host(url: str) -> str:
    """確認網址的主機不在內部網路，回傳主機名稱（小寫）。

    Raises:
        tools.ToolError: 主機在內部網路，或找不到主機。
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host:
        raise tools.ToolError(f"網址沒有主機名稱：{url}")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        addresses = [host]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as error:
            if _uses_proxy(url, host):
                # 只有代理伺服器查得到這個主機：交給代理伺服器。
                return host
            raise tools.ToolError(f"無法讀取 {url}：找不到主機") from error
        addresses = [str(info[4][0]) for info in infos]
    for address in addresses:
        if blocked_address(address):
            raise tools.ToolError(_blocked_message(host, address))
    return host


def _uses_proxy(url: str, host: str) -> bool:
    scheme = urllib.parse.urlsplit(url).scheme
    proxies = urllib.request.getproxies()
    return scheme in proxies and not urllib.request.proxy_bypass(host)


async def _fetch(url: str, max_chars: int, guard: bool) -> str:
    """web_fetch 的本體；guard 為 True 時擋下內部網路（見 blocked_address）。"""
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
    resolver = _PinnedResolver() if guard else None
    connector = aiohttp.TCPConnector(resolver=resolver) if guard else None
    try:
        # trust_env 讓請求走 HTTPS_PROXY 等環境變數設定的代理伺服器。
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            trust_env=True,
            connector=connector,
        ) as session:
            return await _follow(session, url, max_chars, resolver)
    except TimeoutError as error:
        raise tools.ToolError(
            f"無法讀取 {url}：超過 {_TIMEOUT_SECONDS} 秒沒有回應"
        ) from error
    except aiohttp.ClientError as error:
        blocked = _find_blocked(error)
        if blocked is not None:
            raise tools.ToolError(str(blocked)) from error
        raise tools.ToolError(f"無法讀取 {url}：{error}") from error
    except _Blocked as error:
        raise tools.ToolError(str(error)) from error


async def _follow(
    session: aiohttp.ClientSession,
    url: str,
    max_chars: int,
    resolver: _PinnedResolver | None,
) -> str:
    """送出請求；要檢查位址時自己處理轉址，每一站都重新檢查。"""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        if resolver is not None:
            resolver.hosts.add(await _check_host(current))
        async with session.get(
            current, allow_redirects=resolver is None
        ) as response:
            location = response.headers.get("Location")
            if (
                resolver is not None
                and response.status in _REDIRECTS
                and location
            ):
                current = urllib.parse.urljoin(str(response.url), location)
                scheme = urllib.parse.urlsplit(current).scheme
                if scheme not in ("http", "https"):
                    raise tools.ToolError(
                        f"只支援 http 與 https 網址：{current}"
                    )
                continue
            return await _read(response, max_chars)
    raise tools.ToolError(f"無法讀取 {url}：轉址超過 {_MAX_REDIRECTS} 次")


async def _read(response: aiohttp.ClientResponse, max_chars: int) -> str:
    """把回應轉成給模型看的文字。"""
    if response.status >= 400:
        reason = response.reason or ""
        raise tools.ToolError(f"伺服器回應 {response.status} {reason}".strip())
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


def _find_blocked(error: BaseException) -> _Blocked | None:
    """aiohttp 把解析錯誤包起來；找出裡面的 _Blocked。"""
    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, _Blocked):
            return current
        inner = getattr(current, "os_error", None)
        current = (
            inner
            if isinstance(inner, BaseException)
            else (current.__cause__ or current.__context__)
        )
    return None


TOOLS = [web_fetch]
