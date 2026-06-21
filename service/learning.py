"""
Learning capabilities router — real, deliverable features behind the Spinner cards:
  POST /api/v1/companion/pronounce    -> pronunciation score (ESL / Speech-Practice)
  POST /api/v1/companion/scholarships -> real scholarship matches (Scholarship Finder)

Both are pure-Python, deterministic, no paid API. They make the card claims true:
"score" on the Live-Translate/ESL card = a real pronunciation score; "find matches /
check eligibility / discover scholarships you qualify for" = real eligibility matching.
"""
from __future__ import annotations
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

import pronunciation
import scholarships

router = APIRouter(prefix="/api/v1/companion", tags=["learning"])


class PronounceReq(BaseModel):
    target: str          # the phrase the learner meant to say
    heard: str           # the STT transcript of what they said


@router.post("/pronounce")
def pronounce(req: PronounceReq):
    """Score how clearly the learner pronounced the target phrase (0-100 + per-word)."""
    return pronunciation.score_pronunciation(req.target or "", req.heard or "")


class ScholarshipReq(BaseModel):
    level: str = "any"            # hs_senior | undergrad | transfer | grad | any
    field: str = "any"            # any | stem | cs | engineering | business | humanities | health | arts
    demographics: List[str] = []  # e.g. ["hispanic","first_gen","low_income","women","black","native","lgbtq"]
    citizenship: str = "any"      # us | us_or_resident | any
    need: bool = False
    gpa: float = 0.0
    interests: str = ""
    limit: int = 6


@router.post("/scholarships")
def scholarships_match(req: ScholarshipReq):
    """Match the learner's profile to real scholarships they qualify for, ranked by fit."""
    profile = {
        "level": req.level, "field": req.field, "demographics": req.demographics,
        "citizenship": req.citizenship, "need": req.need, "gpa": req.gpa,
        "interests": req.interests,
    }
    return scholarships.match(profile, limit=max(1, min(20, req.limit)))
