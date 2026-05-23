from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from account_register_manager.account_service import account_service
from account_register_manager.cliproxy_upload_service import upload_account_to_targets
from account_register_manager.config import DATA_DIR, config
from account_register_manager.register import openai_register

REGISTER_FILE = DATA_DIR / "register.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_config() -> dict:
    return {
        **openai_register.config,
        "mode": "total",
        "target_quota": 100,
        "target_available": 10,
        "check_interval": 5,
        "enabled": False,
        "stats": {
            "success": 0,
            "fail": 0,
            "done": 0,
            "running": 0,
            "threads": openai_register.config["threads"],
            "elapsed_seconds": 0,
            "avg_seconds": 0,
            "success_rate": 0,
            "current_quota": 0,
            "current_available": 0,
        },
    }


def _normalize(raw: dict) -> dict:
    cfg = _default_config()
    cfg.update({k: v for k, v in raw.items() if k not in {"stats", "logs"}})
    cfg["total"] = max(1, int(cfg.get("total") or 1))
    cfg["threads"] = max(1, int(cfg.get("threads") or 1))
    cfg["mode"] = str(cfg.get("mode") or "total").strip()
    if cfg["mode"] not in {"total", "quota", "available"}:
        cfg["mode"] = "total"
    cfg["target_quota"] = max(1, int(cfg.get("target_quota") or 1))
    cfg["target_available"] = max(1, int(cfg.get("target_available") or 1))
    cfg["check_interval"] = max(1, int(cfg.get("check_interval") or 5))
    cfg["proxy"] = str(cfg.get("proxy") or "").strip()
    cfg["enabled"] = bool(cfg.get("enabled"))
    cfg["stats"] = {**_default_config()["stats"], **(raw.get("stats") if isinstance(raw.get("stats"), dict) else {}), "threads": cfg["threads"]}
    return cfg


class RegisterService:
    def __init__(self, store_file: Path):
        self._store_file = store_file
        self._lock = threading.RLock()
        self._runner: threading.Thread | None = None
        self._logs: list[dict] = []
        self._target_notice_key = ""
        openai_register.register_log_sink = self._append_log
        self._config = self._load()

    def _load(self) -> dict:
        try:
            return _normalize(json.loads(self._store_file.read_text(encoding="utf-8")))
        except Exception:
            return _normalize({})

    def _save(self) -> None:
        self._store_file.parent.mkdir(parents=True, exist_ok=True)
        self._store_file.write_text(json.dumps(self._config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def get(self) -> dict:
        with self._lock:
            payload = {**self._config, "logs": self._logs[-300:]}
            stats = dict(payload.get("stats") or {})
            runner_alive = bool(self._runner and self._runner.is_alive())
            stats["runner_alive"] = runner_alive
            stats["stopping"] = runner_alive and not bool(payload.get("enabled"))
            payload["stats"] = stats
            return json.loads(json.dumps(payload, ensure_ascii=False))

    def update(self, updates: dict) -> dict:
        with self._lock:
            self._config = _normalize({**self._config, **updates})
            if self._config.get("proxy") and isinstance(self._config.get("mail"), dict):
                self._config["mail"]["proxy"] = self._config["proxy"]
            openai_register.config.update({k: self._config[k] for k in ("mail", "proxy", "total", "threads")})
            self._save()
            return self.get()

    def start(self) -> dict:
        with self._lock:
            if self._runner and self._runner.is_alive():
                if self._config.get("enabled"):
                    return self.get()
                self._config["stats"]["stopping"] = True
                self._append_log("当前注册任务仍在停止中，请等待后台任务结束后再启动", "yellow")
                self._save()
                return self.get()
            self._config["enabled"] = True
            self._target_notice_key = ""
            self._logs = []
            metrics = self._pool_metrics()
            self._config["stats"] = {
                "job_id": uuid.uuid4().hex,
                "success": 0,
                "fail": 0,
                "done": 0,
                "running": 0,
                "threads": self._config["threads"],
                **metrics,
                "started_at": _now(),
                "updated_at": _now(),
            }
            openai_register.config.update({k: self._config[k] for k in ("mail", "proxy", "total", "threads")})
            with openai_register.stats_lock:
                openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": time.time()})
            self._save()
            self._runner = threading.Thread(target=self._run, daemon=True, name="standalone-openai-register")
            self._runner.start()
            self._append_log(f"注册任务启动，模式={self._config['mode']}，线程数={self._config['threads']}", "yellow")
            return self.get()

    def stop(self) -> dict:
        with self._lock:
            self._config["enabled"] = False
            if self._runner and self._runner.is_alive():
                self._config["stats"]["stopping"] = True
            self._config["stats"]["updated_at"] = _now()
            self._save()
            self._append_log("已请求停止注册任务，等待当前任务结束", "yellow")
            return self.get()

    def reset(self) -> dict:
        with self._lock:
            self._logs = []
            self._config["stats"] = {
                "success": 0,
                "fail": 0,
                "done": 0,
                "running": 0,
                "threads": self._config["threads"],
                "elapsed_seconds": 0,
                "avg_seconds": 0,
                "success_rate": 0,
                **self._pool_metrics(),
                "updated_at": _now(),
            }
            with openai_register.stats_lock:
                openai_register.stats.update({"done": 0, "success": 0, "fail": 0, "start_time": 0.0})
            self._save()
            return self.get()

    def _append_log(self, text: str, level: str = "") -> None:
        with self._lock:
            self._logs.append({"time": _now(), "text": str(text), "level": str(level or "info")})
            self._logs = self._logs[-300:]

    def _pool_metrics(self) -> dict:
        normal = [item for item in account_service.list_accounts() if item.get("status") == "正常"]
        return {
            "current_quota": sum(int(item.get("quota") or 0) for item in normal if not item.get("image_quota_unknown")),
            "current_available": len(normal),
        }

    def _target_reached(self, cfg: dict, submitted: int) -> bool:
        metrics = self._pool_metrics()
        self._bump(**metrics)
        if cfg.get("mode") == "quota":
            target = int(cfg.get("target_quota") or 1)
            reached = metrics["current_quota"] >= target
            self._notice_target_state(
                reached,
                f"目标额度已满足：当前剩余额度={metrics['current_quota']}，目标={target}，暂不提交新的注册任务",
                f"目标额度未满足：当前剩余额度={metrics['current_quota']}，目标={target}，继续注册",
            )
            return reached
        if cfg.get("mode") == "available":
            target = int(cfg.get("target_available") or 1)
            reached = metrics["current_available"] >= target
            self._notice_target_state(
                reached,
                f"目标可用账号数已满足：当前正常账号={metrics['current_available']}，目标={target}，暂不提交新的注册任务",
                f"目标可用账号数未满足：当前正常账号={metrics['current_available']}，目标={target}，继续注册",
            )
            return reached
        return submitted >= int(cfg.get("total") or 1)

    def _notice_target_state(self, reached: bool, reached_text: str, pending_text: str) -> None:
        text = reached_text if reached else pending_text
        key = f"{reached}:{text}"
        if key == self._target_notice_key:
            return
        self._target_notice_key = key
        self._append_log(text, "yellow" if reached else "info")

    def _bump(self, **updates) -> None:
        with self._lock:
            self._config["stats"].update(updates)
            stats = self._config["stats"]
            started_at = str(stats.get("started_at") or "")
            if started_at:
                try:
                    elapsed = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds())
                except Exception:
                    elapsed = 0.0
                success = int(stats.get("success") or 0)
                fail = int(stats.get("fail") or 0)
                stats["elapsed_seconds"] = round(elapsed, 1)
                stats["avg_seconds"] = round(elapsed / success, 1) if success else 0
                stats["success_rate"] = round(success * 100 / max(1, success + fail), 1)
            stats["updated_at"] = _now()
            self._save()

    def _upload_registered_account(self, result: dict) -> None:
        account = result.get("result") if isinstance(result.get("result"), dict) else {}
        access_token = str(account.get("access_token") or "").strip()
        targets = config.cliproxy_upload_targets
        if not access_token or not targets:
            return

        export_items = account_service.build_export_items([access_token])
        payload = export_items[0] if export_items else account
        for item in upload_account_to_targets(targets, payload):
            target = item.get("target") or "CLIProxyAPI"
            if item.get("ok"):
                self._append_log(f"CLIProxyAPI upload ok: {target} -> {item.get('message')}", "green")
            else:
                self._append_log(f"CLIProxyAPI upload failed: {target} -> {item.get('message')}", "red")

    def _run(self) -> None:
        threads = int(self.get()["threads"])
        submitted, done, success, fail = 0, 0, 0, 0
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = set()
            while True:
                cfg = self.get()
                while self.get()["enabled"] and not self._target_reached(cfg, submitted) and len(futures) < threads:
                    submitted += 1
                    futures.add(executor.submit(openai_register.worker, submitted))
                self._bump(running=len(futures), done=done, success=success, fail=fail)
                if not futures and (not self.get()["enabled"] or str(cfg.get("mode") or "total") == "total"):
                    break
                if not futures:
                    time.sleep(max(1, int(cfg.get("check_interval") or 5)))
                    continue
                finished, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    done += 1
                    try:
                        result = future.result()
                        success += 1 if result.get("ok") else 0
                        fail += 0 if result.get("ok") else 1
                        if result.get("ok"):
                            self._upload_registered_account(result)
                    except Exception:
                        fail += 1
        self._bump(running=0, done=done, success=success, fail=fail, finished_at=_now())
        with self._lock:
            self._config["enabled"] = False
            self._config["stats"]["stopping"] = False
            self._save()
        self._append_log(f"注册任务结束，成功={success}，失败={fail}", "yellow")


register_service = RegisterService(REGISTER_FILE)
