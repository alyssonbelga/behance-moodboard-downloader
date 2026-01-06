#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


CDN_DOMAINS = {
    "mir-s3-cdn-cf.behance.net",
    "mir-s3-cdn-cf.behance.net",
}

PROJECT_MODULES_PRIORITY = {
    "project_modules_max": 3,
    "project_modules": 2,
}
# Mantém apenas o que costuma ser conteúdo inserido no editor do projeto (módulos)
ALLOWED_PATH_KEYWORDS = (
    "/project_modules",
    "/project_modules_max",
)

# Coisas que NÃO são conteúdo do projeto (avatars, assets, ícones, etc.)
BLOCKED_PATH_KEYWORDS = (
    "/users/",
    "/user/",
    "/avatars/",
    "/avatar/",
    "/assets/",
    "/icons/",
    "/icon/",
    "/static/",
    "/badges/",
    "/favicon",
    "/fonts/",
)

def is_behance_project_image(url: str) -> bool:
    """True somente para imagens do conteúdo do projeto (módulos)."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = (parsed.path or "").lower()

        if host not in CDN_DOMAINS:
            return False

        if any(b in path for b in BLOCKED_PATH_KEYWORDS):
            return False

        # Regra principal: só módulos do projeto
        return any(k in path for k in ALLOWED_PATH_KEYWORDS)
    except Exception:
        return False

def canonical_key(url: str) -> str:
    """
    Deduplica a "mesma imagem" em tamanhos diferentes.
    Ex: project_modules_max_3840 -> project_modules_max
    Remove query/fragment.
    """
    parsed = urlparse(url)
    clean = parsed._replace(query="", fragment="").geturl()
    clean = re.sub(r"(project_modules_max)_\d+", r"\1", clean, flags=re.IGNORECASE)
    return clean

@dataclass
class ImageCandidate:
    original_url: str
    resolved_url: str
    priority: int


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    return session


def fetch_html(session: requests.Session, url: str, timeout: int = 20) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_urls_from_srcset(srcset: str) -> List[str]:
    urls = []
    for part in srcset.split(","):
        url = part.strip().split(" ")[0]
        if url:
            urls.append(url)
    return urls


def extract_urls_from_css(css_text: str) -> List[str]:
    urls = []
    for match in re.findall(r"url\(([^)]+)\)", css_text, flags=re.IGNORECASE):
        cleaned = match.strip().strip('"\'')
        if cleaned:
            urls.append(cleaned)
    return urls


def extract_behance_cdn_urls(html: str) -> List[str]:
    # Pega apenas URLs do CDN que tenham project_modules (conteúdo do projeto)
    pattern = re.compile(
        r"https?://mir-s3-cdn-cf\.behance\.net/(?:project_modules(?:_max_[0-9]+)?)/[^\"'\s)]+",
        re.IGNORECASE,
    )
    return pattern.findall(html)

def resolve_url(base_url: str, candidate: str) -> Optional[str]:
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"}:
        return candidate
    if candidate.startswith("//"):
        return f"https:{candidate}"
    return urljoin(base_url, candidate)


def candidate_priority(url: str) -> int:
    for key, value in PROJECT_MODULES_PRIORITY.items():
        if key in url:
            return value
    return 1


def collect_image_candidates(base_url: str, html: str) -> List[ImageCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: List[ImageCandidate] = []

    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            resolved = resolve_url(base_url, src)
            if resolved:
                candidates.append(
                    ImageCandidate(src, resolved, candidate_priority(resolved))
                )
        srcset = img.get("srcset")
        if srcset:
            for srcset_url in extract_urls_from_srcset(srcset):
                resolved = resolve_url(base_url, srcset_url)
                if resolved:
                    candidates.append(
                        ImageCandidate(
                            srcset_url, resolved, candidate_priority(resolved)
                        )
                    )

    for element in soup.find_all(style=True):
        style = element.get("style") or ""
        for css_url in extract_urls_from_css(style):
            resolved = resolve_url(base_url, css_url)
            if resolved:
                candidates.append(
                    ImageCandidate(css_url, resolved, candidate_priority(resolved))
                )

    for style_tag in soup.find_all("style"):
        style_text = style_tag.string or ""
        for css_url in extract_urls_from_css(style_text):
            resolved = resolve_url(base_url, css_url)
            if resolved:
                candidates.append(
                    ImageCandidate(css_url, resolved, candidate_priority(resolved))
                )

    for cdn_url in extract_behance_cdn_urls(html):
        resolved = resolve_url(base_url, cdn_url)
        if resolved:
            candidates.append(
                ImageCandidate(cdn_url, resolved, candidate_priority(resolved))
            )
    # FILTRO FINAL: só conteúdo do projeto
    candidates = [c for c in candidates if is_behance_project_image(c.resolved_url)]
    return candidates


def dedupe_candidates(candidates: Iterable[ImageCandidate]) -> List[ImageCandidate]:
    best: Dict[str, ImageCandidate] = {}

    def score(u: str) -> int:
        u = u.lower()
        # Prioriza project_modules_max (melhor qualidade)
        if "project_modules_max" in u:
            return 3
        if "/project_modules/" in u:
            return 2
        return 1

    for candidate in candidates:
        key = canonical_key(candidate.resolved_url)
        existing = best.get(key)
        if existing is None:
            best[key] = candidate
        else:
            # Mantém o de maior qualidade (max > normal)
            if score(candidate.resolved_url) > score(existing.resolved_url):
                best[key] = candidate

    # Ordena para baixar max primeiro
    return sorted(best.values(), key=lambda c: -score(c.resolved_url))

def render_with_playwright(url: str, timeout: int = 20000) -> str:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout)

        # Tenta pegar apenas a área principal do projeto
        try:
            page.wait_for_selector("main", timeout=timeout)
            content = page.locator("main").inner_html()
        except Exception:
            # fallback: página inteira
            content = page.content()

        browser.close()
        return content



def download_image(
    session: requests.Session,
    url: str,
    output_path: str,
    timeout: int = 30,
) -> Tuple[bool, Optional[int], Optional[str]]:
    try:
        response = session.get(url, stream=True, timeout=timeout)
        response.raise_for_status()
        with open(output_path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)
        size = os.path.getsize(output_path)
        return True, size, None
    except requests.RequestException as exc:
        return False, None, str(exc)


def ensure_output_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def filename_for_index(index: int, url: str) -> str:
    parsed = urlparse(url)
    _, ext = os.path.splitext(parsed.path)
    safe_ext = ext if ext else ".jpg"
    return f"image_{index:03d}{safe_ext}"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Baixa imagens de um projeto do Behance"
    )
    parser.add_argument("url", help="URL do projeto no Behance")
    parser.add_argument(
        "-o",
        "--output",
        default="downloads",
        help="Pasta de saída",
    )
    parser.add_argument(
        "--min-images",
        type=int,
        default=5,
        help="Quantidade mínima para evitar fallback no Playwright",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    url = args.url
    output_dir = args.output

    if not url.startswith("http"):
        print("Erro: a URL precisa começar com http:// ou https://")
        return 1

    ensure_output_dir(output_dir)
    session = build_session()

    try:
        html = fetch_html(session, url)
    except requests.RequestException as exc:
        print(f"Erro ao baixar HTML via requests: {exc}")
        return 1

    candidates = collect_image_candidates(url, html)

    if len(candidates) < args.min_images:
        try:
            rendered_html = render_with_playwright(url)
            candidates.extend(collect_image_candidates(url, rendered_html))
        except Exception as exc:
            print(
                "Aviso: não foi possível renderizar com Playwright. "
                f"Continuando com o HTML original. Detalhes: {exc}"
            )

    unique_candidates = dedupe_candidates(candidates)
    if not unique_candidates:
        print("Nenhuma imagem encontrada.")
        return 1

    manifest = []
    for index, candidate in enumerate(unique_candidates, start=1):
        filename = filename_for_index(index, candidate.resolved_url)
        output_path = os.path.join(output_dir, filename)
        success, size, error = download_image(session, candidate.resolved_url, output_path)
        manifest.append(
            {
                "url_original": candidate.original_url,
                "url_imagem": candidate.resolved_url,
                "arquivo_salvo": filename if success else None,
                "tamanho_bytes": size,
                "status": "ok" if success else "erro",
                "erro": error,
            }
        )

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    print(f"Imagens baixadas: {len(unique_candidates)}")
    print(f"Manifest salvo em: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
