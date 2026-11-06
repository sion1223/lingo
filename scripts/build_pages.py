# -*- coding: utf-8 -*-
"""GitHub Pages용 정적 사이트 빌드: web/ -> _site/.

FastAPI 가 /web/ 에서 하던 일(자산 버전 치환)을 빌드 시점에 하고,
프로젝트 페이지 경로(https://<user>.github.io/lingo/)에서도 동작하도록
index.html 의 /web/ 절대 경로를 상대 경로로 바꾼다.
서버 주소는 소스에 넣지 않고 환경변수로만 주입한다(임시 RunPod URL 커밋 금지).
"""
import argparse
import hashlib
import html
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / 'web'
ASSET_VERSION_PLACEHOLDER = '__LINGO_WEB_ASSET_VERSION__'
EXCLUDED = shutil.ignore_patterns(
    'tests', 'edge-*.ts', 'benchmark-local.mjs', '__pycache__')
# <meta name=...> -> 값을 읽을 환경변수
META_ENV = {
    'lingo-api-base-url': 'LINGO_API_BASE_URL',
    'lingo-edge-endpoint': 'LINGO_EDGE_ENDPOINT',
    'lingo-api-key': 'LINGO_API_KEY',
}


def asset_version(build_sha):
    """server.py 와 같은 규칙: 빌드 SHA 의 sha256 앞 12자리."""
    return hashlib.sha256((build_sha or 'unknown').encode('utf-8')).hexdigest()[:12]


def render_index(source, version, meta):
    out = source.replace('/web/', './').replace(ASSET_VERSION_PLACEHOLDER, version)
    for name, value in meta.items():
        if not value:
            continue
        empty = f'<meta name="{name}" content="">'
        if empty not in out:
            raise ValueError(f'index.html 에 빈 {name} meta 가 없습니다')
        out = out.replace(
            empty, f'<meta name="{name}" content="{html.escape(value, quote=True)}">')
    return out


def build(out_dir, build_sha, meta):
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(WEB_DIR, out_dir, ignore=EXCLUDED)
    index = out_dir / 'index.html'
    index.write_text(
        render_index(index.read_text(encoding='utf-8'), asset_version(build_sha), meta),
        encoding='utf-8')
    # Jekyll 이 _ 로 시작하는 파일을 숨기지 않도록
    (out_dir / '.nojekyll').write_text('', encoding='utf-8')
    return out_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(ROOT / '_site'))
    args = parser.parse_args()
    meta = {name: os.environ.get(env, '').strip() for name, env in META_ENV.items()}
    build_sha = os.environ.get('BUILD_SHA') or os.environ.get('GITHUB_SHA') or 'unknown'
    out = build(args.out, build_sha, meta)
    configured = [name for name, value in meta.items() if value]
    print(f'built {out} (version {asset_version(build_sha)}; meta: {configured or "none"})')


if __name__ == '__main__':
    main()
