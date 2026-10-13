"""Skill lexicon and keyword matching between a JD and a CV.

The lexicon comes from synonyms.yaml. Matching finds which canonical skills
appear in a text, then compares the JD's required / preferred skills with the
skills found in the CV. Everything here is a pure function of its inputs.
"""

import hashlib
import re
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml

from app.scoring.normalize import HEBREW_PREFIX_LETTERS, collapse_key, has_hebrew, normalize_text

DEFAULT_SYNONYMS_PATH = Path(__file__).with_name("synonyms.yaml")

MatchStatus = Literal["matched", "implied", "missing"]


@dataclass(frozen=True)
class Skill:
    name: str
    aliases: tuple[str, ...]
    implies: tuple[str, ...] = ()
    strict: bool = False


def _alias_pattern(alias: str) -> str:
    """Regex for one alias where spaces, hyphens and underscores are interchangeable."""
    parts = [re.escape(part) for part in re.split(r"[\s\-_]+", alias.strip()) if part]
    return r"[\s\-_]+".join(parts)


def _alternation(aliases: list[str]) -> str:
    # Longest first, so "c++17" wins over "c++" and "linux kernel" over "linux".
    ordered = sorted(set(aliases), key=lambda a: (-len(a), a))
    return "|".join(_alias_pattern(alias) for alias in ordered)


class Lexicon:
    """Compiled skill vocabulary.

    Three regexes cover all aliases:
    - latin: normal word boundaries that understand C++, C#, .NET, TCP/IP
    - strict: short/ambiguous aliases such as "c" or "arm"; no hyphen,
      apostrophe or dot next to them ("c-level", "objective-c" do not match C,
      while the Hebrew "ב-C" does)
    - hebrew: allows up to two attached prefix letters (ו, ה, ב, ל, מ, ש, כ),
      so "ולינוקס" matches "לינוקס"
    """

    def __init__(self, skills: dict[str, Skill], version: str) -> None:
        self.skills = skills
        self.version = version
        self._alias_to_skill: dict[str, str] = {}

        latin: list[str] = []
        strict: list[str] = []
        hebrew: list[str] = []
        for skill in skills.values():
            for alias in skill.aliases:
                key = collapse_key(alias)
                previous = self._alias_to_skill.get(key)
                if previous is not None and previous != skill.name:
                    raise ValueError(f"Alias {alias!r} is listed under both {previous!r} and {skill.name!r}")
                self._alias_to_skill[key] = skill.name
                if has_hebrew(alias):
                    hebrew.append(alias)
                elif skill.strict:
                    strict.append(alias)
                else:
                    latin.append(alias)
            for target in skill.implies:
                if target not in skills:
                    raise ValueError(f"{skill.name!r} implies unknown skill {target!r}")

        self._patterns: list[re.Pattern[str]] = []
        if latin:
            self._patterns.append(
                re.compile(rf"(?<![\w+#.])(?P<t>{_alternation(latin)})(?![\w+#]|\.\w)")
            )
        if strict:
            self._patterns.append(
                re.compile(
                    # A hyphen before the alias is fine after Hebrew ("ב-C") but
                    # not after a latin word ("objective-c").
                    rf"(?<![\w+#.'])(?<![a-z0-9]-)(?P<t>{_alternation(strict)})(?![\w+#\-']|\.\w)"
                )
            )
        if hebrew:
            prefix = f"[{HEBREW_PREFIX_LETTERS}]"
            self._patterns.append(
                re.compile(rf"(?<!\w)(?:{prefix}{{1,2}})?(?P<t>{_alternation(hebrew)})(?!\w)")
            )

    def find(self, text: str) -> dict[str, list[str]]:
        """Return canonical skill -> distinct matched phrases, ordered by first appearance."""
        normalized = normalize_text(text)
        hits: list[tuple[int, str, str]] = []
        for pattern in self._patterns:
            for match in pattern.finditer(normalized):
                phrase = collapse_key(match.group("t"))
                skill_name = self._alias_to_skill.get(phrase)
                if skill_name is not None:
                    hits.append((match.start("t"), skill_name, phrase))
        hits.sort()

        found: dict[str, list[str]] = {}
        for _pos, skill_name, phrase in hits:
            phrases = found.setdefault(skill_name, [])
            if phrase not in phrases:
                phrases.append(phrase)
        return found

    def implied_by(self, present: set[str]) -> dict[str, str]:
        """Skills implied (transitively) by the present ones -> the skill that implies them.

        Skills already present are not included.
        """
        implied: dict[str, str] = {}
        queue: deque[tuple[str, str]] = deque()
        for name in sorted(present):
            for target in self.skills[name].implies:
                queue.append((target, name))
        while queue:
            target, source = queue.popleft()
            if target in present or target in implied:
                continue
            implied[target] = source
            for next_target in self.skills[target].implies:
                queue.append((next_target, source))
        return implied


def load_lexicon_from_yaml(raw: bytes) -> Lexicon:
    data = yaml.safe_load(raw.decode("utf-8")) or {}
    skills: dict[str, Skill] = {}
    for name, spec in (data.get("skills") or {}).items():
        name = str(name)
        spec = spec or {}
        aliases = tuple(str(alias).lower() for alias in spec.get("aliases") or [])
        if not aliases:
            raise ValueError(f"Skill {name!r} has no aliases")
        skills[name] = Skill(
            name=name,
            aliases=aliases,
            implies=tuple(str(target) for target in spec.get("implies") or []),
            strict=bool(spec.get("strict", False)),
        )
    version = hashlib.sha256(raw).hexdigest()[:12]
    return Lexicon(skills, version)


@lru_cache(maxsize=4)
def load_lexicon(path: Path = DEFAULT_SYNONYMS_PATH) -> Lexicon:
    return load_lexicon_from_yaml(path.read_bytes())


@dataclass
class SkillMatch:
    skill: str
    status: MatchStatus
    jd_phrases: list[str]
    # What in the CV satisfied it: the matched phrase, or for "implied" the
    # skill that implies it.
    cv_evidence: str | None = None


@dataclass
class MatchResult:
    required: list[SkillMatch] = field(default_factory=list)
    preferred: list[SkillMatch] = field(default_factory=list)
    cv_skills: list[str] = field(default_factory=list)

    def by_status(self, group: Literal["required", "preferred"], status: MatchStatus) -> list[SkillMatch]:
        items = self.required if group == "required" else self.preferred
        return [item for item in items if item.status == status]


def match_skills(required_text: str, preferred_text: str, cv_text: str, lexicon: Lexicon) -> MatchResult:
    """Compare the JD's required and preferred skills against the CV.

    A skill mentioned in both the required and the preferred part counts as
    required only.
    """
    required_found = lexicon.find(required_text)
    preferred_found = {
        name: phrases
        for name, phrases in lexicon.find(preferred_text).items()
        if name not in required_found
    }
    cv_found = lexicon.find(cv_text)
    implied = lexicon.implied_by(set(cv_found))

    def classify(found: dict[str, list[str]]) -> list[SkillMatch]:
        results: list[SkillMatch] = []
        for name, phrases in found.items():
            if name in cv_found:
                results.append(SkillMatch(name, "matched", phrases, cv_found[name][0]))
            elif name in implied:
                results.append(SkillMatch(name, "implied", phrases, implied[name]))
            else:
                results.append(SkillMatch(name, "missing", phrases))
        return results

    return MatchResult(
        required=classify(required_found),
        preferred=classify(preferred_found),
        cv_skills=list(cv_found),
    )
