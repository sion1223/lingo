import re

from scripts.build_pages import asset_version, build, render_index


def test_index_uses_relative_paths_and_versioned_modules(tmp_path):
    out = build(tmp_path / 'site', 'abc123', {'lingo-api-base-url': 'https://api.example.test'})
    index = (out / 'index.html').read_text(encoding='utf-8')

    assert '/web/' not in index
    assert '__LINGO_WEB_ASSET_VERSION__' not in index
    assert f'"./app.js": "./app.js?v={asset_version("abc123")}"' in index
    assert '<meta name="lingo-api-base-url" content="https://api.example.test">' in index
    assert (out / '.nojekyll').exists()


def test_every_imported_module_is_published(tmp_path):
    out = build(tmp_path / 'site', 'abc123', {})
    index = (out / 'index.html').read_text(encoding='utf-8')
    for module in re.findall(r'"\./([\w/.-]+\.js)":', index):
        assert (out / module).exists(), module


def test_dev_only_files_are_not_published(tmp_path):
    out = build(tmp_path / 'site', 'abc123', {})
    assert not (out / 'tests').exists()
    assert not list(out.glob('edge-*.ts'))
    assert not (out / 'benchmark-local.mjs').exists()


def test_meta_values_are_escaped():
    source = '<meta name="lingo-api-key" content="">'
    rendered = render_index(source, 'v', {'lingo-api-key': 'a"b<c'})
    assert rendered == '<meta name="lingo-api-key" content="a&quot;b&lt;c">'
