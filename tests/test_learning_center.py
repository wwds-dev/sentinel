from pathlib import Path
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QTextBrowser, QWidget

from ui.learning_center import (
    HEADER_ROLE,
    LEARNING_CURRICULUM,
    LEARNING_TOPICS,
    build_learning_center,
    lesson_html,
    load_learning_topic,
)


def test_every_learning_topic_exists_in_project_resources():
    root = Path(__file__).resolve().parents[1]
    for topic in LEARNING_TOPICS:
        text = load_learning_topic(root, topic)
        assert text.startswith("# ")
        assert "not available in the current installation" not in text
        assert len(text.split()) >= 100


def test_learning_topic_missing_resource_has_readable_fallback(tmp_path):
    text = load_learning_topic(tmp_path, LEARNING_TOPICS[0])
    assert LEARNING_TOPICS[0].title in text
    assert "not available" in text


def test_learning_topics_have_unique_titles_and_files():
    assert len({topic.title for topic in LEARNING_TOPICS}) == len(LEARNING_TOPICS)
    assert len({topic.filename for topic in LEARNING_TOPICS}) == len(LEARNING_TOPICS)


def test_sidebar_groups_match_the_readme_curriculum():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "docs" / "training" / "README.md").read_text(encoding="utf-8")
    curriculum = readme.split("## Complete curriculum", 1)[1].split("\n## ", 1)[0]
    sections = {
        heading.strip(): re.findall(r"\]\(([^)]+)\)", body)
        for heading, body in re.findall(r"### (.+)\n((?:.|\n)*?)(?=\n### |\Z)", curriculum)
    }
    assert list(sections) == [group for group, _topics in LEARNING_CURRICULUM]
    for group, topics in LEARNING_CURRICULUM:
        assert sections[group] == [topic.filename for topic in topics], group


def test_lesson_html_shapes_tables_code_and_figures_for_the_stylesheet():
    html = lesson_html(
        "![Alt text](docs/training/images/trace.png)\n\n"
        "|a|b|\n|-|-|\n|1|2|\n\n```bash\necho hi\n```\n"
    )
    assert '<table class="figure"' in html
    assert '<p class="caption">Alt text</p>' in html
    assert '<table class="grid" width="100%"' in html
    assert '<table class="code" width="100%"' in html
    assert "<pre>echo hi</pre>" in html


def test_release_bundle_includes_training_resources():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "Sentinel.spec").read_text(encoding="utf-8")
    assert '("docs/training", "docs/training")' in spec
    assert '("docs/testing_roadmap.md", "docs")' in spec


def test_complete_testing_roadmap_is_an_in_app_learning_topic():
    root = Path(__file__).resolve().parents[1]
    topic = next(topic for topic in LEARNING_TOPICS if topic.title == "Testing roadmap")
    text = load_learning_topic(root, topic)

    assert text.startswith("# Sentinel testing roadmap")
    assert "## Agent-by-agent matrix" in text
    assert "## Shared functionality matrix" in text
    assert "## Release gate" in text


def test_every_training_screenshot_reference_exists():
    root = Path(__file__).resolve().parents[1]
    training = root / "docs" / "training"
    references = []
    for source in training.glob("*.md"):
        references.extend(re.findall(r"!\[[^]]*]\(([^)]+)\)", source.read_text()))
    assert len(references) >= 8
    for reference in references:
        assert (root / reference).is_file(), reference


def test_learning_center_opens_with_first_lesson(monkeypatch):
    qapp = QApplication.instance() or QApplication([])
    class Host(QWidget):
        @staticmethod
        def _wire_document_search(search, previous, next_button, label, browser):
            search.textChanged.connect(lambda text: label.setText(text))

    host = Host()
    dialog = build_learning_center(host)
    assert dialog.windowTitle() == "Sentinel Learning Centre"
    browser = dialog.findChild(QTextBrowser, "LearningBrowser")
    assert browser is not None
    assert "complete a safe first request" in browser.toPlainText()

    topics = dialog.findChild(QListWidget, "LearningTopicList")
    assert topics.currentItem().text() == LEARNING_TOPICS[0].title
    headers = [
        topics.item(row) for row in range(topics.count())
        if topics.item(row).data(HEADER_ROLE)
    ]
    assert [item.text() for item in headers] == [group for group, _ in LEARNING_CURRICULUM]
    assert all(item.flags() == Qt.NoItemFlags for item in headers)
    assert topics.count() == len(LEARNING_TOPICS) + len(headers)

    testing_items = topics.findItems("Testing roadmap", Qt.MatchExactly)
    assert len(testing_items) == 1
    topics.setCurrentItem(testing_items[0])
    assert "Agent-by-agent matrix" in browser.toPlainText()
