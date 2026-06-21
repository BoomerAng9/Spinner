"""
Real scholarship matcher — curated dataset of major, verifiable scholarships +
an eligibility-matching engine. Pure Python, no external deps.

The card promises "Find matches · Check eligibility · Discover scholarships you
qualify for." This delivers it for real: the learner's spoken profile (level,
field, background, citizenship, need, GPA, interests) is matched against real
scholarships' STABLE eligibility, ranked, with an eligibility explanation and the
OFFICIAL link. Volatile details (exact deadlines/amounts change yearly) are not
fabricated — we link to the official page for current specifics and expose a
live-search query for the long tail.

Honest by design: every entry is a real program with a real URL; we match on
durable eligibility, and tell the user to confirm current deadlines at the source.
"""
from __future__ import annotations
import re

# level: hs_senior | undergrad | transfer | grad | any
# field: any | stem | cs | engineering | business | humanities | health | arts
# demo : tags the scholarship REQUIRES (any-of). [] = open to all.
# cit  : us | us_or_resident | any   (us_or_resident includes permanent residents/DACA where the program allows)
SCHOLARSHIPS = [
 dict(name="Coca-Cola Scholars", amount="$20,000", level="hs_senior", field="any",
      demo=[], cit="us_or_resident", need=False, gpa=3.0,
      url="https://www.coca-colascholarsfoundation.org/apply/", note="HS seniors, leadership/service. Deadline ~late Sept—Oct."),
 dict(name="The Gates Scholarship", amount="Full cost of attendance", level="hs_senior", field="any",
      demo=["minority","black","hispanic","native","asian_pacific"], cit="us_or_resident", need=True, gpa=3.3,
      url="https://www.thegatesscholarship.org/scholarship", note="Pell-eligible minority HS seniors. Deadline ~mid-Sept."),
 dict(name="Jack Kent Cooke College Scholarship", amount="Up to $55,000/yr", level="hs_senior", field="any",
      demo=[], cit="any", need=True, gpa=3.5,
      url="https://www.jkcf.org/our-scholarships/college-scholarship-program/", note="High-achieving, financial need. Deadline ~Nov."),
 dict(name="Jack Kent Cooke Undergraduate Transfer", amount="Up to $55,000/yr", level="transfer", field="any",
      demo=[], cit="any", need=True, gpa=3.5,
      url="https://www.jkcf.org/our-scholarships/undergraduate-transfer-scholarship/", note="Community-college transfer to 4-yr. Deadline ~Jan."),
 dict(name="Dell Scholars", amount="$20,000", level="hs_senior", field="any",
      demo=[], cit="us_or_resident", need=True, gpa=2.4,
      url="https://www.dellscholars.org/", note="Grit + need; college-readiness program participants. Deadline ~Dec."),
 dict(name="Hispanic Scholarship Fund", amount="$500–$5,000", level="undergrad", field="any",
      demo=["hispanic"], cit="us_or_resident", need=True, gpa=3.0,
      url="https://www.hsf.net/scholarship", note="Hispanic heritage. Deadline ~Feb."),
 dict(name="QuestBridge National College Match", amount="Full 4-yr scholarship", level="hs_senior", field="any",
      demo=["first_gen","low_income"], cit="any", need=True, gpa=3.5,
      url="https://www.questbridge.org/", note="Low-income high-achievers matched to partner colleges. Deadline ~late Sept."),
 dict(name="Horatio Alger National Scholarship", amount="$25,000", level="hs_senior", field="any",
      demo=["adversity","low_income"], cit="us_or_resident", need=True, gpa=2.0,
      url="https://scholars.horatioalger.org/", note="Overcome adversity + need. Deadline ~Oct."),
 dict(name="Ron Brown Scholar Program", amount="$40,000", level="hs_senior", field="any",
      demo=["black"], cit="us_or_resident", need=True, gpa=3.0,
      url="https://www.ronbrown.org/", note="Black HS seniors, leadership/service. Deadline ~Nov/Jan."),
 dict(name="UNCF (general scholarships)", amount="Varies", level="undergrad", field="any",
      demo=["black","minority"], cit="us_or_resident", need=True, gpa=2.5,
      url="https://uncf.org/scholarships", note="Many funds for students of color. Rolling deadlines."),
 dict(name="Society of Women Engineers (SWE)", amount="$1,000–$15,000", level="undergrad", field="engineering",
      demo=["women"], cit="any", need=False, gpa=3.0,
      url="https://swe.org/scholarships/", note="Women in engineering/CS. Deadline ~Feb (sophomore+), ~May (incoming)."),
 dict(name="Generation Google Scholarship", amount="$10,000", level="undergrad", field="cs",
      demo=["minority","women","disability"], cit="any", need=False, gpa=3.0,
      url="https://www.google.com/about/careers/students/generation-google-scholarship/", note="CS/related, underrepresented groups. Deadline ~Dec."),
 dict(name="SMART Scholarship (DoD)", amount="Full tuition + stipend", level="undergrad", field="stem",
      demo=[], cit="us", need=False, gpa=3.0,
      url="https://www.smartscholarship.org/smart", note="STEM + post-grad DoD service commitment. Deadline ~Dec."),
 dict(name="AISES Scholarships", amount="Varies", level="undergrad", field="stem",
      demo=["native"], cit="any", need=False, gpa=3.0,
      url="https://www.aises.org/scholarships", note="Native American STEM students. Deadlines vary."),
 dict(name="Point Foundation Scholarship", amount="Varies", level="undergrad", field="any",
      demo=["lgbtq"], cit="any", need=True, gpa=3.0,
      url="https://pointfoundation.org/", note="LGBTQ students. Deadline ~Jan."),
 dict(name="Cameron Impact Scholarship", amount="Full tuition", level="hs_senior", field="any",
      demo=[], cit="us", need=False, gpa=3.7,
      url="https://www.bryancameroneducationfoundation.org/", note="HS seniors, academics+leadership, non-need. Deadline ~Aug/Sept."),
 dict(name="Elks Most Valuable Student", amount="Up to $50,000", level="hs_senior", field="any",
      demo=[], cit="us", need=True, gpa=2.5,
      url="https://www.elks.org/scholars/scholarships/mvs.cfm", note="Merit + need + leadership. Deadline ~Nov."),
 dict(name="Burger King Scholars", amount="$1,000–$60,000", level="hs_senior", field="any",
      demo=[], cit="us_or_resident", need=True, gpa=2.5,
      url="https://bkmclamorefoundation.org/who-we-are/our-programs/bk-scholars", note="HS seniors / employees. Deadline ~Dec."),
 dict(name="Davidson Fellows", amount="$10,000–$50,000", level="hs_senior", field="stem",
      demo=[], cit="us_or_resident", need=False, gpa=0.0,
      url="https://www.davidsongifted.org/fellows-scholarship/", note="Significant STEM/humanities project, under 18. Deadline ~Feb."),
 dict(name="Regeneron Science Talent Search", amount="Up to $250,000", level="hs_senior", field="stem",
      demo=[], cit="us_or_resident", need=False, gpa=0.0,
      url="https://www.societyforscience.org/regeneron-sts/", note="Original STEM research. Deadline ~Nov."),
]

# field families so 'cs'/'engineering' still match 'stem'-scoped awards
_STEM = {"stem","cs","engineering","health"}

def _toks(s): return set(re.findall(r"[a-z]+", (s or "").lower()))

def _field_ok(req, prof):
    if req == "any": return True
    if prof == req: return True
    if req == "stem" and prof in _STEM: return True
    return False

def _level_ok(req, prof):
    return req == "any" or req == prof or (req == "undergrad" and prof in {"transfer","undergrad"})

def _cit_ok(req, prof):
    if req == "any": return True
    if req == "us_or_resident": return prof in {"us","resident","us_or_resident"}
    return prof == req

def match(profile: dict, limit: int = 6) -> dict:
    """profile = {level, field, demographics:[...], citizenship, need:bool, gpa:float, interests:str}
       Returns {matches:[{...,why,fit}], search_query}."""
    level = (profile.get("level") or "any").lower()
    field = (profile.get("field") or "any").lower()
    demos = set(d.lower() for d in (profile.get("demographics") or []))
    cit   = (profile.get("citizenship") or "any").lower()
    need  = bool(profile.get("need"))
    gpa   = float(profile.get("gpa") or 0.0)
    interest_toks = _toks(profile.get("interests"))

    out = []
    for s in SCHOLARSHIPS:
        # hard eligibility gates
        if not _level_ok(s["level"], level):       continue
        if not _field_ok(s["field"], field):       continue
        if not _cit_ok(s["cit"], cit):              continue
        if s["demo"] and not (demos & set(s["demo"])):  continue
        if s["gpa"] and gpa and gpa + 1e-9 < s["gpa"]:  continue
        # soft fit score
        fit, why = 50, []
        if s["need"] and need: fit += 20; why.append("matches your financial-need profile")
        if s["demo"] and (demos & set(s["demo"])): fit += 20; why.append("eligible by your background")
        if _field_ok(s["field"], field) and s["field"] != "any": fit += 15; why.append(f"fits your {field} field")
        if s["gpa"] and gpa >= s["gpa"]: fit += 10; why.append(f"you meet the {s['gpa']:.1f} GPA min")
        if s["level"] == level and level != "any": fit += 10
        if interest_toks & _toks(s["name"] + " " + s["note"]): fit += 10; why.append("relevant to your interests")
        out.append({**{k: s[k] for k in ("name","amount","url","note")},
                    "fit": min(100, fit), "why": "; ".join(why) or "meets the core eligibility"})

    out.sort(key=lambda x: -x["fit"])
    q = f"{field if field!='any' else ''} scholarships for {level.replace('_',' ')} {' '.join(demos)} {profile.get('interests','')}".strip()
    return {"matches": out[:limit], "count": len(out),
            "search_query": re.sub(r"\s+", " ", q),
            "disclaimer": "Real programs; confirm current deadlines/amounts on each official page. Not financial advice."}


if __name__ == "__main__":
    import json
    tests = [
        {"label":"First-gen Hispanic CS HS senior, need, 3.6",
         "profile":{"level":"hs_senior","field":"cs","demographics":["hispanic","first_gen","low_income"],
                    "citizenship":"us_or_resident","need":True,"gpa":3.6,"interests":"coding robotics"}},
        {"label":"Woman engineering undergrad, 3.2, no specific need",
         "profile":{"level":"undergrad","field":"engineering","demographics":["women"],
                    "citizenship":"any","need":False,"gpa":3.2,"interests":""}},
        {"label":"Community-college transfer, business, need, 3.7",
         "profile":{"level":"transfer","field":"business","demographics":[],
                    "citizenship":"any","need":True,"gpa":3.7,"interests":"entrepreneurship"}},
    ]
    for t in tests:
        r = match(t["profile"])
        print(f"\n=== {t['label']}  ->  {r['count']} eligible (top {len(r['matches'])}) ===")
        for m in r["matches"]:
            print(f"  [{m['fit']:>3}] {m['name']:38} {m['amount']:24} — {m['why']}")
        print(f"  live-search: {r['search_query']}")
