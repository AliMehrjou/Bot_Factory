"""
پارسر پروکسی — فرمت‌های پشتیبانی‌شده:
  socks5://user:pass@host:port
  socks5://host:port
  http://user:pass@host:port
  user:pass@host:port        (پیش‌فرض socks5)
  host:port                  (پیش‌فرض socks5)
  host:port:user:pass        (پیش‌فرض socks5 — فرمت رایج پنل‌های ایران)

خروجی: دیکشنری با کلیدهای scheme/host/port/username/password/proxy_string/url
- proxy_string  → فرمت ذخیره در جدول proxies پروژه sender_bot (بدون scheme)
- url           → فرمت کامل برای python-socks و LOGIN_PROXY_URL
"""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedProxy:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def proxy_string(self) -> str:
        """فرمت sender_bot: user:pass@host:port"""
        if self.username:
            return f"{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.host}:{self.port}"

    @property
    def url(self) -> str:
        if self.username:
            return f"{self.scheme}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.scheme}://{self.host}:{self.port}"


_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_HOSTNAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _valid_host(host: str) -> bool:
    if not host:
        return False
    if _IPV4.match(host):
        return all(0 <= int(p) <= 255 for p in host.split("."))
    return bool(_HOSTNAME.match(host)) and len(host) <= 253

# بازنویسی کامل تابع `parse_proxy_line` در فایل `utils/proxy_parse.py` (محدوده جایگزینی: خطوط ۴۴ تا ۸۶)

def parse_proxy_line(line: str, default_scheme: str = "socks5") -> Optional[ParsedProxy]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    scheme = default_scheme
    if "://" in line:
        scheme_raw, _, rest = line.partition("://")
        scheme = scheme_raw.lower()
        if scheme not in ("socks5", "socks5h", "socks4", "http", "https"):
            return None
        if scheme == "socks5h": scheme = "socks5"
        if scheme == "https": scheme = "http"
        line = rest

    # تلاش اول: فرمت user:pass@host:port
    if "@" in line:
        creds, _, hostport = line.rpartition("@")
        username, sep, password = creds.partition(":")
        
        # اگر جداکننده کاربر/رمز درست بود
        if sep == ":":
            if ":" in hostport:
                host, _, port_s = hostport.rpartition(":")
                if _valid_host(host) and port_s.isdigit():
                    port = int(port_s)
                    if 1 <= port <= 65535:
                        return ParsedProxy(scheme, host, port, username, password)
            else:
                # ممکن است پورت نوشته نشده باشد (خیلی بعید برای پروکسی، اما هندل می‌کنیم)
                pass

    # تلاش دوم: فرمت host:port یا host:port:user:pass
    parts = line.split(":")
    if len(parts) >= 2:
        host = parts[0]
        port_s = parts[1]
        
        if _valid_host(host) and port_s.isdigit():
            port = int(port_s)
            if 1 <= port <= 65535:
                if len(parts) == 2:
                    return ParsedProxy(scheme, host, port)
                
                # پشتیبانی از فرمت host:port:user:pass (حتی اگر پسورد شامل دو نقطه باشد)
                if len(parts) >= 4:
                    username = parts[2]
                    password = ":".join(parts[3:])
                    return ParsedProxy(scheme, host, port, username, password)

    return None


def parse_proxies_text(text: str, default_scheme: str = "socks5") -> tuple:
    """
    چند خط پروکسی را می‌خواند.
    خروجی: (لیست ParsedProxy, لیست خط‌های نامعتبر)
    """
    parsed, invalid = [], []
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        if not line or line.startswith("#"):
            continue
        p = parse_proxy_line(line, default_scheme)
        if p is None:
            invalid.append(raw.strip())
        else:
            parsed.append(p)
    # حذف تکراری با proxy_string
    seen = set()
    unique = []
    for p in parsed:
        if p.proxy_string not in seen:
            seen.add(p.proxy_string)
            unique.append(p)
    return unique, invalid


def mask_proxy(proxy_string: str) -> str:
    """نمایش امن در گزارش‌ها: user:**@host:port"""
    if "@" in proxy_string:
        creds, _, hostport = proxy_string.rpartition("@")
        user = creds.split(":", 1)[0] if ":" in creds else creds
        return f"{user}:***@{hostport}"
    return proxy_string
