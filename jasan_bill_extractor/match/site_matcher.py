"""
match/site_matcher.py
---------------------------------
SiteMatcher (spec.md §3, §5.4): 고정 식별자(팩스 발신번호 > 납부자번호) 우선 매칭,
실패 시 상호명(site_hint_text) fuzzy 매칭으로 보조한다.

낮은 신뢰도의 fuzzy 매칭은 사업장번호를 단정하지 않고 후보만 제시한다 (review.exception_queue로
넘기기 위함 - spec.md §1.5, Phase 0 실습에서 확인된 원칙: 애매하면 틀리게 채우기보다 사람에게 넘긴다).
"""

import re
import difflib
from dataclasses import dataclass, field
from typing import Optional

from .site_master import SiteMaster

FAX_PATTERN = re.compile(r"^(\d{7,})_(\d{14})\.(tif|tiff)$", re.IGNORECASE)

_STRIP_SUFFIXES = ["아파트", "오피스텔", "프라자"]


def _normalize(s: str) -> str:
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", s)  # 지역태그/사업자번호 괄호 제거
    s = re.sub(r"[_\s\-·,\.]", "", s)
    for suf in _STRIP_SUFFIXES:
        s = s.replace(suf, "")
    return s.strip()


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


@dataclass
class MatchResult:
    matched: bool
    site_id: Optional[str] = None
    site_name: Optional[str] = None
    method: str = ""  # "fax_exact" | "payer_id_exact" | "fuzzy_name" | "unmatched"
    confidence: float = 0.0
    candidates: list = field(default_factory=list)  # fuzzy 매칭 시 상위 후보 (site_id, site_name, score)


def extract_fax_number(filename: str) -> Optional[str]:
    m = FAX_PATTERN.match(filename)
    return m.group(1) if m else None


class SiteMatcher:
    def __init__(self, site_master: SiteMaster, min_confidence: float = 0.90):
        self.site_master = site_master
        self.min_confidence = min_confidence

    def match(
        self,
        filename: str,
        payer_id: Optional[str] = None,
        site_hint_text: Optional[str] = None,
    ) -> MatchResult:
        fax = extract_fax_number(filename)
        if fax and fax in self.site_master.excluded_fax:
            return MatchResult(matched=False, method="제외(관할아님/비고지서)")

        if fax and fax in self.site_master.by_fax:
            rec = self.site_master.by_fax[fax]
            if rec.site_id:
                return MatchResult(
                    matched=True, site_id=rec.site_id, site_name=rec.site_name,
                    method="fax_exact", confidence=1.0,
                )

        if payer_id and payer_id in self.site_master.by_payer_id:
            rec = self.site_master.by_payer_id[payer_id]
            if rec.site_id:
                return MatchResult(
                    matched=True, site_id=rec.site_id, site_name=rec.site_name,
                    method="payer_id_exact", confidence=1.0,
                )

        if site_hint_text:
            return self._fuzzy_match(site_hint_text)

        return MatchResult(matched=False, method="unmatched")

    def _fuzzy_match(self, site_hint_text: str) -> MatchResult:
        target = _normalize(site_hint_text)
        scored = []
        for rec in self.site_master.all_known_sites:
            candidates_text = [rec.site_name] + rec.alt_names
            best = 0.0
            for ct in candidates_text:
                if not ct:
                    continue
                s = _similarity(target, _normalize(ct))
                if s > best:
                    best = s
            scored.append((best, rec))
        scored.sort(key=lambda x: -x[0])
        top = scored[:3]

        candidates = [(rec.site_id, rec.site_name, round(score, 2)) for score, rec in top]
        if top and top[0][0] >= self.min_confidence:
            score, rec = top[0]
            return MatchResult(
                matched=True, site_id=rec.site_id, site_name=rec.site_name,
                method="fuzzy_name", confidence=score, candidates=candidates,
            )
        return MatchResult(matched=False, method="fuzzy_name_low_confidence", candidates=candidates)
