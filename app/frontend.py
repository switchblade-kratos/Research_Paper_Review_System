"""HTML pages (Jinja2 templates). Pages call the same db/search/report modules as the API."""
from pathlib import Path
from urllib.parse import urlencode

import psycopg
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import auth, db, reports, search

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def render(request: Request, name: str, **ctx):
    ctx.update(user=auth.current_user(request), msg=request.query_params.get("msg"),
               err=request.query_params.get("err"))
    return templates.TemplateResponse(request, name, ctx)


def go(url: str, **params):
    query = urlencode({k: v for k, v in params.items() if v})
    return RedirectResponse(url + ("?" + query if query else ""), status_code=303)


def need(request: Request, *roles):
    """Returns the user, or None if they must be sent to the login page."""
    user = auth.current_user(request)
    if user is None or (roles and user["role"] not in roles):
        return None
    return user


@router.get("/")
def home(request: Request):
    return go("/dashboard" if auth.current_user(request) else "/login")


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    if auth.login(request, email, password) is None:
        return go("/login", err="Wrong email or password")
    return go("/dashboard")


@router.get("/register")
def register_page(request: Request):
    return render(request, "register.html")


@router.post("/register")
def register_submit(full_name: str = Form(...), email: str = Form(...), password: str = Form(...)):
    try:
        auth.register_author(full_name, email, password)
    except ValueError as err:
        return go("/register", err=str(err))
    except psycopg.Error as err:
        return go("/register", err=db.friendly_error(err))
    return go("/login", msg="Account created, please log in")


@router.get("/logout")
def logout(request: Request):
    auth.logout(request)
    return go("/login")


@router.get("/dashboard")
def dashboard(request: Request):
    user = need(request)
    if not user:
        return go("/login")
    f = dict(request.query_params)
    try:
        papers, has_next = db.list_papers(user, f)
    except (psycopg.Error, ValueError) as err:
        papers, has_next = [], False
        f["error"] = db.friendly_error(err) if isinstance(err, psycopg.Error) else "Invalid filter value"
    page = max(int(f.get("page") or 1), 1) if str(f.get("page", "1")).isdigit() else 1
    base = {k: v for k, v in f.items() if k not in ("page", "error") and v}
    return render(request, "dashboard.html", papers=papers, f=f, page=page, has_next=has_next,
                  base_qs=urlencode(base), domains=db.list_domains(),
                  statuses=db.STATUSES, sorts=list(db.SORTS))


@router.get("/papers/{paper_id}")
def paper_page(request: Request, paper_id: int):
    user = need(request)
    if not user:
        return go("/login")
    paper = db.get_paper(user, paper_id)
    if paper is None:
        return go("/dashboard", err="Paper not found or not visible to you")
    mine = next((r for r in paper["reviews"] if r["reviewer_id"] == user["user_id"]), None)
    return render(request, "paper.html", paper=paper, my_review=mine, statuses=db.STATUSES)


@router.post("/papers/{paper_id}/review")
def review_submit(request: Request, paper_id: int, score: int = Form(...), comments: str = Form(...)):
    user = need(request, "reviewer")
    if not user:
        return go("/login")
    try:
        db.submit_review(user, paper_id, score, comments)
    except psycopg.Error as err:
        return go(f"/papers/{paper_id}", err=db.friendly_error(err))
    return go(f"/papers/{paper_id}", msg="Review saved")


@router.post("/papers/{paper_id}/status")
def status_submit(request: Request, paper_id: int, status: str = Form(...)):
    user = need(request, "admin")
    if not user:
        return go("/login")
    try:
        db.set_status(user, paper_id, status)
    except psycopg.Error as err:
        return go(f"/papers/{paper_id}", err=db.friendly_error(err))
    return go(f"/papers/{paper_id}", msg="Status updated")


@router.get("/search")
def search_page(request: Request, q: str = ""):
    user = need(request)
    if not user:
        return go("/login")
    results, error = [], None
    if q.strip():
        try:
            results = search.search(user, q.strip())
        except Exception as err:   # model download / DB issues are shown on the page
            error = f"Search failed: {err}"
    return render(request, "search.html", q=q, results=results, search_error=error)


@router.get("/submit")
def submit_page(request: Request):
    if not need(request, "author"):
        return go("/login")
    return render(request, "submit.html", domains=db.list_domains())


@router.post("/submit")
def submit_paper(request: Request, title: str = Form(...), abstract: str = Form(...),
                 domain_id: int = Form(...), coauthors: str = Form("")):
    user = need(request, "author")
    if not user:
        return go("/login")
    emails = [e.strip() for e in coauthors.split(",") if e.strip()]
    try:
        vec = search.embed_document(title + "\n" + abstract)
    except Exception:          # no model available: save the paper without an embedding
        vec = None
    try:
        pid = db.submit_paper(user, title, abstract, domain_id, emails, vec)
    except psycopg.Error as err:
        return go("/submit", err=db.friendly_error(err))
    return go(f"/papers/{pid}", msg="Paper submitted")


@router.get("/admin")
def admin_page(request: Request, min_reviews: int = 1):
    user = need(request, "admin")
    if not user:
        return go("/login")
    data = reports.admin_reports(user, min_reviews)
    return render(request, "admin.html", r=data, min_reviews=min_reviews, domains=db.list_domains())


@router.post("/admin/users")
def admin_create_user(request: Request, full_name: str = Form(...), email: str = Form(...),
                      password: str = Form(...), role: str = Form("reviewer"),
                      domain_id: str = Form("")):
    user = need(request, "admin")
    if not user:
        return go("/login")
    try:
        db.create_user(full_name, email, auth.hash_password(password), role,
                       int(domain_id) if domain_id else None, user)
    except psycopg.Error as err:
        return go("/admin", err=db.friendly_error(err))
    return go("/admin", msg=f"Created {role} {email}")
