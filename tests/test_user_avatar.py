import io

import pytest
from PIL import Image

from assistant.stores import user_avatar


@pytest.fixture(autouse=True)
def _prefs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))


def _png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_save_crops_to_square_and_shrinks():
    data = user_avatar.save_avatar(7, _png(1200, 800))
    im = Image.open(io.BytesIO(data))
    assert im.format == "JPEG"
    assert im.size == (user_avatar.SIZE_PX, user_avatar.SIZE_PX)
    assert user_avatar.get_avatar(7) == data
    assert user_avatar.has_avatar(7)


def test_small_image_keeps_size_but_square():
    data = user_avatar.save_avatar(8, _png(300, 200))
    assert Image.open(io.BytesIO(data)).size == (200, 200)


def test_rejects_garbage_and_empty():
    with pytest.raises(user_avatar.AvatarError):
        user_avatar.save_avatar(9, b"not an image")
    with pytest.raises(user_avatar.AvatarError):
        user_avatar.save_avatar(9, b"")
    assert not user_avatar.has_avatar(9)


def test_delete():
    user_avatar.save_avatar(10, _png(100, 100))
    assert user_avatar.delete_avatar(10) is True
    assert user_avatar.get_avatar(10) is None
    assert user_avatar.delete_avatar(10) is False
