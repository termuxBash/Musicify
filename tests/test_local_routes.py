import pytest
from flask import Flask, jsonify

from apps.local import routes


def test_browse_allows_symlinked_directory_inside_music_root(tmp_path, monkeypatch):
    music_dir = tmp_path / "Music"
    external_dir = tmp_path / "external"
    music_dir.mkdir()
    external_dir.mkdir()
    (external_dir / "track.mp3").touch()
    (music_dir / "online").symlink_to(external_dir, target_is_directory=True)

    monkeypatch.setattr(routes, "ROOT_DIR", str(music_dir))
    monkeypatch.setattr(
        routes,
        "render_template",
        lambda template, **context: jsonify(context["items"]),
    )
    app = Flask(__name__)
    app.register_blueprint(routes.local_bp, url_prefix="/local")

    response = app.test_client().get("/local/browse/online")

    assert response.status_code == 200
    assert response.json == [
        {"name": "track.mp3", "is_dir": False, "rel_path": "online/track.mp3"}
    ]


def test_safe_path_still_rejects_paths_outside_music_root(tmp_path):
    music_dir = tmp_path / "Music"
    music_dir.mkdir()

    with pytest.raises(ValueError, match="path escapes its base directory"):
        routes._safe_path(str(music_dir), "../outside")
