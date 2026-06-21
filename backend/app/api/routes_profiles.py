from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.profiles import crud
from app.profiles.schemas import ProfileCreate, ProfileRead, ProfileRuleCreate, ProfileRuleRead

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("", response_model=list[ProfileRead])
def list_profiles(active_only: bool = False, db: Session = Depends(get_db)) -> list[ProfileRead]:
    return list(crud.list_profiles(db, active_only=active_only))


@router.post("", response_model=ProfileRead, status_code=201)
def create_profile(data: ProfileCreate, db: Session = Depends(get_db)) -> ProfileRead:
    profile = crud.create_profile(db, data)
    db.commit()
    return profile


@router.get("/{profile_id}", response_model=ProfileRead)
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> ProfileRead:
    profile = crud.get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.delete("/{profile_id}", status_code=204)
def remove_profile(profile_id: int, db: Session = Depends(get_db)) -> None:
    removed = crud.remove_profile(db, profile_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.commit()


@router.post("/{profile_id}/deactivate", response_model=ProfileRead)
def deactivate_profile(profile_id: int, db: Session = Depends(get_db)) -> ProfileRead:
    profile = crud.set_profile_active(db, profile_id, False)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.commit()
    return profile


@router.post("/{profile_id}/activate", response_model=ProfileRead)
def activate_profile(profile_id: int, db: Session = Depends(get_db)) -> ProfileRead:
    profile = crud.set_profile_active(db, profile_id, True)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.commit()
    return profile


@router.post("/{profile_id}/rules", response_model=ProfileRuleRead, status_code=201)
def add_rule(profile_id: int, data: ProfileRuleCreate, db: Session = Depends(get_db)) -> ProfileRuleRead:
    rule = crud.add_rule(db, profile_id, data)
    if rule is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.commit()
    return rule
