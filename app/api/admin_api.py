# -*- coding: utf-8 -*-
"""管理员接口：上传教材、管理知识库、管理账号。

全部依赖 AdminUser —— 少写一个依赖就拿不到 user 参数，
所以不存在"忘了加鉴权"的路由。

上传是唯一的 async def 路由：需要 await UploadFile.read()。
读完立刻落盘登记任务并返回，切片向量化交给后台线程（app/rag/uploads.py），
避免长 PDF 把请求拖到超时。
"""
import logging

from fastapi import BackgroundTasks, File, Form, UploadFile
from pydantic import BaseModel, Field

from app.api import ok, router
from app.api.deps import AdminUser
from app.business import auth
from app.rag import service as rag, uploads

log = logging.getLogger(__name__)


class JobIdIn(BaseModel):
    job_id: int = Field(ge=1)


class DocIdIn(BaseModel):
    doc_id: int = Field(ge=1)


class VisibilityIn(BaseModel):
    doc_id: int = Field(ge=1)
    visible: bool = True


class NewUserIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=auth.MIN_PASSWORD_LEN, max_length=256)
    role: str = Field(default="student", pattern="^(admin|student)$")
    display_name: str | None = Field(default=None, max_length=64)


class UserIdIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class SetRoleIn(UserIdIn):
    role: str = Field(pattern="^(admin|student)$")


class SetActiveIn(UserIdIn):
    active: bool


class ResetPasswordIn(UserIdIn):
    new_password: str = Field(min_length=auth.MIN_PASSWORD_LEN, max_length=256)


# ------------------------------------------------------------------ 上传教材
@router.post("/admin/corpus/upload")
async def api_admin_upload(admin: AdminUser, background: BackgroundTasks,
                           file: UploadFile = File(...),
                           subject: str = Form(default="")):
    """上传一个教材文件（md/txt/pdf/docx），立即返回任务号，后台处理。

    subject 可选，填了就作为科目归属提示（如"会计"），
    不填则由 ingest.guess_subject 从文件名/文件头/目录名推断。
    """
    data = await file.read()
    job = uploads.accept_upload(file.filename or "upload", data,
                                uploaded_by=admin["id"],
                                subject_hint=(subject or "").strip() or None)
    background.add_task(uploads.process_job, job["id"])
    return ok({"job": job,
               "hint": "已收到，正在后台切片与向量化，轮询任务状态即可。"})


@router.get("/admin/corpus/jobs")
def api_admin_jobs(admin: AdminUser, limit: int = 30, status: str = ""):
    return ok(uploads.list_jobs(limit=max(1, min(limit, 200)), status=status or None))


@router.get("/admin/corpus/jobs/{job_id}")
def api_admin_job(job_id: int, admin: AdminUser):
    job = uploads.get_job(job_id)
    if not job:
        raise ValueError("任务不存在：%s" % job_id)
    return ok(job)


@router.post("/admin/corpus/jobs/retry")
def api_admin_job_retry(body: JobIdIn, admin: AdminUser, background: BackgroundTasks):
    """重试失败的任务（原文件还在上传目录里，不需要重新上传）。"""
    if not uploads.get_job(body.job_id):
        raise ValueError("任务不存在：%s" % body.job_id)
    background.add_task(uploads.process_job, body.job_id)
    return ok({"job_id": body.job_id, "requeued": True})


# ------------------------------------------------------------------ 知识库管理
@router.get("/admin/corpus/docs")
def api_admin_docs(admin: AdminUser):
    return ok(uploads.list_docs())


@router.get("/admin/corpus/stats")
def api_admin_corpus_stats(admin: AdminUser):
    return ok({"storage": uploads.storage_stats(), "rag": rag.health()})


@router.post("/admin/corpus/visibility")
def api_admin_visibility(body: VisibilityIn, admin: AdminUser):
    """上下架：下架的教材不参与检索，但切片与向量保留，可随时恢复。"""
    return ok(uploads.set_visibility(body.doc_id, body.visible))


@router.post("/admin/corpus/delete")
def api_admin_delete_doc(body: DocIdIn, admin: AdminUser):
    """彻底删除教材（切片与向量级联删除，上传的原文件一并删掉）。"""
    return ok(uploads.delete_doc(body.doc_id))


@router.post("/admin/corpus/reindex")
def api_admin_reindex(admin: AdminUser, force: bool = False):
    """重扫 data/corpus 目录入库。只扫服务端固定目录，不接受调用方指定路径。"""
    res = rag.reindex(force=force)
    res.pop("details", None)
    return ok(res)


# ------------------------------------------------------------------ 账号管理
@router.get("/admin/users")
def api_admin_users(admin: AdminUser, limit: int = 200, offset: int = 0):
    return ok({"items": auth.list_users(limit=max(1, min(limit, 500)),
                                        offset=max(0, offset)),
               "total": auth.count_users()})


@router.post("/admin/users/create")
def api_admin_create_user(body: NewUserIn, admin: AdminUser):
    return ok(auth.create_user(body.username, body.password, role=body.role,
                               display_name=body.display_name))


@router.post("/admin/users/role")
def api_admin_set_role(body: SetRoleIn, admin: AdminUser):
    return ok(auth.set_role(body.user_id, body.role))


@router.post("/admin/users/active")
def api_admin_set_active(body: SetActiveIn, admin: AdminUser):
    return ok(auth.set_active(body.user_id, body.active))


@router.post("/admin/users/password")
def api_admin_reset_password(body: ResetPasswordIn, admin: AdminUser):
    """管理员重置他人密码，不需要原密码。"""
    if not auth.get_user(body.user_id):
        raise ValueError("用户不存在")
    auth.set_password(body.user_id, body.new_password)
    return ok({"user_id": body.user_id, "reset": True})


@router.post("/admin/users/delete")
def api_admin_delete_user(body: UserIdIn, admin: AdminUser):
    """删除账号并清空其学习数据。不允许删掉自己，避免把自己锁在外面。"""
    if body.user_id == admin["id"]:
        raise ValueError("不能删除当前登录的账号")
    return ok(auth.delete_user(body.user_id))
