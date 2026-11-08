#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网络访问的安全下载工具：URL 校验、重定向逐跳校验、体积上限、缓存与重试。"""

import hashlib
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple

ALLOWED_URL_SCHEMES = ('http', 'https')
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
DOWNLOAD_RETRIES = 2
CACHE_KEY_CHARS = 12
CACHE_TTL_SECONDS = 7 * 24 * 3600
USER_AGENT = 'talent-development-plan/1.0'

_DNS_CACHE: Dict[str, bool] = {}


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return True
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _host_is_public(host: str) -> bool:
    if not host or host == 'localhost' or host.endswith('.localhost'):
        return False

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _is_public_ip(host)

    if host in _DNS_CACHE:
        return _DNS_CACHE[host]

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        _DNS_CACHE[host] = True
        return True

    public = all(_is_public_ip(info[4][0]) for info in infos)
    _DNS_CACHE[host] = public
    return public


def validate_download_url(url: str) -> bool:
    """仅允许 http/https 且解析到公网地址的链接，阻断 file:// 与内网/回环地址。"""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES or not parsed.netloc:
        return False
    return _host_is_public((parsed.hostname or '').strip().lower())


def safe_cache_name(url: str) -> Optional[str]:
    """由 URL 生成安全缓存文件名：剥离路径成分、白名单字符、URL 哈希前缀防冲突。"""
    segment = urllib.parse.unquote(url.rsplit('/', 1)[-1])
    name = Path(segment).name
    if not name or name in ('.', '..'):
        return None
    name = re.sub(r'[^\w\u4e00-\u9fa5.\- ]', '_', name).strip()
    if not name or name in ('.', '..'):
        return None
    digest = hashlib.sha256(url.encode('utf-8')).hexdigest()[:CACHE_KEY_CHARS]
    return f"{digest}_{name}"


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not validate_download_url(newurl):
            raise urllib.error.URLError(f"拒绝重定向到非公网地址: {newurl[:80]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_ValidatingRedirectHandler)


def write_cache(cache_dir: Path, cache_name: str, data: bytes) -> bool:
    """写入缓存前校验目标仍位于缓存目录内，拒绝路径穿越。"""
    try:
        base = cache_dir.resolve()
        target = (cache_dir / cache_name).resolve()
    except OSError as e:
        print(f"[WARNING] 缓存路径解析失败: {e}")
        return False
    if base not in target.parents:
        print(f"[WARNING] 拒绝写入缓存目录之外的路径: {target}")
        return False
    try:
        target.write_bytes(data)
    except OSError as e:
        print(f"[WARNING] 缓存写入失败: {e}")
        return False
    return True


def read_cache(
    cache_dir: Optional[Path],
    cache_name: Optional[str],
    ttl: int = CACHE_TTL_SECONDS,
) -> Tuple[Optional[bytes], bool]:
    """读取缓存，返回 (内容, 是否已过期)；无缓存返回 (None, False)。"""
    if cache_dir is None or not cache_name:
        return None, False
    path = cache_dir / cache_name
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None, False
        stale = (time.time() - path.stat().st_mtime) > ttl
        return path.read_bytes(), stale
    except OSError as e:
        print(f"[WARNING] 缓存读取异常: {e}")
        return None, False


def download_bytes(
    url: str,
    cache_dir: Optional[Path] = None,
    timeout: int = 30,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    retries: int = DOWNLOAD_RETRIES,
    refresh: bool = False,
) -> Optional[bytes]:
    """下载并缓存字节内容；带 URL 校验、体积上限、TTL 与有限重试。"""
    if not url:
        return None
    if not validate_download_url(url):
        print(f"[WARNING] 拒绝非公网 HTTP(S) 链接: {url[:60]}")
        return None

    cache_name = safe_cache_name(url)
    stale_copy: Optional[bytes] = None
    if cache_dir is not None and cache_name:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"[WARNING] 缓存目录创建失败: {e}")

    if not refresh:
        cached, stale = read_cache(cache_dir, cache_name)
        if cached is not None:
            if not stale:
                return cached
            stale_copy = cached

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with _OPENER.open(req, timeout=timeout) as resp:
                declared = resp.headers.get('Content-Length')
                if declared:
                    try:
                        if int(declared) > max_bytes:
                            print(f"[WARNING] 响应体积超限({declared}B): {url[:60]}")
                            return stale_copy
                    except ValueError:
                        pass
                raw = resp.read(max_bytes + 1)

            if len(raw) > max_bytes:
                print(f"[WARNING] 响应体积超限(>{max_bytes}B): {url[:60]}")
                return stale_copy
            if not raw:
                return stale_copy

            if cache_dir is not None and cache_name:
                write_cache(cache_dir, cache_name, raw)
            return raw

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f"[WARNING] 下载失败 ({url[:60]}...): {e}")
            if stale_copy is not None:
                print("[WARNING] 使用已过期的本地缓存副本")
            return stale_copy

    return stale_copy


def download_text(url: str, cache_dir: Optional[Path] = None, **kwargs) -> Optional[str]:
    """下载文本并解码为字符串；失败返回 None。"""
    raw = download_bytes(url, cache_dir=cache_dir, **kwargs)
    if raw is None:
        return None
    text = raw.decode('utf-8', errors='replace')
    return text if text.strip() else None
