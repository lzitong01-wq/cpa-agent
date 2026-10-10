# -*- coding: utf-8 -*-
"""账号与权限：注册、登录校验、角色、密码哈希。

密码存储用标准库 `hashlib.pbkdf2_hmac('sha256', ...)`，迭代 240000 次，
每个账号独立随机 salt，校验用 `hmac.compare_digest` 做常量时间比较。
不引入 bcrypt/argon2 是为了不增加二进制依赖；迭代次数写进表里，
将来提高强度时老账号仍可登录（登录成功后自动按新参数重算）。

本模块属于业务层：不认识 HTTP、不认识 session，只回答
"这个用户名密码对不对""这个人是不是管理员"。
"""
import hashlib
import hmac
import logging
import os
import re
import secrets

import config
from app import db

log = logging.getLogger(__name__)

ITERATIONS = 240_000
ROLES = ("admin", "student")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.一-鿿-]{2,32}$")
MIN_PASSWORD_LEN = 6

PUBLIC_FIELDS = ("id", "username", "display_name", "role", "is_active",
                 "created_at", "last_login_at")


# ------------------------------------------------------------------ 密码
def hash_password(password: str, salt: str = None, iterations: int = ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("utf-8"), iterations)
    return digest.hex(), salt, iterations


def verify_password(password: str, row: dict) -> bool:
    if not row or not password:
        return False
    expect = row["password_hash"]
    got, _, _ = hash_password(password, row["password_salt"],
                              int(row["hash_iterations"] or ITERATIONS))
    return hmac.compare_digest(got, expect)


def _public(row):
    return {k: row[k] for k in PUBLIC_FIELDS} if row else None


# ------------------------------------------------------------------ 查询
def get_user(user_id: str):
    return db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def get_by_username(username: str):
    return db.query_one("SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                        (str(username or "").strip(),))


def list_users(limit=200, offset=0) -> list:
    rows = db.query_all(
        "SELECT u.*, "
        "  (SELECT COUNT(*) FROM answer_records r WHERE r.user_id = u.id) AS answer_count, "
        "  (SELECT COUNT(*) FROM wrong_questions w WHERE w.user_id = u.id "
        "     AND w.status = 'open') AS wrong_open "
        "FROM users u ORDER BY u.role, u.username LIMIT ? OFFSET ?", (limit, offset))
    out = []
    for r in rows:
        item = _public(r)
        item["answer_count"] = r["answer_count"]
        item["wrong_open"] = r["wrong_open"]
        out.append(item)
    return out


def count_users() -> int:
    return db.query_scalar("SELECT COUNT(*) FROM users", (), 0)


def admin_exists() -> bool:
    return bool(db.query_scalar(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1", (), 0))

# ------------------------------------------------------------------ 写入
def _validate(username: str, password: str, role: str):
    username = str(username or "").strip()
    if not USERNAME_RE.match(username):
        raise ValueError("用户名只能是 2~32 位的中英文、数字、下划线、点或短横线")
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError("密码至少 %d 位" % MIN_PASSWORD_LEN)
    if role not in ROLES:
        raise ValueError("角色只能是 %s" % " / ".join(ROLES))
    return username


def create_user(username: str, password: str, role: str = "student",
                display_name: str = None, user_id: str = None) -> dict:
    """注册。用户名唯一（大小写不敏感），返回不含密码的公开字段。"""
    username = _validate(username, password, role)
    if get_by_username(username):
        raise ValueError("用户名已被占用：%s" % username)
    uid = user_id or ("u_" + secrets.token_hex(8))
    if get_user(uid):
        raise ValueError("用户 id 冲突：%s" % uid)
    pwd_hash, salt, iters = hash_password(password)
    db.execute(
        "INSERT INTO users (id, username, display_name, password_hash, password_salt, "
        "hash_iterations, role, is_active) VALUES (?,?,?,?,?,?,?,1)",
        (uid, username, display_name or username, pwd_hash, salt, iters, role))
    log.info("新账号：%s（%s）", username, role)
    return _public(get_user(uid))


def authenticate(username: str, password: str) -> dict:
    """登录校验。成功返回公开字段，失败抛 ValueError（不区分"用户不存在/密码错"）。"""
    row = get_by_username(username)
    if not row or not verify_password(password, row):
        raise ValueError("用户名或密码不正确")
    if not row["is_active"]:
        raise ValueError("该账号已被停用，请联系管理员")
    # 迭代次数升级：老账号登录成功后按当前强度重算
    if int(row["hash_iterations"] or 0) < ITERATIONS:
        set_password(row["id"], password)
    db.execute("UPDATE users SET last_login_at = datetime('now','localtime') WHERE id = ?",
               (row["id"],))
    return _public(get_user(row["id"]))


def set_password(user_id: str, new_password: str) -> None:
    if len(new_password or "") < MIN_PASSWORD_LEN:
        raise ValueError("密码至少 %d 位" % MIN_PASSWORD_LEN)
    pwd_hash, salt, iters = hash_password(new_password)
    db.execute("UPDATE users SET password_hash = ?, password_salt = ?, hash_iterations = ? "
               "WHERE id = ?", (pwd_hash, salt, iters, user_id))


def change_password(user_id: str, old_password: str, new_password: str) -> None:
    row = get_user(user_id)
    if not verify_password(old_password, row):
        raise ValueError("原密码不正确")
    set_password(user_id, new_password)


def set_role(user_id: str, role: str) -> dict:
    if role not in ROLES:
        raise ValueError("角色只能是 %s" % " / ".join(ROLES))
    row = get_user(user_id)
    if not row:
        raise ValueError("用户不存在")
    if row["role"] == "admin" and role != "admin" and _active_admin_count() <= 1:
        raise ValueError("至少要保留一个启用状态的管理员")
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    return _public(get_user(user_id))


def set_active(user_id: str, active: bool) -> dict:
    row = get_user(user_id)
    if not row:
        raise ValueError("用户不存在")
    if row["role"] == "admin" and not active and _active_admin_count() <= 1:
        raise ValueError("至少要保留一个启用状态的管理员")
    db.execute("UPDATE users SET is_active = ? WHERE id = ?",
               (1 if active else 0, user_id))
    return _public(get_user(user_id))


def _active_admin_count() -> int:
    return db.query_scalar(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1", (), 0)


def delete_user(user_id: str) -> dict:
    """删除账号，同时清掉该账号的学习数据（答题记录/错题本/对话历史）。"""
    row = get_user(user_id)
    if not row:
        raise ValueError("用户不存在")
    if row["role"] == "admin" and _active_admin_count() <= 1:
        raise ValueError("至少要保留一个启用状态的管理员")
    with db.transaction() as conn:
        for table in ("answer_records", "wrong_questions", "chat_history"):
            conn.execute("DELETE FROM %s WHERE user_id = ?" % table, (user_id,))
        conn.execute("UPDATE retrieval_logs SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    log.info("已删除账号 %s 及其学习数据", row["username"])
    return {"id": user_id, "username": row["username"], "deleted": True}

# ------------------------------------------------------------------ 初始化
def ensure_bootstrap_admin() -> dict:
    """保证系统里至少有一个管理员，否则谁也没法上传教材。

    首个管理员的密码来源（按优先级）：
    1. 环境变量 CPA_ADMIN_PASSWORD；
    2. 随机生成并打印到控制台（只打印这一次）。
    已存在管理员时什么也不做。
    """
    if admin_exists():
        return {"created": False, "reason": "已存在管理员账号"}
    username = os.environ.get("CPA_ADMIN_USER", "admin")
    password = os.environ.get("CPA_ADMIN_PASSWORD", "")
    generated = False
    if not password:
        password = secrets.token_urlsafe(9)
        generated = True
    existing = get_by_username(username)
    if existing:  # 同名普通账号提权，避免用户名冲突导致无法初始化
        set_role(existing["id"], "admin")
        set_password(existing["id"], password)
        user = _public(get_user(existing["id"]))
    else:
        user = create_user(username, password, role="admin",
                           display_name="系统管理员", user_id="admin")
    return {"created": True, "user": user, "username": username,
            "password": password if generated else None, "generated": generated}


def ensure_legacy_users() -> dict:
    """把历史数据里出现过的 user_id 收编成正式账号。

    加登录之前的数据是以 'demo_user' / 'smoke_user' 这类裸字符串写入的，
    直接建同名账号（初始密码 = 用户名 + '123'）比丢弃数据更实用。
    """
    ids = set()
    for table in ("answer_records", "wrong_questions", "chat_history"):
        for r in db.query_all("SELECT DISTINCT user_id FROM %s" % table):
            if r["user_id"]:
                ids.add(r["user_id"])
    ids.add(config.DEFAULT_USER_ID)
    created = []
    for uid in sorted(ids):
        if get_user(uid) or get_by_username(uid):
            continue
        try:
            create_user(uid, str(uid) + "123", role="student",
                        display_name=uid, user_id=uid)
            created.append(uid)
        except ValueError as exc:  # 用户名不合规则跳过，数据保留但需管理员手工处理
            log.warning("历史账号 %s 无法自动建号：%s", uid, exc)
    return {"created": created, "count": len(created)}


