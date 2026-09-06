"""Single-owner R2 task journal and private, immutable media objects.

Records commit only after their media uploads finish. No task is automatically
resubmitted after a process restart, since provider submissions may be billable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import time
import threading
from pathlib import Path

from app.models import const

PREFIX = "qingzhi/v1/"
MAX_RECORD_BYTES = 4 * 1024 * 1024
FILE_FIELDS = ("videos", "combined_videos", "images", "audio_file", "subtitle_path")
RECORD_FIELDS = (
    "task_id",
    "state",
    "progress",
    "video_subject",
    "script",
    "terms",
    "failed_stage",
    "error",
    "kind",
    "created_at",
    "updated_at",
    "assets",
    "persistence_status",
    "deleted",
    "provider_tasks",
)


class PersistenceError(RuntimeError):
    pass


def _identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Invalid task identifier")
    return value


def _digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class R2Store:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    @classmethod
    def from_environment(cls):
        import boto3
        from botocore.config import Config

        names = (
            "R2_ENDPOINT_URL",
            "R2_BUCKET_NAME",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
        )
        values = {name: os.environ.get(name, "").strip() for name in names}
        if not all(values.values()):
            raise PersistenceError(
                "R2 configuration incomplete: configure all four R2 secrets"
            )
        if not re.fullmatch(
            r"https://[a-f0-9]{32}(?:\.(?:eu|fedramp))?\.r2\.cloudflarestorage\.com",
            values["R2_ENDPOINT_URL"],
        ):
            raise PersistenceError(
                "R2_ENDPOINT_URL must be an HTTPS Cloudflare R2 S3 endpoint"
            )
        client = boto3.client(
            "s3",
            endpoint_url=values["R2_ENDPOINT_URL"],
            region_name="auto",
            aws_access_key_id=values["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=values["R2_SECRET_ACCESS_KEY"],
            config=Config(
                signature_version="s3v4",
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 3, "mode": "standard"},
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )
        return cls(client, values["R2_BUCKET_NAME"])

    def records(self):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=PREFIX + "tasks/"):
            for item in page.get("Contents", []):
                key = item["Key"]
                if not key.endswith(".json"):
                    continue
                body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
                try:
                    payload = json.loads(body.read(MAX_RECORD_BYTES + 1))
                finally:
                    body.close()
                task_id = _identifier(payload["task_id"])
                if key != self.record_key(task_id):
                    raise PersistenceError("R2 task record identifier mismatch")
                yield payload

    @staticmethod
    def record_key(task_id):
        return PREFIX + "tasks/" + _identifier(task_id) + ".json"

    def write(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        if len(body) > MAX_RECORD_BYTES:
            raise PersistenceError("Task record exceeds the 4 MiB limit")
        self.client.put_object(
            Bucket=self.bucket,
            Key=self.record_key(payload["task_id"]),
            Body=body,
            ContentType="application/json",
        )

    def upload(self, path, key):
        from boto3.s3.transfer import TransferConfig

        self.client.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={
                "ContentType": mimetypes.guess_type(path)[0]
                or "application/octet-stream"
            },
            Config=TransferConfig(
                multipart_threshold=16 * 1024 * 1024,
                multipart_chunksize=16 * 1024 * 1024,
                max_concurrency=2,
            ),
        )

    def download(self, key, path):
        self.client.download_file(self.bucket, key, str(path))

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


class R2State:
    def __init__(self, store, root):
        self._tasks = {}
        self._lock = threading.RLock()
        self.store = store
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # Fail closed: a temporary R2 outage must not create an empty history.
        try:
            for record in self.store.records():
                task_id = _identifier(record["task_id"])
                if record.get("deleted"):
                    self._remove_remote(record)
                    continue
                if record.get("state") == const.TASK_STATE_PROCESSING:
                    record.update(
                        state=const.TASK_STATE_FAILED,
                        failed_stage="interrupted",
                        error="容器重启，任务已中断；核查模型账单后再手动重试。 / Interrupted by restart; check provider activity before retrying.",
                        updated_at=time.time(),
                    )
                    self.store.write(record)
                self._tasks[task_id] = record
        except Exception:
            raise PersistenceError(
                "无法读取 R2 任务记录，请检查存储配置和连接。 / Cannot load R2 task history."
            ) from None

    def _record(self, task):
        record = {key: copy.deepcopy(task[key]) for key in RECORD_FIELDS if key in task}
        # Credentials and arbitrary runtime config never enter the journal.
        for field in ("error", "video_subject", "script"):
            if isinstance(record.get(field), str):
                for name, secret in os.environ.items():
                    if (
                        any(
                            word in name
                            for word in ("KEY", "TOKEN", "PASSWORD", "SECRET")
                        )
                        and len(secret) >= 8
                    ):
                        record[field] = record[field].replace(secret, "[redacted]")
        return record

    def _asset_path(self, asset, task_id):
        relative = asset["path"]
        path = (self.root / relative).resolve()
        expected = self.root / "tasks" / _identifier(task_id)
        if not path.is_relative_to(expected) or path == expected:
            raise PersistenceError("Invalid stored artifact path")
        if not asset["key"].startswith(PREFIX + "media/" + task_id + "/"):
            raise PersistenceError("Invalid stored artifact key")
        return path

    def _upload_results(self, task):
        assets = list(task.get("assets", []))
        for field in FILE_FIELDS:
            raw = task.get(field)
            paths = raw if isinstance(raw, list) else [raw] if raw else []
            for index, value in enumerate(paths):
                path = Path(value).resolve()
                if not path.is_relative_to(self.root) or not path.is_file():
                    raise PersistenceError(
                        "Result file is missing or outside the workspace"
                    )
                digest = _digest(path)
                key = f"{PREFIX}media/{task['task_id']}/{digest}{path.suffix.lower()}"
                existing = next(
                    (
                        a
                        for a in assets
                        if a["field"] == field
                        and a["index"] == index
                        and a["sha256"] == digest
                    ),
                    None,
                )
                if existing:
                    continue
                self.store.upload(path, key)
                # Restore into a portable task directory, never an old absolute path.
                relative = (
                    f"tasks/{task['task_id']}/{field}-{index}{path.suffix.lower()}"
                )
                assets = [
                    a for a in assets if (a["field"], a["index"]) != (field, index)
                ]
                assets.append(
                    dict(
                        field=field,
                        index=index,
                        path=relative,
                        key=key,
                        sha256=digest,
                        size=path.stat().st_size,
                    )
                )
        task["assets"] = assets

    def _commit(self, task):
        if task.get("state") == const.TASK_STATE_COMPLETE:
            self._upload_results(task)
        task["persistence_status"] = "saved"
        self.store.write(self._record(task))

    def update_task(
        self, task_id, state=const.TASK_STATE_PROCESSING, progress=0, **kwargs
    ):
        _identifier(task_id)
        with self._lock:
            task = copy.deepcopy(self._tasks.get(task_id, {}))
            task.update(
                kwargs,
                task_id=task_id,
                state=state,
                progress=min(100, int(progress)),
                updated_at=time.time(),
            )
            task.setdefault("created_at", time.time())
            self._tasks[task_id] = task
            try:
                self._commit(task)
            except Exception:
                task["persistence_status"] = "failed"
                # Keep completed local output available for an explicit storage retry.
                # Never regenerate paid media just to retry an upload.
                if state != const.TASK_STATE_COMPLETE:
                    raise PersistenceError(
                        "任务未保存到 R2，操作已停止。 / R2 task save failed."
                    ) from None

    def patch_task(self, task_id, **kwargs):
        with self._lock:
            if task_id not in self._tasks:
                return False
            task = self._tasks[task_id]
            task.update(copy.deepcopy(kwargs), updated_at=time.time())
            try:
                self._commit(task)
            except Exception:
                task["persistence_status"] = "failed"
                raise PersistenceError("R2 task update failed") from None
            return True

    def record_provider_task(self, task_id, remote_id):
        with self._lock:
            task = self._tasks[task_id]
            identifiers = list(task.get("provider_tasks", []))
            if remote_id not in identifiers:
                identifiers.append(remote_id)
            self.patch_task(task_id, provider_tasks=identifiers)

    def get_task(self, task_id):
        with self._lock:
            return copy.deepcopy(self._tasks.get(task_id))

    def get_all_tasks(self, page, page_size):
        with self._lock:
            tasks = sorted(
                self._tasks.values(), key=lambda t: t.get("created_at", 0), reverse=True
            )
            return copy.deepcopy(tasks[(page - 1) * page_size : page * page_size]), len(
                tasks
            )

    def retry_save(self, task_id):
        with self._lock:
            task = self._tasks[task_id]
            try:
                self._commit(task)
            except Exception:
                task["persistence_status"] = "failed"
                raise PersistenceError(
                    "R2 保存仍失败，保留本地文件并稍后重试。 / R2 save failed; keep local files and retry."
                ) from None

    def restore_task(self, task_id):
        with self._lock:
            task = self._tasks[task_id]
            for asset in task.get("assets", []):
                path = self._asset_path(asset, task_id)
                if not path.is_file() or _digest(path) != asset["sha256"]:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".r2-")
                    os.close(fd)
                    try:
                        self.store.download(asset["key"], Path(temporary))
                        if _digest(Path(temporary)) != asset["sha256"]:
                            raise PersistenceError("R2 artifact checksum mismatch")
                        os.replace(temporary, path)
                    except Exception:
                        raise PersistenceError(
                            "无法恢复作品，请检查 R2 后重试。 / Artifact restore failed."
                        ) from None
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                field = asset["field"]
                if field not in FILE_FIELDS:
                    raise PersistenceError("Invalid artifact field")
                if field in ("audio_file", "subtitle_path"):
                    task[field] = str(path)
                else:
                    values = task.setdefault(field, [])
                    while len(values) <= asset["index"]:
                        values.append("")
                    values[asset["index"]] = str(path)
            return copy.deepcopy(task)

    def _remove_remote(self, task):
        for asset in task.get("assets", []):
            self._asset_path(asset, task["task_id"])
            self.store.delete(asset["key"])
        self._remove_cache(task["task_id"])
        self.store.delete(self.store.record_key(task["task_id"]))

    def _remove_cache(self, task_id):
        for folder in ("tasks", "studio/images", "studio/stitches"):
            path = self.root / folder / _identifier(task_id)
            if not path.parent.resolve().is_relative_to(self.root):
                raise PersistenceError("Invalid local cache path")
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)

    def delete_task(self, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            if task.get("state") == const.TASK_STATE_PROCESSING:
                raise PersistenceError("Cannot delete a running task")
            tombstone = self._record(task)
            tombstone["deleted"] = True
            try:
                self.store.write(tombstone)
                self._remove_remote(tombstone)
            except Exception:
                raise PersistenceError(
                    "R2 删除未完成，请重试。 / R2 deletion incomplete; retry."
                ) from None
            self._tasks.pop(task_id, None)
