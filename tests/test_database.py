"""Unit tests для SQLite-хранилища (временная БД, без реальных файлов проекта)."""
import tempfile

from news.models import TimelineItem
from storage.database import Database


class TestDatabase:
    def setup_method(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Database(f"{self.tmp}/test.db")

    def teardown_method(self):
        self.db.close()

    def test_upsert_and_get_user(self):
        self.db.upsert_user(1, "alice", voice="zahar")
        user = self.db.get_user(1)
        assert user is not None
        assert user["username"] == "alice"
        assert user["voice"] == "zahar"

    def test_upsert_updates_fields(self):
        self.db.upsert_user(1, "alice", voice="zahar")
        self.db.upsert_user(1, "alice", speed=1.5)
        user = self.db.get_user(1)
        assert user["speed"] == 1.5
        assert user["voice"] == "zahar"  # старые поля не стираются

    def test_user_settings_defaults(self):
        s = self.db.get_user_settings(999)  # несуществующий
        assert s["voice"] == "zahar"
        assert s["video_source"] == "auto"
        assert s["subtitle_style"] == "tiktok"

    def test_next_job_id_increments(self):
        id1 = self.db.next_job_id()
        self.db.create_job(id1, 1, "text")
        id2 = self.db.next_job_id()
        assert id1.startswith("JOB-")
        assert id1 != id2
        assert int(id2.rsplit("-", 1)[1]) == int(id1.rsplit("-", 1)[1]) + 1

    def test_create_and_update_job(self):
        self.db.create_job("JOB-1", 1, "text", "script")
        job = self.db.get_job("JOB-1")
        assert job["status"] == "queued"
        self.db.finish_job("JOB-1", "completed", output_path="/tmp/x.mp4")
        job = self.db.get_job("JOB-1")
        assert job["status"] == "completed"
        assert job["output_path"] == "/tmp/x.mp4"
        assert job["completed_at"] is not None

    def test_cancelled_saved_to_sqlite(self):
        """P0: отменённый job сохраняет статус cancelled в SQLite."""
        self.db.create_job("JOB-9", 1, "text")
        self.db.finish_job("JOB-9", "cancelled", error="Отменено пользователем")
        job = self.db.get_job("JOB-9")
        assert job["status"] == "cancelled"
        assert job["error"] == "Отменено пользователем"
        assert job["completed_at"] is not None
        # Не попадает в "историю готовых видео" (только completed)
        history = self.db.list_history(1, limit=10)
        assert all(h["status"] == "completed" for h in history)

    def test_set_stage(self):
        from storage.database import JOB_STATUSES
        self.db.create_job("JOB-1", 1, "text")
        assert "analyzing" in JOB_STATUSES
        assert "rendering" in JOB_STATUSES
        assert "cancelled" in JOB_STATUSES
        self.db.set_stage("JOB-1", "analyzing")
        assert self.db.get_job("JOB-1")["status"] == "analyzing"
        # Невалидный статус игнорируется
        self.db.set_stage("JOB-1", "bogus")
        assert self.db.get_job("JOB-1")["status"] == "analyzing"

    def test_indexes_exist(self):
        """PHASE 6: индексы для частых запросов существуют."""
        rows = self.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "idx_jobs_user_id" in names
        assert "idx_jobs_status" in names
        assert "idx_jobs_created_at" in names
        assert "idx_videos_job_id" in names

    def test_list_jobs(self):
        self.db.create_job("JOB-1", 1, "text")
        self.db.create_job("JOB-2", 1, "text")
        jobs = self.db.list_jobs(1)
        assert len(jobs) == 2

    def test_add_video_and_history(self):
        self.db.create_job("JOB-1", 1, "text")
        self.db.finish_job("JOB-1", "completed", output_path="/tmp/x.mp4")
        self.db.add_video("JOB-1", "/tmp/x.mp4", 30.0)
        history = self.db.list_history(1)
        assert len(history) == 1
        assert history[0]["duration"] == 30.0

    def test_news_batch_roundtrip(self):
        batch_id = "BATCH-1"
        self.db.create_news_batch(batch_id, 1, 3)
        batch = self.db.get_news_batch(batch_id)
        assert batch is not None
        assert batch["status"] == "queued"

    def test_timeline_item_phrase_timings_roundtrip(self):
        """PHASE 6: phrase_timings (word-level ASR) сохраняются и читаются."""
        item = TimelineItem(
            id="n1", type="news", news_id=1, start=0.5, end=3.5, duration=3.0,
            text="Новость", audio_path="/tmp/a.mp3",
            phrase_timings=[{"start": 0.1, "end": 0.8, "text": "Новость"}],
        )
        self.db.save_timeline_item("BATCH-1", item)
        items = self.db.list_timeline_items("BATCH-1")
        assert len(items) == 1
        assert items[0]["phrase_timings"] == [{"start": 0.1, "end": 0.8, "text": "Новость"}]

    def test_timeline_item_without_phrase_timings(self):
        item = TimelineItem(id="t1", type="transition", news_id=1,
                            start=0.0, end=1.0, duration=1.0, text="Переход")
        self.db.save_timeline_item("BATCH-1", item)
        items = self.db.list_timeline_items("BATCH-1")
        assert items[0]["phrase_timings"] is None