from flask import Flask

from core.stats import stats_bp


class FakePlayback:
    def __init__(self):
        self.removed_indexes = []
        self.front_indexes = []
        self.reordered_indexes = []

    def remove_from_queue(self, index):
        self.removed_indexes.append(index)
        return {"title": "Queued song"}

    def move_to_front(self, index):
        self.front_indexes.append(index)
        return True

    def move_in_queue(self, source_index, target_index):
        self.reordered_indexes.append((source_index, target_index))
        return True


def create_queue_app():
    app = Flask(__name__)
    app.playback = FakePlayback()
    app.register_blueprint(stats_bp)
    return app


def test_queue_mutations_read_indexes_from_json_body():
    app = create_queue_app()
    client = app.test_client()

    response = client.post("/remove_from_queue", json={"index": 2})
    assert response.status_code == 200
    assert response.json == {"status": "removed", "title": "Queued song"}

    response = client.post("/play_next", json={"index": 3})
    assert response.status_code == 200
    assert response.json == {"status": "moved"}

    response = client.post(
        "/reorder_queue",
        json={"source_index": 1, "target_index": 4},
    )
    assert response.status_code == 200
    assert response.json == {"status": "reordered"}

    assert app.playback.removed_indexes == [2]
    assert app.playback.front_indexes == [3]
    assert app.playback.reordered_indexes == [(1, 4)]


def test_queue_mutations_reject_missing_or_invalid_indexes():
    client = create_queue_app().test_client()

    assert client.post("/remove_from_queue", json={}).status_code == 400
    assert client.post("/play_next", json={"index": -1}).status_code == 400
    assert client.post(
        "/reorder_queue",
        json={"source_index": True, "target_index": 0},
    ).status_code == 400

