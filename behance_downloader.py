#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


# =========================================================
# CONFIGURAÇÃO RÍGIDA – NÃO MEXA SE QUISER RESULTADO LIMPO
# =========================================================

ALLOWED_DOMAIN = "mir-s3-cdn-cf.behance.net"

FS_WEBP_PATH = "/project_modules/fs_webp/"
SOURCE_PATH = "/project_modules/source/"


# =========================================================
# MODELOS
# =========================================================

@dataclass
class ImageCandidate:
    url: str
    priority: int


# =========================================================
# HTTP
# =========================================================

def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        }
    )
    return session


def fetch_html(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


# =========================================================
# FILTROS DEFINITIVOS (AQUI ESTÁ O SEGREDO)
# =========================================================

def is_valid_project_asset(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc != ALLOWED_DOMAIN:
        return False

    path = (parsed.path or "").lower()

    if FS_WEBP_PATH in path:
        return True

    if SOURCE_PATH in path and (path.endswith(".gif") or path.endswith(".mp4")):
        return True

    return False


def extract_fs_webp_from_html(html: str) -> Set[str]:
    """
    Extrai SOMENTE URLs fs_webp direto do HTML.
    """
    pattern = re.compile(
        r"https://mir-s3-cdn-cf\.behance\.net/project_modules/fs_webp/[^\"'\s)]+",
        re.IGNORECASE,
    )
    return set(pattern.findall(html))


def extract_next_data_json(html: str) -> Optional[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__", type="application/json")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except Exception:
        return None


def walk_json_for_urls(obj, out: Set[str]) -> None:
    if obj is None:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            walk_json_for_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_json_for_urls(v, out)
    elif isinstance(obj, str):
        if obj.startswith("https://"):
            out.add(obj)


# =========================================================
# COLETA FINAL (SEM HTML GENÉRICO, SEM CSS, SEM SRCSET)
# =========================================================

def collect_project_images(html: str) -> List[ImageCandidate]:
    found: Dict[str, ImageCandidate] = {}

    # 1) HTML direto (fs_webp)
    for url in extract_fs_webp_from_html(html):
        if is_valid_project_asset(url):
            key = file_signature(url)
            found[key] = ImageCandidate(url=url, priority=1)

    # 2) NEXT_DATA (mais confiável)
    next_data = extract_next_data_json(html)
    if next_data:
        urls: Set[str] = set()
        walk_json_for_urls(next_data, urls)
        for url in urls:
            if is_valid_project_asset(url):
                key = file_signature(url)
                found[key] = ImageCandidate(url=url, priority=2)

    return list(found.values())


# =========================================================
# DEDUPLICAÇÃO PERFEITA
# =========================================================

def file_signature(url: str) -> str:
    """
    Assinatura única do módulo.
    Ex:
    de718d239892003.6932b544208fa
    """
    filename = os.path.basename(urlparse(url).path)
    stem, _ = os.path.splitext(filename)
    return stem.lower()


# =========================================================
# PLAYWRIGHT (FALLBACK)
# =========================================================

def render_with_playwright(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)

        try:
            page.wait_for_selector("main", timeout=10000)
            html = page.locator("main").inner_html()
        except Exception:
            html = page.content()

        browser.close()
        return html


# =========================================================
# DOWNLOAD
# =========================================================

def download_file(
    session: requests.Session,
    url: str,
    output_path: str,
) -> Tuple[bool, Optional[int], Optional[str]]:
    try:
        r = session.get(url, stream=True, timeout=30)
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
        return True, os.path.getsize(output_path), None
    except Exception as exc:
        return False, None, str(exc)


# =========================================================
# CLI
# =========================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Baixa SOMENTE as imagens reais de um projeto do Behance"
    )
    parser.add_argument("url", help="URL do projeto Behance")
    parser.add_argument("-o", "--output", default="downloads", help="Pasta de saída")
    parser.add_argument(
        "--min-images",
        type=int,
        default=3,
        help="Mínimo antes de usar Playwright",
    )
    return parser.parse_args()


# =========================================================
# MAIN
# =========================================================

def main() -> int:
    args = parse_args()
    url = args.url
    output_dir = args.output

    if not url.startswith("http"):
        print("URL inválida.")
        return 1

    os.makedirs(output_dir, exist_ok=True)
    session = build_session()

    try:
        html = fetch_html(session, url)
    except Exception as exc:
        print(f"Erro ao baixar HTML: {exc}")
        return 1

    images = collect_project_images(html)

    if len(images) < args.min_images:
        try:
            rendered_html = render_with_playwright(url)
            images = collect_project_images(rendered_html)
        except Exception:
            pass

    if not images:
        print("Nenhuma imagem do projeto encontrada.")
        return 1

    manifest = []

    for idx, img in enumerate(images, start=1):
        ext = os.path.splitext(urlparse(img.url).path)[1] or ".webp"
        filename = f"image_{idx:03d}{ext}"
        path = os.path.join(output_dir, filename)

        ok, size, err = download_file(session, img.url, path)
        manifest.append(
            {
                "url_imagem": img.url,
                "arquivo_salvo": filename if ok else None,
                "tamanho_bytes": size,
                "status": "ok" if ok else "erro",
                "erro": err,
            }
        )

    with open(os.path.join(output_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Imagens baixadas: {len(images)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
