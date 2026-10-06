"""Searchable, file-backed Learning Centre for Sentinel."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import markdown
from PySide6.QtCore import QEvent, QObject, QSize, Qt, QUrl

from ui.theme import accent, recolour
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImageReader,
    QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyledItemDelegate,
    QTextBrowser,
    QVBoxLayout,
)

from services.runtime_paths import resource_base


@dataclass(frozen=True)
class LearningTopic:
    title: str
    filename: str
    summary: str


# Grouped the same way as the curriculum in docs/training/README.md; a test
# keeps the two in step.
LEARNING_CURRICULUM = (
    ("Foundations", (
        LearningTopic("Start here", "quick_start.md", "A guided first run through Sentinel."),
        LearningTopic("Workspace tour", "workspace.md", "Navigation, work area, history and Inspector."),
        LearningTopic("Controls & settings", "controls_settings.md", "Every shared control and setting."),
        LearningTopic("Portable USB mode", "portable.md", "Build, use, update and safely eject a portable copy."),
        LearningTopic("Privacy & cost", "privacy_cost.md", "Choose routes and protect sensitive data."),
        LearningTopic("Troubleshooting", "troubleshooting.md", "Resolve common setup and run problems."),
    )),
    ("Agents", (
        LearningTopic("Chat essentials", "chat.md", "Conversation, routing, privacy and costs."),
        LearningTopic("Trace", "trace.md", "Public-source identity research and live sources."),
        LearningTopic("Bloodhound", "bloodhound.md", "Dossiers, images, and file discovery."),
        LearningTopic("Beacon", "beacon.md", "Authorised Wi-Fi diagnostics and lab workflows."),
        LearningTopic("Sentry", "sentry.md", "Read-only network anomaly watch and continuous background monitoring."),
        LearningTopic("Bug Spray", "bug_spray.md", "Authorised website assessment and reporting."),
        LearningTopic("Tunnel", "tunnel.md", "Profile-aware VPN checks, safe previews, design, and troubleshooting."),
        LearningTopic("Forge", "forge.md", "Create and review agent scaffolds."),
    )),
    ("Across Sentinel", (
        LearningTopic("Agent workflows", "workflows.md", "Combine agents to complete larger goals."),
        LearningTopic(
            "Testing roadmap",
            "../testing_roadmap.md",
            "How every agent, shared control, and release mode is verified.",
        ),
        LearningTopic("Advanced tools", "advanced_tools.md", "Safe next steps and the v3 Kali roadmap."),
    )),
)

LEARNING_TOPICS = tuple(
    topic for _group, topics in LEARNING_CURRICULUM for topic in topics
)

# Qt rich text understands a CSS subset: no block padding or borders, so code
# blocks and figures are wrapped in single-cell tables (see lesson_html).
LESSON_STYLESHEET = """
body { color: #d8dfdb; font-size: 14px; }
h1 { color: #ffffff; font-size: 26px; font-weight: 600; margin-top: 2px; margin-bottom: 14px; }
h2 { color: #3cff88; font-size: 17px; font-weight: 600; margin-top: 28px; margin-bottom: 8px; }
h3 { color: #e8ece9; font-size: 15px; font-weight: 600; margin-top: 20px; margin-bottom: 6px; }
h4 { color: #a8b3ad; font-size: 13px; font-weight: 600; margin-top: 16px; margin-bottom: 4px; }
p { margin-top: 0; margin-bottom: 12px; line-height: 150%; }
li { margin-bottom: 6px; line-height: 145%; }
ul, ol { margin-top: 0; margin-bottom: 12px; }
b, strong { color: #ffffff; font-weight: 600; }
em { color: #e8ece9; }
a { color: #3cff88; text-decoration: none; }
code { font-family: Menlo, Monaco, monospace; font-size: 12px; color: #74d99f; background-color: #1b201d; }
pre { font-family: Menlo, Monaco, monospace; font-size: 12px; color: #d8dfdb; margin: 0; white-space: pre-wrap; }
hr { background-color: #232a26; height: 1px; border: none; }
table.grid { border-collapse: collapse; border: 1px solid #232a26; margin-top: 4px; margin-bottom: 16px; }
table.grid th { background-color: #1b201d; color: #a8b3ad; font-size: 12px; font-weight: 600;
    text-align: left; padding: 8px 10px; border: 1px solid #232a26; }
table.grid td { padding: 8px 10px; border: 1px solid #232a26; }
table.code { border-collapse: collapse; border: 1px solid #232a26; margin-top: 2px; margin-bottom: 16px; }
table.code td { background-color: #0d0f0e; padding: 12px 14px; }
table.figure { border-collapse: collapse; border: 1px solid #232a26; margin-top: 6px; margin-bottom: 4px; }
table.figure td { padding: 0; }
p.caption { color: #7d8983; font-size: 12px; margin-top: 0; margin-bottom: 18px; }
"""

_FIGURE = re.compile(r'<p>\s*(<img [^>]*alt="([^"]*)"[^>]*/?>)\s*</p>')
_CODE_BLOCK = re.compile(r"<pre><code[^>]*>(.*?)\n?</code></pre>", re.S)

HEADER_ROLE = Qt.UserRole + 1
TOPIC_ROLE = Qt.UserRole + 2


def load_learning_topic(resource_root: Path, topic: LearningTopic) -> str:
    """Return topic Markdown, with a useful message when resources are missing."""
    path = resource_root / "docs" / "training" / topic.filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return (
            f"# {topic.title}\n\n"
            "This lesson is not available in the current installation. "
            "Update or reinstall Sentinel to restore its training files."
        )


def lesson_html(source: str) -> str:
    """Render lesson Markdown into HTML shaped for LESSON_STYLESHEET."""
    body = markdown.markdown(source, extensions=["tables", "fenced_code"])
    body = body.replace(
        "<table>", '<table class="grid" width="100%" cellspacing="0" cellpadding="0">'
    )
    body = _CODE_BLOCK.sub(
        r'<table class="code" width="100%" cellspacing="0"><tr><td><pre>\1</pre></td></tr></table>',
        body,
    )

    def figure(match: re.Match) -> str:
        image, alt = match.groups()
        caption = f'<p class="caption">{alt}</p>' if alt else ""
        return (
            '<table class="figure" cellspacing="0"><tr><td>'
            f"{image}</td></tr></table>{caption}"
        )

    return _FIGURE.sub(figure, body)


def fit_lesson_images(browser: QTextBrowser, natural: dict[str, QSize]) -> None:
    """Scale screenshots down to the reading width, never up past full size."""
    document = browser.document()
    # Leave room for the figure's border so the pane never scrolls sideways,
    # and for a vertical scrollbar that has not appeared yet: Qt shows it
    # during layout without sending the viewport another resize event.
    available = browser.viewport().width() - 2 * int(document.documentMargin()) - 4
    scrollbar = browser.verticalScrollBar()
    if not scrollbar.isVisible():
        available -= scrollbar.sizeHint().width()
    if available <= 0:
        return
    cursor = QTextCursor(document)
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            image = fragment.charFormat().toImageFormat()
            if fragment.isValid() and image.isValid():
                name = image.name()
                if name not in natural:
                    path = document.baseUrl().resolved(QUrl(name)).toLocalFile()
                    natural[name] = QImageReader(path).size()
                size = natural[name]
                if size.isValid() and size.width() > 0:
                    width = min(size.width(), available)
                    image.setWidth(width)
                    image.setHeight(size.height() * width / size.width())
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(
                        fragment.position() + fragment.length(), QTextCursor.KeepAnchor
                    )
                    cursor.setCharFormat(image)
            iterator += 1
        block = block.next()


class _ImageFitter(QObject):
    """Re-fit lesson images whenever the reading pane changes width."""

    def __init__(self, browser: QTextBrowser) -> None:
        super().__init__(browser)
        self.browser = browser
        self.natural: dict[str, QSize] = {}
        self._width = -1
        browser.viewport().installEventFilter(self)

    def fit(self) -> None:
        self._width = self.browser.viewport().width()
        fit_lesson_images(self.browser, self.natural)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Resize and self.browser.viewport().width() != self._width:
            self.fit()
        return False


class LearningTopicDelegate(QStyledItemDelegate):
    """Paint group headers as a letter-spaced caption with a hairline rule."""

    def sizeHint(self, option, index) -> QSize:
        if index.data(HEADER_ROLE):
            return QSize(option.rect.width(), 26 if index.row() == 0 else 40)
        return super().sizeHint(option, index)

    def paint(self, painter, option, index) -> None:
        if not index.data(HEADER_ROLE):
            super().paint(painter, option, index)
            return
        painter.save()
        font = QFont(option.font)
        font.setPixelSize(10)
        font.setWeight(QFont.DemiBold)
        font.setCapitalization(QFont.AllUppercase)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.8)
        metrics = QFontMetrics(font)
        text = str(index.data(Qt.DisplayRole) or "")
        rect = option.rect.adjusted(16, 0, -12, -6)
        painter.setFont(font)
        painter.setPen(QColor(accent()))
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignBottom, text)
        rule_x = rect.left() + metrics.horizontalAdvance(text.upper()) + 10
        rule_y = rect.bottom() - metrics.descent() - metrics.xHeight() // 2
        if rule_x < rect.right():
            painter.setPen(QPen(QColor("#232a26"), 1))
            painter.drawLine(rule_x, rule_y, rect.right(), rule_y)
        painter.restore()


def build_learning_center(app) -> QDialog:
    resource_root = resource_base()
    dialog = QDialog(app)
    dialog.setWindowTitle("Sentinel Learning Centre")
    dialog.resize(1120, 760)

    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(16, 16, 16, 16)
    outer.setSpacing(10)

    title = QLabel("Learning Centre")
    title.setObjectName("AgentTitle")
    outer.addWidget(title)
    subtitle = QLabel("Detailed, practical training for using Sentinel safely and effectively.")
    subtitle.setObjectName("AgentSubtitle")
    outer.addWidget(subtitle)

    search_row = QHBoxLayout()
    search_box = QLineEdit()
    search_box.setPlaceholderText("Search the current lesson…")
    search_box.setClearButtonEnabled(True)
    search_row.addWidget(search_box, 1)
    previous_btn = QPushButton("Previous")
    previous_btn.setObjectName("ChipBtn")
    next_btn = QPushButton("Next")
    next_btn.setObjectName("ChipBtn")
    match_label = QLabel("")
    match_label.setObjectName("DocsMatchLabel")
    search_row.addWidget(previous_btn)
    search_row.addWidget(next_btn)
    search_row.addWidget(match_label)
    outer.addLayout(search_row)

    body = QHBoxLayout()
    body.setSpacing(12)
    topic_list = QListWidget()
    topic_list.setObjectName("LearningTopicList")
    topic_list.setFixedWidth(245)
    topic_list.setItemDelegate(LearningTopicDelegate(topic_list))
    topic_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    first_item = None
    index = 0
    for group, topics in LEARNING_CURRICULUM:
        header = QListWidgetItem(group)
        header.setFlags(Qt.NoItemFlags)
        header.setData(HEADER_ROLE, True)
        topic_list.addItem(header)
        for topic in topics:
            item = QListWidgetItem(topic.title)
            item.setToolTip(topic.summary)
            item.setData(TOPIC_ROLE, index)
            topic_list.addItem(item)
            first_item = first_item or item
            index += 1
    body.addWidget(topic_list)

    browser = QTextBrowser()
    browser.setObjectName("LearningBrowser")
    browser.setOpenExternalLinks(True)
    # Tables, figures and wrapped code all fit the width; Qt still measures
    # an image inside a table cell a pixel wide, which would add a scrollbar.
    browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    browser.setSearchPaths([str(resource_root / "docs" / "training")])
    document = browser.document()
    document.setBaseUrl(QUrl.fromLocalFile(str(resource_root) + "/"))
    document.setDefaultStyleSheet(recolour(LESSON_STYLESHEET))
    document.setDocumentMargin(22)
    fitter = _ImageFitter(browser)
    body.addWidget(browser, 1)
    outer.addLayout(body, 1)

    def render_topic(current, _previous=None) -> None:
        if current is None or current.data(TOPIC_ROLE) is None:
            return
        topic = LEARNING_TOPICS[current.data(TOPIC_ROLE)]
        browser.setHtml(lesson_html(load_learning_topic(resource_root, topic)))
        fitter.fit()
        browser.moveCursor(QTextCursor.Start)
        search_box.clear()

    topic_list.currentItemChanged.connect(render_topic)
    app._wire_document_search(
        search_box, previous_btn, next_btn, match_label, browser
    )

    close_btn = QPushButton("Close")
    close_btn.setObjectName("ChipBtn")
    close_btn.clicked.connect(dialog.accept)
    close_btn.setFixedWidth(100)
    outer.addWidget(close_btn, 0, Qt.AlignRight)

    topic_list.setCurrentItem(first_item)
    return dialog


def show_learning_center(app) -> None:
    build_learning_center(app).exec()
