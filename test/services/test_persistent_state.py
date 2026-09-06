import copy
import io
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.models import const
from app.services.persistent_state import PREFIX, PersistenceError, R2State, R2Store


class Store:
    record_key = staticmethod(R2Store.record_key)

    def __init__(self):
        self.rows = {}
        self.files = {}
        self.fail_write = self.fail_upload = self.fail_download = self.fail_delete = (
            False
        )
        self.upload_count = 0

    def records(self):
        yield from copy.deepcopy(list(self.rows.values()))

    def write(self, record):
        if self.fail_write:
            raise OSError("storage offline")
        self.rows[record["task_id"]] = copy.deepcopy(record)

    def upload(self, path, key):
        if self.fail_upload:
            raise OSError("upload offline")
        self.upload_count += 1
        self.files[key] = Path(path).read_bytes()

    def download(self, key, path):
        if self.fail_download:
            path.write_bytes(b"partial")
            raise OSError("download offline")
        path.write_bytes(self.files[key])

    def delete(self, key):
        if self.fail_delete:
            raise OSError("delete offline")
        self.files.pop(key, None)
        if key.startswith(PREFIX + "tasks/"):
            self.rows.pop(Path(key).stem, None)


@pytest.fixture
def workspace(tmp_path):
    store = Store()
    root = tmp_path / "first"
    state = R2State(store, root)
    return store, state, root


def complete(state, root, task_id="task1"):
    path = root / "tasks" / task_id / "result.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"verified video bytes")
    state.update_task(task_id, video_subject="Test", kind="video")
    state.update_task(
        task_id, const.TASK_STATE_COMPLETE, 100, videos=[str(path)], script="script"
    )
    return path


def test_restart_restores_media_to_new_root_and_preserves_script(workspace, tmp_path):
    store, state, root = workspace
    complete(state, root)
    fresh = R2State(store, tmp_path / "new-container")
    assert fresh.get_task("task1")["script"] == "script"
    assert "videos" not in store.rows["task1"]  # no absolute paths in records
    recovered = fresh.restore_task("task1")
    result = Path(recovered["videos"][0])
    assert result.is_relative_to(tmp_path / "new-container")
    assert result.read_bytes() == b"verified video bytes"
    fresh.restore_task("task1")  # cache may be reused after checksum verification
    assert store.upload_count == 1


def test_processing_tasks_become_interrupted_without_resubmission(workspace):
    store, state, root = workspace
    state.update_task("queued", video_subject="queued")
    state.update_task("running", progress=35, video_subject="running")
    fresh = R2State(store, root)
    for name in ("queued", "running"):
        assert fresh.get_task(name)["state"] == const.TASK_STATE_FAILED
        assert store.rows[name]["failed_stage"] == "interrupted"
    assert store.upload_count == 0


def test_upload_failure_keeps_local_output_for_save_only_retry(workspace):
    store, state, root = workspace
    store.fail_upload = True
    result = complete(state, root)
    assert state.get_task("task1")["persistence_status"] == "failed"
    assert store.rows["task1"]["state"] == const.TASK_STATE_PROCESSING
    assert result.is_file()
    store.fail_upload = False
    state.retry_save("task1")
    assert store.rows["task1"]["state"] == const.TASK_STATE_COMPLETE
    assert state.get_task("task1")["persistence_status"] == "saved"
    assert store.upload_count == 1


def test_record_write_failure_does_not_advertise_saved(workspace):
    store, state, root = workspace
    complete(state, root)
    store.fail_write = True
    state.update_task("task1", const.TASK_STATE_COMPLETE, 100, script="updated")
    assert state.get_task("task1")["persistence_status"] == "failed"
    assert store.rows["task1"]["script"] == "script"
    with pytest.raises(PersistenceError):
        state.retry_save("task1")
    store.fail_write = False
    state.retry_save("task1")
    assert store.rows["task1"]["script"] == "updated"
    assert store.upload_count == 1  # metadata retry reuses immutable objects


def test_initial_write_fails_closed(workspace):
    store, state, _ = workspace
    store.fail_write = True
    with pytest.raises(PersistenceError):
        state.update_task("task")
    assert state.get_task("task")["persistence_status"] == "failed"
    broken = Mock()
    broken.records.side_effect = OSError()
    with pytest.raises(PersistenceError):
        R2State(broken, state.root)


def test_only_allowlisted_metadata_and_redacted_errors_are_persisted(
    workspace, monkeypatch
):
    store, state, _ = workspace
    secret = "test-only-secret-123456"
    monkeypatch.setenv("MINIMAX_API_KEY", secret)
    state.update_task(
        "task", api_key=secret, params={"password": secret}, error=f"failure {secret}"
    )
    record = json.dumps(store.rows["task"])
    assert secret not in record
    assert "params" not in record
    assert "api_key" not in record


def test_partial_download_is_never_promoted(workspace, tmp_path):
    store, state, root = workspace
    complete(state, root)
    fresh = R2State(store, tmp_path / "fresh")
    store.fail_download = True
    with pytest.raises(PersistenceError):
        fresh.restore_task("task1")
    assert not list((tmp_path / "fresh").rglob("*.mp4"))
    assert not list((tmp_path / "fresh").rglob(".r2-*"))
    store.fail_download = False
    key = next(iter(store.files))
    store.files[key] = b"corrupt"
    with pytest.raises(PersistenceError):
        fresh.restore_task("task1")


def test_paths_and_task_ids_cannot_escape_workspace(workspace, tmp_path):
    store, state, root = workspace
    with pytest.raises(ValueError):
        state.update_task("../escape")
    complete(state, root)
    store.rows["task1"]["assets"][0]["path"] = "../../escape.mp4"
    fresh = R2State(store, tmp_path / "fresh")
    with pytest.raises(PersistenceError):
        fresh.restore_task("task1")
    state.update_task(
        "outside", const.TASK_STATE_COMPLETE, videos=[str(tmp_path / "outside")]
    )
    assert state.get_task("outside")["persistence_status"] == "failed"


def test_delete_tombstone_finishes_after_restart(workspace):
    store, state, root = workspace
    complete(state, root)
    store.fail_delete = True
    with pytest.raises(PersistenceError):
        state.delete_task("task1")
    assert store.rows["task1"]["deleted"]
    store.fail_delete = False
    fresh = R2State(store, root)
    assert fresh.get_all_tasks(1, 10) == ([], 0)
    assert not store.files and not store.rows


def test_patch_page_copy_and_delete(workspace):
    store, state, root = workspace
    complete(state, root)
    state.update_task("task2")
    with pytest.raises(PersistenceError):
        state.delete_task("task2")
    assert not state.patch_task("missing", script="nothing")
    assert state.patch_task("task1", script="changed")
    tasks, total = state.get_all_tasks(1, 1)
    assert total == 2 and tasks[0]["task_id"] == "task2"
    tasks[0]["progress"] = 99
    assert state.get_task("task2")["progress"] == 0
    state.delete_task("task1")
    state.delete_task("missing")
    assert state.get_task("task1") is None
    assert not store.files


def test_s3_paginated_records_and_json_write():
    client = Mock()
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": PREFIX + "tasks/a.json"}]},
        {"Contents": [{"Key": PREFIX + "tasks/b.json"}]},
    ]
    client.get_object.side_effect = [
        {"Body": io.BytesIO(json.dumps({"task_id": name}).encode())}
        for name in ("a", "b")
    ]
    store = R2Store(client, "private-bucket")
    assert [r["task_id"] for r in store.records()] == ["a", "b"]
    store.write({"task_id": "a", "script": "中文"})
    args = client.put_object.call_args.kwargs
    assert args["Bucket"] == "private-bucket"
    assert json.loads(args["Body"])["script"] == "中文"
    assert "ACL" not in args


def test_s3_config_validated_without_credential_fallback(monkeypatch):
    for key in (
        "R2_ENDPOINT_URL",
        "R2_BUCKET_NAME",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(PersistenceError):
        R2Store.from_environment()
    for key in ("R2_BUCKET_NAME", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "test-value")
    monkeypatch.setenv("R2_ENDPOINT_URL", "http://localhost")
    with pytest.raises(PersistenceError):
        R2Store.from_environment()
    monkeypatch.setenv(
        "R2_ENDPOINT_URL", "https://" + "a" * 32 + ".r2.cloudflarestorage.com"
    )
    store = R2Store.from_environment()
    assert store.client.meta.region_name == "auto"
    assert store.client.meta.config.signature_version == "s3v4"


def test_provider_checkpoint_survives_restart(workspace):
    store, state, root = workspace
    state.update_task("task")
    state.record_provider_task("task", "remote-paid-task")
    state.update_task("task", progress=25)
    fresh = R2State(store, root)
    assert fresh.get_task("task")["provider_tasks"] == ["remote-paid-task"]
    assert fresh.get_task("task")["failed_stage"] == "interrupted"


def test_library_restore_and_delete_flow(workspace, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from app.services import state as sm
    from app.utils import utils

    store, state, root = workspace
    complete(state, root)
    fresh = R2State(store, tmp_path / "new")
    monkeypatch.setattr(sm, "state", fresh)
    monkeypatch.setattr(utils, "task_dir", lambda: str(tmp_path / "new" / "tasks"))
    app = AppTest.from_string(
        "from webui.studio_tools import render_library\nrender_library()"
    )
    app.run(timeout=20)
    assert not app.exception
    app.button(key="r2_open_task1").click().run()
    assert not app.exception
    assert (
        Path(fresh.get_task("task1")["videos"][0]).read_bytes()
        == b"verified video bytes"
    )
    app.checkbox(key="r2_confirm_task1").check().run()
    app.button(key="r2_delete_task1").click().run()
    assert not app.exception
    assert fresh.get_task("task1") is None
    assert not store.files
