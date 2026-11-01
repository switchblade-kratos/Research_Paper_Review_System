"""JSON API (FastAPI). Interactive docs at /docs once the server is running."""
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import auth, db, reports, search

router = APIRouter(prefix="/api", tags=["api"])


class LoginIn(BaseModel):
    email: str
    password: str


class RegisterIn(BaseModel):
    full_name: str
    email: str
    password: str


class PaperIn(BaseModel):
    title: str
    abstract: str
    domain_id: int
    coauthor_emails: list[str] = []


class ReviewIn(BaseModel):
    score: int = Field(ge=1, le=10)
    comments: str


class StatusIn(BaseModel):
    status: str


class UserIn(BaseModel):
    full_name: str
    email: str
    password: str
    role: str = "reviewer"
    domain_id: Optional[int] = None


def _run(fn, *args):
    """Run a DB call and turn database errors into HTTP 400 with a readable message."""
    try:
        return fn(*args)
    except psycopg.Error as err:
        raise HTTPException(status_code=400, detail=db.friendly_error(err))


@router.post("/login")
def api_login(body: LoginIn, request: Request):
    user = auth.login(request, body.email, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong email or password")
    return user


@router.post("/register")
def api_register(body: RegisterIn):
    try:
        return {"user_id": _run(auth.register_author, body.full_name, body.email, body.password)}
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))


@router.post("/logout")
def api_logout(request: Request):
    auth.logout(request)
    return {"ok": True}


@router.get("/me")
def api_me(user=Depends(auth.require())):
    return user


@router.get("/domains")
def api_domains():
    return db.list_domains()


@router.get("/papers")
def api_papers(status: str = "", domain_id: str = "", year_from: str = "", year_to: str = "",
               keyword: str = "", min_score: str = "", sort: str = "newest", page: int = 1,
               user=Depends(auth.require())):
    filters = dict(status=status, domain_id=domain_id, year_from=year_from, year_to=year_to,
                   keyword=keyword, min_score=min_score, sort=sort, page=page)
    rows, has_next = _run(db.list_papers, user, filters)
    return {"page": page, "has_next": has_next, "papers": rows}


@router.get("/papers/{paper_id}")
def api_paper(paper_id: int, user=Depends(auth.require())):
    paper = _run(db.get_paper, user, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found or not visible to you")
    return paper


@router.post("/papers")
def api_submit(body: PaperIn, user=Depends(auth.require("author"))):
    try:
        vec = search.embed_document(body.title + "\n" + body.abstract)
    except Exception:          # no model available: save the paper without an embedding
        vec = None
    pid = _run(db.submit_paper, user, body.title, body.abstract, body.domain_id,
               body.coauthor_emails, vec)
    return {"paper_id": pid}


@router.post("/papers/{paper_id}/reviews")
def api_review(paper_id: int, body: ReviewIn, user=Depends(auth.require("reviewer"))):
    _run(db.submit_review, user, paper_id, body.score, body.comments)
    return {"ok": True}


@router.patch("/papers/{paper_id}/status")
def api_status(paper_id: int, body: StatusIn, user=Depends(auth.require("admin"))):
    if _run(db.set_status, user, paper_id, body.status) == 0:
        raise HTTPException(status_code=404, detail="Paper not found")
    return {"ok": True}


@router.get("/search")
def api_search(q: str, limit: int = 10, user=Depends(auth.require())):
    return _run(search.search, user, q, min(limit, 50))


@router.get("/reports")
def api_reports(min_reviews: int = 1, user=Depends(auth.require("admin"))):
    return _run(reports.admin_reports, user, min_reviews)


@router.post("/users")
def api_create_user(body: UserIn, user=Depends(auth.require("admin"))):
    uid = _run(db.create_user, body.full_name, body.email, auth.hash_password(body.password),
               body.role, body.domain_id, user)
    return {"user_id": uid}
