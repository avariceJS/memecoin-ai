"""Operator successor source. The original root stays untouched until its own expiry.

This module does not install LaunchAgents, does not call the network, and does not
rewrite an old source. Parent review calls authorize_successor before any fetch.
"""

import fcntl
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from memecoin_continuous.runtime import guard_source
from memecoin_ml.data import read, sha
from memecoin_paper.runtime import atomic
from memecoin_registry.contracts import digest, seal
from memecoin_runner.ledger import publish

NS = 10**9
WEEK_NS = 7 * 24 * 60 * 60 * NS
MAX_ADDITIONAL = 30000
MAX_BACKGROUND = 6000
ATTEMPT = "attempt-0001"
BINDING_FILES = ("config.json", "universe.json", "provider-seed.json", "seed.json")
SECRET_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
}
POOL_INTS = ("next_ns", "streak", "probes", "probe_day", "history_pages")
INHERITED_STATEMENT = (
    "Квалификация прежнего источника сохранена как унаследованная история. "
    "Она не повторялась и не является новым измерением свежести, edge или качества исполнения."
)
AUTHORIZATION_STATEMENT = (
    "Текущий разбор уже принял отдельный продукт на 7 суток и не больше 30000 дополнительных GET. "
    "Разрешение записывается вместе со сборкой преемника после исходного срока, без новой сессии. "
    "Это не новое измерение свежести и не продолжение остановленного процесса."
)
PROVIDER_DISCLOSURE = (
    "Единственный провайдер — публичный GeckoTerminal OHLCV. "
    "Другие сети и новые когорты здесь не включаются."
)


def explain(code, reason=None):
    """Operator-facing Russian text. Codes stay stable for tests."""
    if code == "old_source_fatal_stop":
        shown = reason or "unknown"
        return f"Прежний источник остановлен ({shown}). Файл STOP не снят, преемник не создаётся."
    if code == "inherited_failures_kept":
        count = reason or "?"
        return f"Унаследовано отказов провайдера: {count}. Счётчик не сброшен."
    text = {
        "operator_expired": "Срок операторского продукта закончился. Корень не продлевается.",
        "operator_paid_forbidden": "Платный бюджет запрещён. Допустима только нулевая оплата.",
        "operator_request_cap": (
            "Дополнительный лимит запросов больше 30000. Конфигурация отклонена."
        ),
        "operator_background_cap": "Фоновый лимит больше 6000. Конфигурация отклонена.",
        "operator_expiry_bound": (
            "Срок продукта длиннее 7 суток от инициализации. Конфигурация отклонена."
        ),
        "operator_profile": "Профиль источника неизвестен. Допустимы balanced и history.",
        "operator_stop": "На корне оператора стоит STOP. Новые действия остановлены.",
        "operator_clock": "Часы раньше инициализации продукта. Действие отклонено.",
        "operator_config": "Конфигурация оператора неполная или не читается.",
        "operator_mode_inactive": "Режим оператора не активен. Тик источника не выполняется.",
        "old_source_unreadable": "Прежний источник не читается. Новый каталог не создаётся.",
        "successor_integrity": "Целостность преемника не подтверждена. Второй бюджет не создаётся.",
        "successor_ready_integrity": (
            "Маркер готовности преемника не сошёлся. Второй бюджет не создаётся."
        ),
        "successor_history_unknown": (
            "История курсоров прежнего источника не подтверждена. Повторная загрузка не начинается."
        ),
        "successor_provider_history_unknown": (
            "История отказов провайдера не подтверждена. Новый бюджет не открывается."
        ),
        "successor_index_missing": (
            "Индекс прежнего источника не найден. Пустой индекс не создаётся."
        ),
        "successor_index_corruption": "Индекс повреждён. Автоисправление не выполняется.",
        "successor_partial_bundle": (
            "Сборка runtime прервана и сохранена. Повторная инициализация бюджета не выполняется."
        ),
        "successor_authorization_integrity": (
            "Привязка разрешения источника не сошлась. Запросы не отправляются."
        ),
        "successor_unauthorized": (
            "Преемник собран. Отдельное разрешение ещё не записано, запросы не отправляются."
        ),
        "successor_pool_mismatch": "Набор рынков преемника не совпал с прежним universe.",
        "successor_cohort": "Горячая когорта должна остаться теми же шестью рынками.",
        "successor_history_floor": "Нижняя граница истории прежнего источника неизвестна.",
        "symlink_forbidden": "Символическая ссылка в корне источника запрещена.",
        "source_profile": "Профиль расписания неизвестен.",
        "resource_priority": (
            "Фаза ресурса неизвестна. Допустимы collect, snapshot, train, paper и waiting."
        ),
        "successor_http_timeout": (
            "Таймаут HTTP прежнего источника не является положительным целым. "
            "Новый предел не подменяется."
        ),
        "successor_source_busy": (
            "Прежний источник ещё держит блокировку записи. Снимок не снимается."
        ),
        "successor_stop": "На преемнике стоит STOP. Повторный тик не выполняется.",
        "expected_expiry_stop": (
            "Остановка совпала со сроком прежнего источника. "
            "Допускается новый продукт, а не продолжение старого процесса."
        ),
        "inherited_cooldown_block": (
            "Следующий запрос ждёт унаследованную паузу провайдера. Счётчики отказов не сброшены."
        ),
        "borrowed": (
            "Исходный источник ещё внутри своего срока. Новый каталог и сеть не используются."
        ),
        "waiting_successor": "Срок исходного источника закончился. Преемник ещё не опубликован.",
        "successor": "Опубликован отдельный источник оператора. Прежний корень не переписывается.",
        "profile_pending": "Профиль сохранён и начнёт влиять после публикации преемника.",
        "profile_active_history": (
            "Профиль history повышает долю фоновой истории. "
            "Горячие дедлайны, пауза 12 с и фоновый ритм сохраняются."
        ),
        "profile_active_balanced": (
            "Профиль balanced чередует холодные запросы и историю. Горячие дедлайны сохраняются."
        ),
        "cooldown": "Провайдер на паузе до конца унаследованного cooldown.",
        "index_unavailable": "Индекс свечей сейчас нельзя прочитать.",
    }
    return text.get(
        code,
        "Состояние источника требует проверки. Автоматическое продолжение остановлено.",
    )


def _code(exc):
    text = str(exc)
    code, _, reason = text.partition(":")
    return code, (reason or None)


def _explained(code):
    name, _, reason = code.partition(":")
    return explain(name, reason or None)


def choose(s, universe, now, c, *, profile, priority="collect"):
    """Hot deadlines and one background slot. History still leaves every third slot to cold.

    snapshot, train and paper drop only a historical background job. Hot deadlines and a
    cold job that the profile already chose stay. collect and waiting keep the usual mix.
    Caps, failures and request spacing are not changed here.
    """
    if profile not in ("balanced", "history"):
        raise ValueError("source_profile")
    if priority not in ("collect", "snapshot", "train", "paper", "waiting"):
        raise ValueError("resource_priority")
    hot = set(c["hot_pools"])
    day = now // (86400 * NS)

    def due(pool, quality):
        probes = quality["probes"] if quality["probe_day"] == day else 0
        return quality["next_ns"] <= now and (quality["status"] == "active" or probes < 2)

    pending = [(q["next_ns"], p) for p, q in s["pools"].items() if p in hot and due(p, q)]
    if pending:
        return dict(
            pool=min(pending)[1],
            lane="prospective",
            before=None,
            limit=100,
            interval_s=c["hot_interval_s"],
            role="hot",
        )
    next_hot = min(
        (q["next_ns"] for p, q in s["pools"].items() if p in hot and q["status"] == "active"),
        default=now + 86400 * NS,
    )
    if (
        next_hot - now < 35 * NS
        or now < s.get("background_next_ns", 0)
        or s.get("background_attempts", 0) >= c["max_background_requests"]
    ):
        return None
    cold = [(q["next_ns"], p) for p, q in s["pools"].items() if p not in hot and due(p, q)]
    history = [
        (s["pools"][u["pool"]]["history_pages"], u["pool"])
        for u in universe
        if s["pools"][u["pool"]]["status"] == "active"
        and not s["pools"][u["pool"]]["history_done"]
        and s["pools"][u["pool"]]["history_pages"] < 30
    ]
    slot = s.get("background_attempts", 0)
    if profile == "balanced":
        prefer_cold = bool(cold) and (slot % 2 == 0 or not history)
    else:
        prefer_cold = bool(cold) and (not history or slot % 3 == 2)
    if prefer_cold:
        return dict(
            pool=min(cold)[1],
            lane="prospective",
            before=None,
            limit=100,
            interval_s=c["cold_interval_s"],
            role="cold",
            background=True,
        )
    if history and priority not in ("snapshot", "train", "paper"):
        pool = min(history)[1]
        return dict(
            pool=pool,
            lane="historical",
            before=s["pools"][pool]["cursor"],
            limit=1000,
            role="history",
            background=True,
        )
    return None


def _clock(now_ns):
    now = time.time_ns() if now_ns is None else now_ns
    if type(now) is not int:
        raise ValueError("operator_clock")
    return now


def _load_operator(root):
    if not isinstance(root, Path) or root.is_symlink() or not root.is_dir():
        raise ValueError("operator_config")
    path = root / "config.json"
    if not path.is_file() or path.is_symlink():
        raise ValueError("operator_config")
    try:
        config = read(path)
    except (OSError, ValueError) as exc:
        raise ValueError("operator_config") from exc
    if not isinstance(config, dict):
        raise ValueError("operator_config")
    if config.get("product") != "operator" or config.get("mode") != "operator":
        raise ValueError("operator_mode_inactive")
    if config.get("provider") != "geckoterminal":
        raise ValueError("operator_config")
    if config.get("profile") not in ("balanced", "history"):
        raise ValueError("operator_profile")
    init = config.get("init_ns")
    expires = config.get("expires_ns")
    if type(init) is not int or type(expires) is not int or expires <= init:
        raise ValueError("operator_config")
    if expires > init + WEEK_NS:
        raise ValueError("operator_expiry_bound")
    if config.get("paid_budget_usd") != 0 or type(config.get("paid_budget_usd")) is not int:
        raise ValueError("operator_paid_forbidden")
    additional = config.get("additional_requests")
    if type(additional) is not int or not 0 <= additional <= MAX_ADDITIONAL:
        raise ValueError("operator_request_cap")
    background = config.get("max_background_requests")
    if type(background) is not int or not 1 <= background <= MAX_BACKGROUND:
        raise ValueError("operator_background_cap")
    raw = config.get("source_root")
    if type(raw) is not str:
        raise ValueError("operator_config")
    source = Path(raw)
    if not source.is_absolute():
        raise ValueError("operator_config")
    config = dict(config)
    config["source_root"] = str(source)
    return config


def _clock_bounds(config, now):
    if now < config["init_ns"]:
        raise ValueError("operator_clock")
    if now >= config["expires_ns"]:
        raise ValueError("operator_expired")


def _stop_reason(root):
    path = root / "STOP"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        return "unknown"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return "unknown"
    reason = data.get("reason") if isinstance(data, dict) else None
    if type(reason) is not str or not reason or len(reason) > 80:
        return "unknown"
    if any(ch.isspace() for ch in reason):
        return "unknown"
    return reason


def _token(value):
    if type(value) is not str or not value or len(value) > 80 or any(ch.isspace() for ch in value):
        return None
    return value


def _collection_profile(root):
    """Live scheduling profile. Only this operator file, only balanced or history."""
    path = root / "collection-profile.json"
    if not path.exists():
        return "balanced"
    if path.is_symlink() or not path.is_file():
        raise ValueError("operator_profile")
    try:
        data = read(path)
    except (OSError, ValueError) as exc:
        raise ValueError("operator_profile") from exc
    if not isinstance(data, dict) or set(data) - {"profile", "at_ns"}:
        raise ValueError("operator_profile")
    if data.get("profile") not in ("balanced", "history"):
        raise ValueError("operator_profile")
    if "at_ns" in data and type(data["at_ns"]) is not int:
        raise ValueError("operator_profile")
    return data["profile"]


def _admit_expiry_stop(old, expires_ns, now, *, audit=True):
    """Admit only the expected deadline stop. Unknown and earlier stops stay closed.

    The read-only pending check uses audit=False. ensure_successor keeps the full raw audit.
    """
    path = old / "STOP"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("old_source_fatal_stop:unknown")
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("old_source_fatal_stop:unknown") from exc
    if not isinstance(data, dict):
        raise ValueError("old_source_fatal_stop:unknown")
    reason = _token(data.get("reason"))
    if reason is None:
        raise ValueError("old_source_fatal_stop:unknown")
    if reason != "source_worker_failure":
        raise ValueError(f"old_source_fatal_stop:{reason}")
    name = data.get("incident")
    if type(name) is not str or name != Path(name).name or not name.startswith("incident-"):
        raise ValueError("old_source_fatal_stop:unknown")
    if not name.endswith(".json"):
        raise ValueError("old_source_fatal_stop:unknown")
    stamp = name[len("incident-") : -len(".json")]
    if not stamp.isdigit():
        raise ValueError("old_source_fatal_stop:unknown")
    incident_ns = int(stamp)
    if incident_ns < expires_ns or now < expires_ns:
        raise ValueError("old_source_fatal_stop:earlier_stop")
    incident_path = old / name
    if incident_path.is_symlink() or not incident_path.is_file():
        raise ValueError("old_source_fatal_stop:unknown")
    try:
        incident = json.loads(incident_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("old_source_fatal_stop:unknown") from exc
    category = incident.get("category") if isinstance(incident, dict) else None
    if category != "source_stop_or_deadline":
        shown = _token(category) or "unknown"
        raise ValueError(f"old_source_fatal_stop:{shown}")
    try:
        guard_source(old)
    except (OSError, ValueError) as exc:
        raise ValueError("old_source_fatal_stop:guard_binding") from exc
    if audit:
        _assert_exact_snapshot(old, now)
    return {
        "kind": "expected_source_deadline",
        "not_a_resume": True,
        "reason": "source_worker_failure",
        "category": "source_stop_or_deadline",
        "incident_name": name,
        "incident_ns": incident_ns,
        "stop_sha": sha(path),
        "incident_sha": sha(incident_path),
    }


def _strip_secrets(value):
    if isinstance(value, dict):
        return {k: _strip_secrets(v) for k, v in value.items() if k.lower() not in SECRET_KEYS}
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


def _load_old_config(old):
    if not old.is_dir() or old.is_symlink():
        raise ValueError("old_source_unreadable")
    path = old / "config.json"
    if not path.is_file() or path.is_symlink():
        raise ValueError("old_source_unreadable")
    try:
        config = read(path)
    except (OSError, ValueError) as exc:
        raise ValueError("old_source_unreadable") from exc
    if type(config.get("expires_ns")) is not int:
        raise ValueError("old_source_unreadable")
    floor = config.get("history_floor_s")
    if type(floor) is not int or floor < 0:
        raise ValueError("successor_history_floor")
    _http_timeout(config)
    hot = config.get("hot_pools")
    if (
        not isinstance(hot, list)
        or len(hot) != 6
        or len(set(hot)) != 6
        or any(type(pool) is not str or not pool for pool in hot)
    ):
        raise ValueError("successor_cohort")
    return config


def _http_timeout(config):
    """Explicit successor timeout. Missing means the fetch default of 15 seconds."""
    if "http_timeout_s" not in config:
        return 15
    timeout = config["http_timeout_s"]
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("successor_http_timeout")
    return timeout


def _resource_priority(root):
    """Operator phase file only. Missing means collect. Extra flags fail closed."""
    path = root / "resource-priority.json"
    if not path.exists():
        return "collect"
    if path.is_symlink() or not path.is_file():
        raise ValueError("resource_priority")
    try:
        data = read(path)
    except (OSError, ValueError) as exc:
        raise ValueError("resource_priority") from exc
    if not isinstance(data, dict) or set(data) != {"phase", "at_ns"}:
        raise ValueError("resource_priority")
    if data.get("phase") not in ("collect", "snapshot", "train", "paper", "waiting"):
        raise ValueError("resource_priority")
    if type(data.get("at_ns")) is not int:
        raise ValueError("resource_priority")
    return data["phase"]


def _universe(old, hot):
    path = old / "universe.json"
    if not path.is_file() or path.is_symlink():
        raise ValueError("old_source_unreadable")
    try:
        data = _strip_secrets(read(path))
    except (OSError, ValueError) as exc:
        raise ValueError("old_source_unreadable") from exc
    rows = data.get("pools") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("successor_pool_mismatch")
    names = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("pool")) is not str or not row["pool"]:
            raise ValueError("successor_pool_mismatch")
        names.append(row["pool"])
    if len(names) != len(set(names)) or any(pool not in names for pool in hot):
        raise ValueError("successor_pool_mismatch")
    return data, names


def _last_journal_state(old):
    journal = old / "journal"
    if not journal.exists():
        return None
    if journal.is_symlink() or not journal.is_dir():
        raise ValueError("successor_integrity")
    binding_path = journal / "binding.json"
    if not binding_path.is_file() or binding_path.is_symlink():
        raise ValueError("successor_provider_history_unknown")
    try:
        binding = json.loads(binding_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("successor_integrity") from exc
    previous = digest(binding)
    last = None
    paths = sorted(p for p in journal.glob("event-*.json") if p.is_file())
    for i, path in enumerate(paths):
        if path.name != f"event-{i:06d}.json" or path.is_symlink():
            raise ValueError("successor_integrity")
        try:
            row = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("successor_integrity") from exc
        checksum = row.pop("sha256", None)
        if row.get("previous") != previous or row.get("sequence") != i or digest(row) != checksum:
            raise ValueError("successor_integrity")
        payload = row.get("payload")
        if not isinstance(payload, dict) or "state" not in payload:
            raise ValueError("successor_integrity")
        previous = checksum
        last = payload["state"]
    return last


def _validate_state(state, names):
    if not isinstance(state, dict):
        raise ValueError("successor_provider_history_unknown")
    for key in ("last_ns", "cooldown_ns", "failures", "attempts"):
        if type(state.get(key)) is not int or state[key] < 0:
            raise ValueError("successor_provider_history_unknown")
    events = state.get("rate_events")
    if not isinstance(events, list) or any(type(item) is not int or item < 0 for item in events):
        raise ValueError("successor_provider_history_unknown")
    if "bytes" in state and (type(state["bytes"]) is not int or state["bytes"] < 0):
        raise ValueError("successor_provider_history_unknown")
    pools = state.get("pools")
    if not isinstance(pools, dict) or set(pools) != set(names):
        raise ValueError("successor_pool_mismatch")
    for quality in pools.values():
        if not isinstance(quality, dict):
            raise ValueError("successor_history_unknown")
        for key in POOL_INTS:
            if type(quality.get(key)) is not int:
                raise ValueError("successor_history_unknown")
        if quality["history_pages"] < 0 or quality["history_pages"] > 30:
            raise ValueError("successor_history_unknown")
        if quality.get("status") not in ("active", "quarantined"):
            raise ValueError("successor_history_unknown")
        if type(quality.get("history_done")) is not bool:
            raise ValueError("successor_history_unknown")
        if quality.get("cursor") is not None and type(quality.get("cursor")) is not int:
            raise ValueError("successor_history_unknown")
    return state


def _inherited_state(old, names):
    journal_state = _last_journal_state(old)
    if journal_state is not None:
        return _validate_state(journal_state, names)
    seed_path = old / "seed.json"
    provider_path = old / "provider-seed.json"
    if not seed_path.is_file() or seed_path.is_symlink():
        raise ValueError("successor_history_unknown")
    if not provider_path.is_file() or provider_path.is_symlink():
        raise ValueError("successor_provider_history_unknown")
    try:
        seed = read(seed_path)
        provider = read(provider_path)
    except (OSError, ValueError) as exc:
        raise ValueError("successor_history_unknown") from exc
    if not isinstance(seed, dict) or "scheduler_state" not in seed:
        raise ValueError("successor_history_unknown")
    if not isinstance(provider, dict):
        raise ValueError("successor_provider_history_unknown")
    merged = dict(provider)
    merged["pools"] = seed["scheduler_state"]
    return _validate_state(merged, names)


def _qualification_ref(old):
    path = old / "qualification.json"
    if not path.exists():
        return None, None
    if path.is_symlink() or not path.is_file():
        raise ValueError("successor_integrity")
    try:
        data = read(path)
    except (OSError, ValueError) as exc:
        raise ValueError("successor_integrity") from exc
    approved = data.get("approved") if isinstance(data, dict) else None
    if type(approved) is not bool:
        approved = None
    return sha(path), approved


def _ro_connect(path):
    """Read-only SQLite connection. Coordination sidecars may appear. The file is not frozen."""
    path = Path(path).resolve()
    if path.is_symlink() or not path.is_file():
        raise sqlite3.OperationalError("index missing")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=20)


def logical_index(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("successor_index_missing")
    db = _ro_connect(path)
    try:
        db.execute("BEGIN DEFERRED")
        check = db.execute("pragma integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise ValueError("successor_index_corruption")
        payload = {
            "firsts": [
                list(row)
                for row in db.execute(
                    "select pool,open_s,available,pin,payload,checksum "
                    "from firsts order by pool,open_s"
                )
            ],
            "versions": [
                list(row)
                for row in db.execute(
                    "select pool,open_s,available,pin,payload,checksum from versions "
                    "order by pool,open_s,pin"
                )
            ],
            "captures": [
                list(row)
                for row in db.execute(
                    "select pin,path,available,status,n,record,checksum from captures order by pin"
                )
            ],
        }
        db.execute("ROLLBACK")
        return digest(payload)
    except sqlite3.Error as exc:
        raise ValueError("successor_index_corruption") from exc
    finally:
        db.close()


def _backup_index(src, dest):
    """Lock the source writer, then backup from one deferred read transaction."""
    if not src.is_file() or src.is_symlink():
        raise ValueError("successor_index_missing")
    if dest.exists() or dest.is_symlink():
        raise ValueError("successor_integrity")
    partial = dest.with_name("index.sqlite.partial")
    if partial.exists() or partial.is_symlink():
        raise ValueError("successor_integrity")
    origin = src.parent
    lock_path = origin / ".source.lock"
    if lock_path.is_symlink():
        raise ValueError("symlink_forbidden")
    lock_file = lock_path.open("a")
    source_db = None
    target = None
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("successor_source_busy") from exc
        source_db = _ro_connect(src)
        source_db.execute("BEGIN DEFERRED")
        target = sqlite3.connect(partial)
        source_db.backup(target)
        target.commit()
        source_db.execute("ROLLBACK")
    except ValueError:
        raise
    except sqlite3.Error as exc:
        raise ValueError("successor_index_corruption") from exc
    finally:
        if target is not None:
            target.close()
        if source_db is not None:
            source_db.close()
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()
    os.replace(partial, dest)


def _assert_exact_snapshot(origin, cutoff):
    """Backup through SQLite, then audit that copy. This is not a three-file WAL copy."""
    from memecoin_continuous.store import audit

    index = origin / "index.sqlite"
    if not index.is_file() or index.is_symlink():
        raise ValueError("old_source_fatal_stop:index_snapshot")
    try:
        with tempfile.TemporaryDirectory(prefix="operator-audit-") as tmp:
            root = Path(tmp)
            _backup_index(index, root / "index.sqlite")
            audit(root, cutoff)
    except ValueError as exc:
        if str(exc) == "successor_source_busy":
            raise
        if str(exc).startswith("old_source_fatal_stop:"):
            raise
        raise ValueError("old_source_fatal_stop:index_snapshot") from exc


def _adopt_index(old_index, dest, attempt):
    if (dest.parent / "index.sqlite.partial").exists():
        raise ValueError("successor_integrity")
    if not dest.is_file() or dest.is_symlink():
        _backup_index(old_index, dest)
        return
    verify_copy = attempt / "index-verify.sqlite"
    if verify_copy.exists():
        raise ValueError("successor_integrity")
    _backup_index(old_index, verify_copy)
    try:
        if logical_index(verify_copy) != logical_index(dest):
            raise ValueError("successor_integrity")
    except ValueError:
        raise
    else:
        verify_copy.unlink()


def _materials(op, old, old_config, created_ns, state, universe, qualification, admission):
    pools = json.loads(json.dumps(state["pools"]))
    qsha, qapproved = qualification
    hot = list(old_config["hot_pools"])
    source_config = {
        "product": "operator_successor",
        "created_ns": created_ns,
        "expires_ns": op["expires_ns"],
        "paid_budget_usd": 0,
        "max_requests": op["additional_requests"],
        "additional_requests": op["additional_requests"],
        "prior_provider_attempts": state["attempts"],
        "prior_provider_bytes": state.get("bytes", 0),
        "max_background_requests": op["max_background_requests"],
        "hot_pools": hot,
        "hot_interval_s": 120,
        "cold_interval_s": 1800,
        "request_spacing_s": 12,
        "http_timeout_s": _http_timeout(old_config),
        "history_floor_s": old_config["history_floor_s"],
        "qualification_expires_ns": created_ns,
        "qualification_max_requests": 0,
        "service_prefix": "ai.memecoin.operator.b13.",
        "provider": "geckoterminal",
        "provider_disclosure": PROVIDER_DISCLOSURE,
        "profile": op["profile"],
        "measurement": False,
        "inherited_source": str(old.resolve()),
    }
    provider_seed = {
        "last_ns": state["last_ns"],
        "cooldown_ns": state["cooldown_ns"],
        "failures": state["failures"],
        "rate_events": list(state["rate_events"]),
        "attempts": 0,
        "bytes": 0,
        "prior_provider_attempts": state["attempts"],
        "prior_provider_bytes": state.get("bytes", 0),
        "inherited_source": str(old.resolve()),
    }
    seed = {"quality": {}, "scheduler_state": pools, "inherited": True}
    inheritance = {
        "kind": "inherited_source_state",
        "measurement": False,
        "statement": INHERITED_STATEMENT,
        "source": str(old.resolve()),
        "prior_provider_attempts": state["attempts"],
        "prior_provider_bytes": state.get("bytes", 0),
        "failures": state["failures"],
        "rate_events": list(state["rate_events"]),
        "cooldown_ns": state["cooldown_ns"],
        "last_ns": state["last_ns"],
        "history_floor_s": old_config["history_floor_s"],
        "hot_pools": hot,
        "quarantined": sorted(
            pool for pool, quality in pools.items() if quality["status"] == "quarantined"
        ),
        "inherited_qualification_sha": qsha,
        "inherited_qualification_approved": qapproved,
        "expiry_admission": admission,
    }
    return {
        "config.json": source_config,
        "universe.json": universe,
        "provider-seed.json": provider_seed,
        "seed.json": seed,
        "inheritance.json": inheritance,
    }


def _adopt_files(source, expected):
    for name, value in expected.items():
        path = source / name
        if path.is_symlink():
            raise ValueError("symlink_forbidden")
        if path.exists() and read(path) != value:
            raise ValueError("successor_integrity")
    for name, value in expected.items():
        path = source / name
        if not path.exists():
            publish(path, value)


def stage_successor_bundle(source, bundle):
    """Copy the runtime tree and seal the binding. Does not write a LaunchAgent."""
    if bundle.exists() or bundle.is_symlink():
        raise ValueError("successor_partial_bundle")
    for name in BINDING_FILES:
        if not (source / name).is_file():
            raise ValueError("successor_integrity")
    bundle.mkdir()
    src = Path(__file__).resolve().parents[1]
    copied = False
    for path in src.glob("memecoin_*/*.py"):
        dest = bundle / "runtime" / path.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        copied = True
    if not copied:
        raise ValueError("successor_partial_bundle")
    publish(
        bundle / "binding.json",
        {
            "root": str(source.resolve()),
            "python_sha": sha(Path(sys.executable).resolve()),
            "files": {name: sha(source / name) for name in BINDING_FILES},
        },
    )
    seal(bundle)
    pin = sha(bundle / "manifest.json")
    publish(source / "deployment.json", {"bundle": str(bundle.resolve()), "pin": pin})
    guard_source(source)
    return pin


def _adopt_bundle(source, bundle):
    if not bundle.is_dir() or bundle.is_symlink():
        raise ValueError("successor_partial_bundle")
    try:
        guard_source(source)
    except ValueError as exc:
        raise ValueError("successor_partial_bundle") from exc


def _receipt(root, op, index_id):
    """Immutable pins plus the seed index id. Later candles are not part of this receipt."""
    source = root / "source"
    deployment = read(source / "deployment.json")
    config = read(source / "config.json")
    return {
        "additional_requests": op["additional_requests"],
        "attempt": ATTEMPT,
        "bundle": deployment["bundle"],
        "config_sha": sha(source / "config.json"),
        "deployment_pin": deployment["pin"],
        "expires_ns": op["expires_ns"],
        "index_id": index_id,
        "inheritance_sha": sha(source / "inheritance.json"),
        "init_ns": op["init_ns"],
        "paid_budget_usd": 0,
        "prior_provider_attempts": config["prior_provider_attempts"],
        "provider_seed_sha": sha(source / "provider-seed.json"),
        "seed_sha": sha(source / "seed.json"),
        "source": str(source.resolve()),
        "source_root": str(Path(op["source_root"]).resolve()),
        "universe_sha": sha(source / "universe.json"),
    }


def _verify_ready(root):
    path = root / "successor-ready.json"
    if not path.is_file() or path.is_symlink():
        raise ValueError("successor_ready_integrity")
    try:
        saved = read(path)
    except (OSError, ValueError) as exc:
        raise ValueError("successor_ready_integrity") from exc
    if not isinstance(saved, dict) or "ready_sha" not in saved:
        raise ValueError("successor_ready_integrity")
    ready_sha = saved.pop("ready_sha")
    stage_path = root / "staging" / ATTEMPT / "stage.json"
    if not stage_path.is_file() or stage_path.is_symlink():
        raise ValueError("successor_ready_integrity")
    try:
        stage = read(stage_path)
    except (OSError, ValueError) as exc:
        raise ValueError("successor_ready_integrity") from exc
    if not isinstance(stage, dict) or type(stage.get("index_id")) is not str:
        raise ValueError("successor_ready_integrity")
    op = _load_operator(root)
    receipt = _receipt(root, op, stage["index_id"])
    if digest(saved) != ready_sha or saved != stage or saved != receipt:
        raise ValueError("successor_ready_integrity")
    saved["ready_sha"] = ready_sha
    return saved


def _publish_ready(root, op):
    path = root / "successor-ready.json"
    if path.exists():
        return _verify_ready(root)
    stage_path = root / "staging" / ATTEMPT / "stage.json"
    try:
        stage = read(stage_path)
    except (OSError, ValueError) as exc:
        raise ValueError("successor_ready_integrity") from exc
    if not isinstance(stage, dict) or type(stage.get("index_id")) is not str:
        raise ValueError("successor_ready_integrity")
    identity = _receipt(root, op, stage["index_id"])
    if identity != stage:
        raise ValueError("successor_ready_integrity")
    body = dict(identity)
    body["ready_sha"] = digest(identity)
    publish(path, body)
    return body


def _result(phase, **extra):
    body = {"phase": phase, "network": False, "message": explain(phase)}
    body.update(extra)
    return body


def ensure_successor(root, now_ns=None):
    """Build one successor after the original expiry. Never renews the operator root."""
    op = _load_operator(root)
    now = _clock(now_ns)
    _clock_bounds(op, now)
    if _stop_reason(root) is not None:
        raise ValueError("operator_stop")
    old = Path(op["source_root"])
    old_config = _load_old_config(old)
    if now < old_config["expires_ns"]:
        return _result(
            "borrowed",
            created=False,
            resumed=False,
            reused=False,
            source=str(old),
            expires_ns=old_config["expires_ns"],
            operator_expires_ns=op["expires_ns"],
            profile_effect="pending",
        )
    ready_path = root / "successor-ready.json"
    if ready_path.exists():
        ready = _verify_ready(root)
        authorize_successor(root, now_ns=now)
        config = read(root / "source" / "config.json")
        return _result(
            "successor",
            created=False,
            resumed=False,
            reused=True,
            source=str(root / "source"),
            expires_ns=op["expires_ns"],
            original_expires_ns=old_config["expires_ns"],
            additional_requests=config["additional_requests"],
            prior_provider_attempts=config["prior_provider_attempts"],
            max_requests=config["max_requests"],
            ready_sha=ready["ready_sha"],
            authorization="bound",
        )
    admission = _admit_expiry_stop(old, old_config["expires_ns"], now)
    universe, names = _universe(old, old_config["hot_pools"])
    state = _inherited_state(old, names)
    qualification = _qualification_ref(old)
    if not (old / "index.sqlite").is_file() or (old / "index.sqlite").is_symlink():
        raise ValueError("successor_index_missing")
    staging = root / "staging"
    existed = staging.exists() or (root / "source").exists()
    if staging.exists():
        if staging.is_symlink() or not staging.is_dir():
            raise ValueError("successor_integrity")
        extra = [path.name for path in staging.iterdir() if path.name != ATTEMPT]
        if extra:
            raise ValueError("successor_integrity")
    else:
        staging.mkdir()
    attempt = staging / ATTEMPT
    if attempt.exists() and (attempt.is_symlink() or not attempt.is_dir()):
        raise ValueError("successor_integrity")
    attempt.mkdir(exist_ok=True)
    source = root / "source"
    if source.is_symlink() or (source.exists() and not source.is_dir()):
        raise ValueError("symlink_forbidden" if source.is_symlink() else "successor_integrity")
    source.mkdir(exist_ok=True)
    created_ns = now
    if (source / "config.json").exists():
        existing = read(source / "config.json")
        if type(existing.get("created_ns")) is not int:
            raise ValueError("successor_integrity")
        created_ns = existing["created_ns"]
    expected = _materials(
        op, old, old_config, created_ns, state, universe, qualification, admission
    )
    _adopt_files(source, expected)
    old_index = old / "index.sqlite"
    _adopt_index(old_index, source / "index.sqlite", attempt)
    bundle = root / "source-runtime"
    if bundle.exists():
        _adopt_bundle(source, bundle)
    else:
        if (source / "deployment.json").exists():
            raise ValueError("successor_integrity")
        stage_successor_bundle(source, bundle)
    from memecoin_continuous.store import audit

    try:
        audit(source, now)
    except ValueError as exc:
        raise ValueError("successor_index_corruption") from exc
    index_id = logical_index(source / "index.sqlite")
    atomic(attempt / "stage.json", _receipt(root, op, index_id))
    ready = _publish_ready(root, op)
    authorize_successor(root, now_ns=now)
    config = expected["config.json"]
    return _result(
        "successor",
        created=not existed,
        resumed=bool(existed),
        reused=False,
        source=str(source),
        expires_ns=op["expires_ns"],
        original_expires_ns=old_config["expires_ns"],
        additional_requests=config["additional_requests"],
        prior_provider_attempts=config["prior_provider_attempts"],
        max_requests=config["max_requests"],
        ready_sha=ready["ready_sha"],
        authorization="bound",
    )


def _authorization_bound(source):
    act = source / "activation.json"
    qual = source / "qualification.json"
    dep = source / "deployment.json"
    if not (act.is_file() and qual.is_file() and dep.is_file()):
        return False
    if act.is_symlink() or qual.is_symlink():
        return False
    try:
        activation = read(act)
        qualification = read(qual)
        pin = read(dep)["pin"]
    except (OSError, ValueError, KeyError):
        return False
    if qualification.get("measurement") is not False or qualification.get("approved") is not True:
        return False
    if qualification.get("freshness_remeasured") is not False:
        return False
    if qualification.get("kind") != "successor_operator_authorization":
        return False
    if activation.get("measurement") is not False:
        return False
    if activation.get("config_sha") != sha(source / "config.json"):
        return False
    if activation.get("runtime_pin") != pin:
        return False
    return activation.get("qualification_sha") == sha(qual)


def authorize_successor(root, now_ns=None):
    """Parent-reviewed latch for hot.authorization. This is not a new freshness measurement."""
    op = _load_operator(root)
    now = _clock(now_ns)
    _clock_bounds(op, now)
    if _stop_reason(root) is not None:
        raise ValueError("operator_stop")
    _verify_ready(root)
    source = root / "source"
    inherited = read(source / "inheritance.json")
    bad_measurement = inherited.get("measurement") is not False
    bad_statement = inherited.get("statement") != INHERITED_STATEMENT
    if bad_measurement or bad_statement:
        raise ValueError("successor_authorization_integrity")
    qual = {
        "approved": True,
        "measurement": False,
        "freshness_remeasured": False,
        "kind": "successor_operator_authorization",
        "statement": AUTHORIZATION_STATEMENT,
        "inherited_statement": inherited["statement"],
        "inherited_qualification_sha": inherited.get("inherited_qualification_sha"),
        "inherited_qualification_approved": inherited.get("inherited_qualification_approved"),
        "inherited_source": inherited["source"],
        "paid_budget_usd": 0,
    }
    qual_path = source / "qualification.json"
    if qual_path.exists():
        if qual_path.is_symlink() or read(qual_path) != qual:
            raise ValueError("successor_authorization_integrity")
    else:
        publish(qual_path, qual)
    activation = {
        "config_sha": sha(source / "config.json"),
        "runtime_pin": read(source / "deployment.json")["pin"],
        "qualification_sha": sha(qual_path),
        "kind": "successor_operator_authorization",
        "measurement": False,
        "statement": AUTHORIZATION_STATEMENT,
    }
    act_path = source / "activation.json"
    if act_path.exists():
        if act_path.is_symlink() or read(act_path) != activation:
            raise ValueError("successor_authorization_integrity")
    else:
        publish(act_path, activation)
    if not _authorization_bound(source):
        raise ValueError("successor_authorization_integrity")
    return {
        "authorization": "bound",
        "measurement": False,
        "network": False,
        "qualification_sha": activation["qualification_sha"],
        "message": AUTHORIZATION_STATEMENT,
    }


def successor_pending(root, now_ns=None):
    """True only while a qualified handoff is still waiting. Errors return False.

    This does not audit raw captures, create a successor, or change STOP.
    ensure_successor still audits before activation.
    """
    try:
        op = _load_operator(root)
        now = _clock(now_ns)
        if now < op["init_ns"] or now >= op["expires_ns"]:
            return False
        old = Path(op["source_root"])
        old_config = _load_old_config(old)
        if now < old_config["expires_ns"]:
            return False
        ready = root / "successor-ready.json"
        if ready.exists():
            _verify_ready(root)
            return False
        _admit_expiry_stop(old, old_config["expires_ns"], now, audit=False)
    except (OSError, ValueError):
        return False
    return True


def current_source(root):
    op = _load_operator(root)
    ready = root / "successor-ready.json"
    if not ready.exists():
        return Path(op["source_root"])
    _verify_ready(root)
    return root / "source"


def _counts(path, hot):
    if not path.is_file() or path.is_symlink():
        return None
    db = None
    try:
        db = _ro_connect(path)
        db.execute("BEGIN DEFERRED")
        bars = db.execute("select count(*) from firsts").fetchone()[0]
        markets = db.execute("select count(distinct pool) from firsts").fetchone()[0]
        captures = db.execute("select count(*) from captures").fetchone()[0]
        versions = db.execute("select count(*) from versions").fetchone()[0]
        present = {
            row[0] for row in db.execute("select distinct pool from firsts") if row[0] in set(hot)
        }
        db.execute("ROLLBACK")
    except (sqlite3.Error, OSError):
        return None
    finally:
        if db is not None:
            db.close()
    return {
        "bars": bars,
        "markets": markets,
        "captures": captures,
        "versions": versions,
        "hot_pools_with_bars": len(present),
    }


def _health(root):
    path = root / "health.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = read(path)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    keep = {}
    for key in ("at_ns", "attempts", "cooldown_ns", "failures"):
        if type(data.get(key)) is int:
            keep[key] = data[key]
    pools = data.get("pools")
    if isinstance(pools, dict):
        safe = {}
        for pool, quality in pools.items():
            if not isinstance(quality, dict):
                continue
            safe[pool] = {
                "status": quality.get("status"),
                "history_pages": quality.get("history_pages"),
                "history_done": quality.get("history_done"),
                "cursor": quality.get("cursor"),
            }
        keep["pools"] = safe
    return keep


def _blocked(code, reason=None):
    message = explain(code, reason)
    return {
        "ok": False,
        "phase": "blocked",
        "profile": None,
        "profile_effect": "pending",
        "priority": None,
        "provider": "geckoterminal",
        "provider_disclosure": PROVIDER_DISCLOSURE,
        "network": False,
        "authorization": None,
        "errors": [{"code": code, "message": message}],
        "notices": [],
        "message": message,
        "bars": None,
        "markets": None,
        "coverage": None,
        "bounds": None,
        "inherited": None,
        "progress": None,
        "source": None,
    }


def source_status(root, now_ns=None):
    """Read-only public status. Local paths and pool ids are operational, not credentials."""
    try:
        op = _load_operator(root)
        now = _clock(now_ns)
        old = Path(op["source_root"])
        old_config = _load_old_config(old)
    except ValueError as exc:
        code, reason = _code(exc)
        return _blocked(code, reason)
    errors = []
    notices = []
    if now < op["init_ns"]:
        errors.append("operator_clock")
    if now >= op["expires_ns"]:
        errors.append("operator_expired")
    if _stop_reason(root) is not None:
        errors.append("operator_stop")
    ready = root / "successor-ready.json"
    phase = "blocked"
    source = old
    if errors:
        phase = "blocked"
    elif ready.exists():
        try:
            _verify_ready(root)
            phase = "successor"
            source = root / "source"
        except ValueError as exc:
            errors.append(_code(exc)[0])
            phase = "blocked"
    elif now < old_config["expires_ns"]:
        phase = "borrowed"
    else:
        try:
            admission = _admit_expiry_stop(old, old_config["expires_ns"], now)
        except ValueError as exc:
            errors.append(str(exc))
            phase = "blocked"
        else:
            phase = "waiting_successor"
            if admission is not None:
                notices.append("expected_expiry_stop")
    state = None
    counters_source = None
    if phase != "blocked":
        try:
            state = _last_journal_state(old if phase == "borrowed" else source)
            if state is not None:
                counters_source = "journal"
        except ValueError as exc:
            errors.append(_code(exc)[0])
            phase = "blocked"
    if state is None and phase == "successor":
        try:
            state = read(source / "provider-seed.json")
            pools = read(source / "seed.json")["scheduler_state"]
            state = dict(state)
            state["pools"] = pools
            counters_source = "provider-seed"
        except (OSError, ValueError, KeyError):
            errors.append("successor_integrity")
            phase = "blocked"
    if state is None and phase == "borrowed":
        health = _health(old)
        if health and "failures" in health:
            state = health
            counters_source = "health"
    live_profile = None
    try:
        live_profile = _collection_profile(root)
    except ValueError as exc:
        errors.append(str(exc))
    priority = None
    try:
        priority = _resource_priority(root)
    except ValueError as exc:
        errors.append(str(exc))
    if phase == "successor" and live_profile:
        profile = live_profile
        profile_effect = "active"
        if profile == "history":
            profile_code = "profile_active_history"
        else:
            profile_code = "profile_active_balanced"
    else:
        profile = live_profile
        profile_effect = "pending"
        profile_code = "profile_pending"
    if phase == "successor" and (source / "STOP").exists():
        errors.append("successor_stop")
    auth = None
    if phase == "successor":
        auth = "bound" if _authorization_bound(source) else "pending_parent_review"
        if auth != "bound":
            errors.append("successor_unauthorized")
    counts = None
    if phase in ("borrowed", "successor", "waiting_successor"):
        index_root = source if phase == "successor" else old
        counts = _counts(index_root / "index.sqlite", old_config["hot_pools"])
        if counts is None:
            notices.append("index_unavailable")
    inherited = None
    if isinstance(state, dict) and "failures" in state:
        inherited = {
            "failures": state.get("failures"),
            "rate_events": state.get("rate_events"),
            "cooldown_ns": state.get("cooldown_ns"),
            "last_ns": state.get("last_ns"),
            "counters_source": counters_source,
            "quarantined": sorted(
                pool
                for pool, quality in state.get("pools", {}).items()
                if isinstance(quality, dict) and quality.get("status") == "quarantined"
            ),
        }
        projected = None
        cooldown = state.get("cooldown_ns")
        failures = state.get("failures")
        if type(cooldown) is int and cooldown > now:
            projected = explain("inherited_cooldown_block")
            notices.append("inherited_cooldown_block")
        elif type(failures) is int and failures > 0:
            projected = explain("inherited_failures_kept", str(failures))
        inherited["projected_block"] = projected
    sealed = None
    if phase == "successor":
        try:
            sealed = read(source / "config.json")
        except (OSError, ValueError):
            errors.append("successor_integrity")
    bounds = {
        "operator_init_ns": op["init_ns"],
        "operator_expires_ns": op["expires_ns"],
        "original_expires_ns": old_config["expires_ns"],
        "paid_budget_usd": 0,
        "additional_requests": op["additional_requests"],
        "history_floor_s": old_config["history_floor_s"],
        "http_timeout_s": _http_timeout(old_config),
        "planned_successor": {
            "hot_interval_s": 120,
            "cold_interval_s": 1800,
            "request_spacing_s": 12,
            "http_timeout_s": _http_timeout(old_config),
            "max_background_requests": op["max_background_requests"],
            "max_requests": op["additional_requests"],
        },
    }
    if sealed:
        bounds["successor_expires_ns"] = sealed.get("expires_ns")
        bounds["prior_provider_attempts"] = sealed.get("prior_provider_attempts")
        bounds["enforced"] = {
            "hot_interval_s": sealed.get("hot_interval_s"),
            "cold_interval_s": sealed.get("cold_interval_s"),
            "request_spacing_s": sealed.get("request_spacing_s"),
            "http_timeout_s": sealed.get("http_timeout_s"),
            "max_background_requests": sealed.get("max_background_requests"),
            "max_requests": sealed.get("max_requests"),
        }
    else:
        for key in ("hot_interval_s", "cold_interval_s", "request_spacing_s", "max_requests"):
            if type(old_config.get(key)) is int:
                bounds[key] = old_config[key]
    coverage = None
    if counts is not None:
        coverage = {
            "hot_pools": len(old_config["hot_pools"]),
            "hot_pools_with_bars": counts["hot_pools_with_bars"],
            "admitted": None,
            "quarantined": inherited["quarantined"] if inherited else [],
        }
        try:
            _, names = _universe(old, old_config["hot_pools"])
            coverage["admitted"] = len(names)
        except ValueError:
            coverage["admitted"] = None
    progress = None
    if sealed or isinstance(state, dict):
        progress = {
            "attempts": None if not isinstance(state, dict) else state.get("attempts"),
            "prior_provider_attempts": None
            if not sealed
            else sealed.get("prior_provider_attempts"),
            "max_requests": None if not sealed else sealed.get("max_requests"),
            "additional_requests": op["additional_requests"],
        }
    messages = [explain(phase), explain(profile_code)]
    if inherited and inherited.get("projected_block"):
        messages.append(inherited["projected_block"])
    messages.extend(_explained(code) for code in errors)
    return {
        "ok": not errors and phase in ("borrowed", "successor"),
        "phase": phase,
        "profile": profile,
        "profile_effect": profile_effect,
        "priority": priority,
        "recorded_profile": None if not sealed else sealed.get("profile"),
        "provider": "geckoterminal",
        "provider_disclosure": PROVIDER_DISCLOSURE,
        "network": False,
        "authorization": auth,
        "source": str(source),
        "errors": [{"code": code.split(":", 1)[0], "message": _explained(code)} for code in errors],
        "notices": [{"code": code, "message": explain(code)} for code in notices],
        "message": " ".join(messages),
        "bars": None if counts is None else counts["bars"],
        "markets": None if counts is None else counts["markets"],
        "coverage": coverage,
        "bounds": bounds,
        "inherited": inherited,
        "progress": progress,
    }


def run_source(root):
    """One tick of the owned successor. The parent supervisor and lease stay outside."""
    op = _load_operator(root)
    now = time.time_ns()
    _clock_bounds(op, now)
    priority = _resource_priority(root)
    if _stop_reason(root) is not None:
        raise ValueError("operator_stop")
    if not (root / "successor-ready.json").exists():
        return _result("borrowed", ticked=False, source=op["source_root"], profile_effect="pending")
    _verify_ready(root)
    source_root = root / "source"
    stop = source_root / "STOP"
    if stop.is_symlink() or stop.exists():
        raise ValueError("successor_stop")
    if not _authorization_bound(source_root):
        return {
            "phase": "successor_unauthorized",
            "ticked": False,
            "network": False,
            "source": str(source_root),
            "message": explain("successor_unauthorized"),
        }
    guard_source(source_root)
    import memecoin_continuous.hot as hot
    import memecoin_continuous.source as source_mod

    profile = _collection_profile(root)
    original = hot.choose

    def patched(state, universe, now_ns, config):
        return choose(state, universe, now_ns, config, profile=profile, priority=priority)

    hot.choose = patched
    try:
        source_mod.tick(source_root)
    except BlockingIOError:
        raise
    except Exception:
        stop = source_root / "STOP"
        if not stop.exists() and not stop.is_symlink():
            publish(stop, dict(reason="unknown", at_ns=time.time_ns()))
        raise
    finally:
        hot.choose = original
    return {
        "phase": "successor",
        "ticked": True,
        "source": str(source_root),
        "profile": profile,
        "message": explain(
            "profile_active_history" if profile == "history" else "profile_active_balanced"
        ),
    }


def worker_command(root):
    """Argv for one supervised successor tick. Does not install a LaunchAgent."""
    _verify_ready(root)
    source = root / "source"
    if not _authorization_bound(source):
        raise ValueError("successor_unauthorized")
    deployment = read(source / "deployment.json")
    runtime = str((Path(deployment["bundle"]) / "runtime").resolve())
    script = (
        "import sys;sys.path.insert(0,sys.argv.pop(1));"
        "from pathlib import Path;"
        "from memecoin_operator.acquisition import run_source;"
        "run_source(Path(sys.argv[1]))"
    )
    return [sys.executable, "-I", "-B", "-c", script, runtime, str(root.resolve())]
