"""The skill folders shipped with Prometheus must all be well-formed."""
import re
from pathlib import Path

import prometheus
from prometheus.skillbook import SkillBook

SKILLS = Path(prometheus.__file__).resolve().parent / "skills"
EXPECTED = {"research", "task-planning", "memory", "models", "coding", "understand-software", "explain-code",
            "file-sorting", "video", "learning-roadmap"}


def test_every_folder_is_a_valid_skill():
    book = SkillBook(SKILLS)
    folders = {p.name for p in SKILLS.iterdir() if p.is_dir()}
    assert {s.name for s in book.catalog()} == folders        # nothing silently ignored
    assert EXPECTED <= folders


def test_descriptions_are_short_and_references_exist():
    book = SkillBook(SKILLS)
    for skill in book.catalog():
        assert 20 <= len(skill.description) <= 220, skill.name
        refs = skill.references()
        assert refs, f"{skill.name} has no reference files"
        for ref in refs:
            text = book.reference(skill.name, ref)
            assert text.lstrip().startswith("# "), f"{skill.name}/{ref} needs a title"
            assert len(text.split()) >= 150, f"{skill.name}/{ref} is too thin"
        for named in re.findall(r"`?([A-Za-z0-9_.-]+\.md)`?", skill.body):
            if named != "SKILL.md":
                assert named in refs, f"{skill.name}/SKILL.md mentions {named}, which does not exist"


def test_no_secrets_or_private_paths_in_skills():
    for path in SKILLS.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"sk-(ant|or)-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{20,}", text), path
        assert "/tmp/claude-0" not in text and "scratchpad" not in text, path
