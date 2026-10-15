"""Presidio analyzer for FinanceBrain: finance identifiers, contact data, sensitive HR data, and client names.

Detection layers (all feed one Presidio AnalyzerEngine, so context-word enhancement and scoring are uniform):
  * ChecksumRecognizer: SSN, card (Luhn), IBAN (mod-97), ABA routing (checksum + context), US phone. These reuse
    the exact validators of the outbound guard, so the guard and the redactor agree on what is raw PII.
  * Pattern recognizers: account numbers, EIN, device fingerprints, SWIFT/BIC, email, street addresses, dates of
    birth (old years, or any date near "DOB"/"born"), salaries (near "salary"/"compensation"), medical leave.
  * Names: a deny-list built from the tenant's CRM client records, plus spaCy PERSON entities that are not
    employees (employee names are business-directory data and are kept by policy).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer import RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import SpacyRecognizer

from backend.config import get_settings
from backend.llm.guard import find_pii

log = logging.getLogger(__name__)

# Canonical PII types (identical to the span types in data/raw/<tenant>/pii_spans.jsonl).
PII_TYPES = ("CLIENT_NAME", "PHONE", "EMAIL", "ADDRESS", "SSN", "DOB", "EIN", "DEVICE_ID", "ACCOUNT_NUMBER",
             "ROUTING_NUMBER", "CARD_NUMBER", "IBAN", "SWIFT", "SALARY", "MEDICAL")

_CHECKSUM_SCORES = {"SSN": 0.9, "CARD_NUMBER": 0.95, "IBAN": 0.95, "ROUTING_NUMBER": 0.85, "PHONE": 0.8}
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_OLD_YEAR = r"(?:19\d{2}|200[0-4])"
_ANY_YEAR = r"(?:19|20)\d{2}"
_SWIFT_CC = ("US|GB|DE|NL|CY|AE|FR|CH|SG|HK|KY|VG|PA|LU|IE|JP|CA|MX|BS|MT|LI|BE|ES|IT|AT|SE|NO|DK|PL|PT|GR|TR|"
             "IL|IN|CN|KR|AU|NZ|ZA|BR|AR|CL|CO|PE|BM|JE|GG|IM|GI|MC|SC|MU|BH|QA|SA|KW|OM|EE|LV|LT|CZ|HU|RO|BG")


class ChecksumRecognizer(EntityRecognizer):
    """High-confidence identifiers validated by checksum (shared with backend.llm.guard)."""

    def __init__(self) -> None:
        super().__init__(supported_entities=list(_CHECKSUM_SCORES), name="FinanceChecksumRecognizer",
                         supported_language="en")

    def load(self) -> None:  # nothing to load
        return None

    def analyze(self, text, entities, nlp_artifacts=None):  # type: ignore[override]
        return [RecognizerResult(f.type, f.start, f.end, _CHECKSUM_SCORES[f.type])
                for f in find_pii(text) if f.type in entities]


def _pr(entity: str, patterns: list[tuple[str, str, float]], context: list[str] | None = None,
        flags: int = re.MULTILINE) -> PatternRecognizer:
    return PatternRecognizer(supported_entity=entity, name=f"{entity}_Recognizer", supported_language="en",
                             patterns=[Pattern(n, r, s) for n, r, s in patterns], context=context,
                             global_regex_flags=flags)


def pattern_recognizers() -> list[PatternRecognizer]:
    ci = re.MULTILINE | re.IGNORECASE
    return [
        _pr("ACCOUNT_NUMBER", [("acct_10_12", r"(?<![\w.\-/])[1-9]\d{9,11}(?![\w\-])", 0.6)],
            ["account", "acct", "checking", "savings", "brokerage", "loan", "share", "ending"]),
        _pr("EIN", [("ein", r"(?<![\d-])\d{2}-\d{7}(?![\d-])", 0.55)], ["ein", "tax", "employer", "tin"]),
        _pr("DEVICE_ID", [("device", r"\bDEV-[0-9A-F]{12}\b", 0.95)]),
        _pr("SWIFT", [("swift_digit", rf"\b[A-Z]{{4}}(?:{_SWIFT_CC})(?=[A-Z0-9]{{0,4}}\d)[A-Z0-9]{{2}}(?:[A-Z0-9]{{3}})?\b", 0.75),
                      ("swift_branch_xxx", rf"\b[A-Z]{{4}}(?:{_SWIFT_CC})[A-Z0-9]{{2}}XXX\b", 0.65),
                      ("swift_alpha", rf"\b[A-Z]{{4}}(?:{_SWIFT_CC})[A-Z0-9]{{2}}(?:[A-Z0-9]{{3}})?\b", 0.3)],
            ["swift", "bic", "bank", "beneficiary", "correspondent", "wire"]),
        _pr("EMAIL", [("email", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", 0.9)]),
        _pr("ADDRESS", [("us_street_city_state_zip",
                         r"\b\d{1,6} [A-Z][A-Za-z'.]*(?: [A-Z][A-Za-z'.]*){0,4}(?: (?:Apt\.|Suite) \d{1,5})?, "
                         r"[A-Z][A-Za-z'. ]{1,30}?, [A-Z]{2} \d{5}\b", 0.85)]),
        _pr("DOB", [("iso_old", rf"\b{_OLD_YEAR}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b", 0.6),
                    ("us_old", rf"\b(?:0[1-9]|1[0-2])/(?:0[1-9]|[12]\d|3[01])/{_OLD_YEAR}\b", 0.6),
                    ("long_old", rf"\b(?:{_MONTHS}) (?:[1-9]|[12]\d|3[01]), {_OLD_YEAR}\b", 0.6),
                    ("iso_any", rf"\b{_ANY_YEAR}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b", 0.2),
                    ("us_any", rf"\b(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/{_ANY_YEAR}\b", 0.2),
                    ("long_any", rf"\b(?:{_MONTHS}) (?:[1-9]|[12]\d|3[01]), {_ANY_YEAR}\b", 0.2)],
            ["dob", "birth", "born", "birthday", "birthdate"]),
        _pr("SALARY", [("usd", r"\$\d{2,3},\d{3}(?:\.\d{2})?(?!\d|,\d)", 0.2)],
            ["salary", "compensation", "payroll", "comp"]),
        _pr("MEDICAL", [("leave", r"\b(?:(?:short|long)-term disability|intermittent FMLA|medical) leave "
                                  r"(?:for|following|after) (?:an? |the )?[a-z][a-z\- ]{2,40}?"
                                  r"(?=[.,;:!?)\n]| (?:and|starting|beginning|from|through|until|effective|on|while|"
                                  r"so|as|which|that|with|in|next|this)\b|$)", 0.7)], flags=ci),
    ]


_WORD = re.compile(r"[A-Za-z][A-Za-z'’.\-]*")
_POSSESSIVE = re.compile(r"['’]s?$")
_CONNECTORS = {"and", "or", "&", "de", "van", "von"}
_CHUNK = re.compile(r"[^,\n:;()/|]+")  # PERSON spans from spaCy can bleed across these separators
_SALARY_DOC = re.compile(r"\b(?:salary|compensation|merit increase|pay (?:raise|increase|adjustment)|base pay)\b",
                         re.IGNORECASE)


@lru_cache
def nlp_engine():
    model = get_settings().spacy_model
    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": model}],
        "ner_model_configuration": {"model_to_presidio_entity_mapping": {"PERSON": "PERSON", "PER": "PERSON"},
                                    "low_confidence_score_multiplier": 0.4, "low_score_entity_names": [],
                                    "labels_to_ignore": ["CARDINAL", "ORDINAL", "QUANTITY", "MONEY", "PERCENT",
                                                         "DATE", "TIME", "ORG", "GPE", "LOC", "NORP", "FAC",
                                                         "PRODUCT", "EVENT", "WORK_OF_ART", "LAW", "LANGUAGE"]},
    })
    log.info("loading spaCy model %s", model)
    return provider.create_engine()


@dataclass(frozen=True)
class Detection:
    type: str
    start: int
    end: int
    score: float
    recognizer: str


class PiiAnalyzer:
    """Tenant-specific analyzer (the client-name deny-list and employee directory differ per tenant)."""

    def __init__(self, tenant_domain: str, client_names: list[str], employee_names: list[str],
                 keep_terms: list[str] = ()):
        self.domain = tenant_domain.lower()
        emp = {n.casefold() for n in employee_names}
        self.employee_full = emp
        self.employee_parts = {p for n in emp for p in n.split()}
        self.keep_terms = {t.casefold() for t in keep_terms}
        self.keep_words = {w for t in self.keep_terms for w in re.findall(r"[a-z0-9]{3,}", t)}
        names = sorted({n for n in client_names if n and n.casefold() not in emp}, key=len, reverse=True)
        registry = RecognizerRegistry(supported_languages=["en"])
        registry.add_recognizer(ChecksumRecognizer())
        for r in pattern_recognizers():
            registry.add_recognizer(r)
        if names:
            registry.add_recognizer(PatternRecognizer(
                supported_entity="CLIENT_NAME", name="ClientNameDenyList", supported_language="en",
                deny_list=names, deny_list_score=0.95, global_regex_flags=re.MULTILINE | re.IGNORECASE))
        registry.add_recognizer(SpacyRecognizer(supported_language="en", supported_entities=["PERSON"]))
        self.engine = AnalyzerEngine(registry=registry, nlp_engine=nlp_engine(), supported_languages=["en"],
                                     default_score_threshold=0.5)
        self.entities = [*PII_TYPES, "PERSON"]

    def _keep_person(self, span: str) -> bool:
        """True if a spaCy PERSON span should be redacted (i.e. it is plausibly a non-employee person)."""
        if any(ch.isdigit() for ch in span) or "@" in span:
            return False
        # words without possessives; initials ("S.") and connectors ("and") carry no identity on their own
        words = [_POSSESSIVE.sub("", w) for w in _WORD.findall(span)]
        words = [w for w in words if len(w.rstrip(".")) > 1 and w.casefold() not in _CONNECTORS]
        if len(words) < 2 or not all(w[:1].isupper() for w in words):
            return False
        low = " ".join(w.casefold() for w in words)
        if low in self.employee_full or low in self.keep_terms:
            return False
        # need two identity words that are neither employee name parts nor system/team/deal terms
        unknown = [w for w in words if w.casefold().rstrip(".") not in self.employee_parts
                   and w.casefold().rstrip(".") not in self.keep_words]
        return len(unknown) >= 2

    def _person_chunk(self, text: str, start: int, end: int) -> tuple[int, int] | None:
        """First sub-span of a spaCy PERSON span that qualifies as a non-employee person name."""
        for m in _CHUNK.finditer(text, start, end):
            chunk = m.group(0)
            s = m.start() + (len(chunk) - len(chunk.lstrip()))
            e = m.start() + len(chunk.rstrip())
            if text[e - 2:e] in ("'s", "’s"):
                e -= 2
            if e > s and self._keep_person(text[s:e]):
                return s, e
        return None

    @staticmethod
    def _english_word(w: str) -> bool:
        try:
            return nlp_engine().nlp["en"].vocab.has_vector(w.lower())
        except Exception:  # noqa: BLE001 - vocab lookup is best-effort
            return False

    def detect(self, text: str, hint: str = "") -> list[Detection]:
        """Detect PII spans. ``hint`` is other text from the same document (e.g. its title) used for
        document-level context: an HR compensation email boosts bare dollar amounts to SALARY."""
        if not text or not text.strip():
            return []
        ctx = ["salary"] if _SALARY_DOC.search(text) or _SALARY_DOC.search(hint) else None
        results = self.engine.analyze(text=text, language="en", entities=self.entities, context=ctx)
        dets: list[Detection] = []
        for r in results:
            span = text[r.start:r.end]
            typ = r.entity_type
            rec = (r.recognition_metadata or {}).get("recognizer_name", "")
            start, end = r.start, r.end
            if typ == "PERSON":
                sub = self._person_chunk(text, start, end)
                if sub is None:
                    continue
                (start, end), typ = sub, "CLIENT_NAME"
            elif typ == "EMAIL" and span.lower().endswith("@" + self.domain):
                continue  # work email: business directory data
            elif (typ == "SWIFT" and not any(c.isdigit() for c in span) and not span.endswith("XXX")
                  and self._english_word(span)):
                continue  # an all-letter BIC shape that is just an upper-case word ("ACCEPTED")
            dets.append(Detection(typ, start, end, float(r.score), rec))
        # resolve overlaps: highest score, then longest span wins
        kept: list[Detection] = []
        for d in sorted(dets, key=lambda d: (-d.score, -(d.end - d.start), d.start)):
            if all(d.end <= k.start or d.start >= k.end for k in kept):
                kept.append(d)
        return sorted(kept, key=lambda d: d.start)
