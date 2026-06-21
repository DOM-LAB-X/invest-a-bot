from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.profiles import crud
from app.profiles.schemas import ProfileCreate, ProfileRuleCreate

router = APIRouter(prefix="/dashboard/profiles", tags=["dashboard"])
templates = Jinja2Templates(directory="app/web/templates")


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


@router.get("", response_class=HTMLResponse)
def profiles_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    profiles = crud.list_profiles(db)
    return templates.TemplateResponse(
        request,
        "profiles/index.html",
        {"active_nav": "profiles", "profiles": profiles},
    )


@router.post("", response_class=RedirectResponse)
def create_profile_form(
    name: str = Form(...),
    description: str = Form(""),
    filer_names: str = Form(""),
    tickers: str = Form(""),
    transaction_types: str = Form(""),
    min_amount: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    rule = ProfileRuleCreate(
        filer_names=_split_csv(filer_names),
        tickers=[t.upper() for t in (_split_csv(tickers) or [])] or None,
        transaction_types=_split_csv(transaction_types),
        min_amount=min_amount or None,
    )
    data = ProfileCreate(name=name, description=description or None, rules=[rule])
    crud.create_profile(db, data)
    db.commit()
    return RedirectResponse(url="/dashboard/profiles", status_code=303)


@router.post("/{profile_id}/activate", response_class=RedirectResponse)
def activate(profile_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    crud.set_profile_active(db, profile_id, True)
    db.commit()
    return RedirectResponse(url="/dashboard/profiles", status_code=303)


@router.post("/{profile_id}/deactivate", response_class=RedirectResponse)
def deactivate(profile_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    crud.set_profile_active(db, profile_id, False)
    db.commit()
    return RedirectResponse(url="/dashboard/profiles", status_code=303)


@router.post("/{profile_id}/delete", response_class=RedirectResponse)
def delete(profile_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    crud.remove_profile(db, profile_id)
    db.commit()
    return RedirectResponse(url="/dashboard/profiles", status_code=303)
