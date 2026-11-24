"""仅在科研指导回复中显示用户提供的头像。"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_guide_image_is_a_local_png():
    image = ROOT / "web/static/assets/research-guide.png"
    assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_extra_portrait_and_appearance_controls_are_removed():
    html = (ROOT / "web/static/index.html").read_text(encoding="utf-8")
    for removed_id in ('rg-show-portrait', 'rg-show-avatar', 'rg-header-avatar', 'rg-portrait'):
        assert f'id="{removed_id}"' not in html
    assert 'id="research-guide"' in html


def test_avatar_is_only_added_to_assistant_messages():
    js = (ROOT / "web/static/research-guide.js").read_text(encoding="utf-8")
    assert 'if(m.role==="assistant") heading.append(messageAvatar());' in js
    assert '/static/assets/research-guide.png' in js
    assert 'setAppearance(' not in js
    avatar = js.split('function messageAvatar(', 1)[1].split('async function request(', 1)[0]
    assert 'fetch(' not in avatar
    assert 'scrollTop' not in avatar
    central=(ROOT / "web/static/conversation-home.js").read_text(encoding="utf-8")
    assert 'if(m.role==="assistant")' in central
    assert '/static/assets/research-guide.png' in central
    assert 'if (window.SRA_CONVERSATIONS) return;' in js
