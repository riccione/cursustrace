"""Kanban board: one column per pipeline stage, drag a card to change its stage."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from nicegui import events, ui

from cursustrace import db
from cursustrace.web.attention_ui import attention_reasons, most_urgent
from cursustrace.web.constants import STATUS_NAMES
from cursustrace.web.session import current_user_id

MAX_CARDS_PER_COLUMN = 50

STAGE_LABELS: dict[db.JobStatus, str] = dict(STATUS_NAMES)

_DRAG_START_JS = (
    "(e) => { e.dataTransfer.setData('text/plain', '__JOB_ID__');"
    " e.dataTransfer.effectAllowed = 'move'; }"
)
_DRAG_ENTER_JS = (
    "(e) => { const el = e.currentTarget;"
    " if (e.relatedTarget && el.contains(e.relatedTarget)) return;"
    " el.dataset.dragDepth = String(Number(el.dataset.dragDepth || 0) + 1);"
    " el.classList.add('is-drop-target'); }"
)
_DRAG_LEAVE_JS = (
    "(e) => { const el = e.currentTarget;"
    " if (e.relatedTarget && el.contains(e.relatedTarget)) return;"
    " const depth = Number(el.dataset.dragDepth || 0) - 1;"
    " el.dataset.dragDepth = String(Math.max(depth, 0));"
    " if (depth <= 0) el.classList.remove('is-drop-target'); }"
)
_DRAG_OVER_JS = "(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }"
_DRAG_END_JS = (
    "(e) => { document.querySelectorAll('.is-drop-target').forEach((el) => {"
    " el.classList.remove('is-drop-target'); el.dataset.dragDepth = '0'; }); }"
)
_DROP_JS = (
    "(e) => { e.preventDefault(); const el = e.currentTarget;"
    " el.dataset.dragDepth = '0'; el.classList.remove('is-drop-target');"
    " emit(e.dataTransfer.getData('text/plain')); }"
)


def move_job(job_id: int, status: db.JobStatus) -> None:
    """Move a position to a stage, recording the event like the stage checkboxes do."""
    db.set_job_status(job_id, status, user_id=current_user_id())


def _emitted_job_id(event: events.GenericEventArguments) -> int | None:
    """Read the job id the client emitted (a single value arrives unwrapped)."""
    value = event.args
    if isinstance(value, list):
        value = value[0] if value else None
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _drop_handler(
    status: db.JobStatus,
    refresh: Callable[[], None],
) -> Callable[[events.GenericEventArguments], None]:
    """Build a drop handler that already knows its column.

    The status is bound here instead of being read from the enclosing loop, so
    every column drops into its own stage rather than the last one built.
    """

    def on_drop(event: events.GenericEventArguments) -> None:
        job_id = _emitted_job_id(event)
        if job_id is None:
            return
        move_job(job_id, status)
        refresh()

    return on_drop


def _move(job_id: int, status: db.JobStatus, refresh: Callable[[], None]) -> None:
    move_job(job_id, status)
    refresh()


def _render_move_menu(job: db.Job, current: db.JobStatus, refresh: Callable[[], None]) -> None:
    """Render the menu that moves a card without a mouse (touch and keyboard)."""
    button = (
        ui.button(icon="swap_horiz")
        .props("flat dense round size=md aria-label=Move to another stage")
        .classes("ct-board-card__move")
        .mark(f"move-{job['id']}")
    )
    with button, ui.menu().props("auto-close"):
        for status in db.JOB_STATUSES:
            if status == current:
                continue
            ui.item(
                STAGE_LABELS[status],
                on_click=lambda target=status: _move(job["id"], target, refresh),
            ).mark(f"move-{job['id']}-to-{status}")


def _render_card(
    job: db.Job,
    *,
    refresh: Callable[[], None],
    tags: list[str],
    on_tag_click: Callable[[str], None] | None,
) -> None:
    """Render one compact board card."""
    current = db.job_status(job)
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    reasons = attention_reasons(job)
    card = ui.element("div").classes("ct-board-card w-full").mark(f"board-card-{job['id']}")
    card.props("draggable=true")
    card.on("dragstart", js_handler=_DRAG_START_JS.replace("__JOB_ID__", str(job["id"])))
    card.on("dragend", js_handler=_DRAG_END_JS)
    with card:
        with ui.row().classes("ct-board-card__top w-full items-start justify-between gap-1"):
            link = ui.link(title, f"/job/{job['id']}").classes("ct-board-card__title")
            link.props["title"] = title
            _render_move_menu(job, current, refresh)
        ui.label(company).classes("ct-board-card__company")
        if reasons:
            ui.chip(most_urgent(reasons), icon="warning").props("dense color=warning").mark(
                "attention-chip"
            )
        chips: list[tuple[str, str | None]] = [(name, None) for name in tags]
        if job["profile_name"]:
            chips.append((f"CV: {job['profile_name']}", "cv-used-chip"))
        if chips:
            with ui.row().classes("ct-board-card__chips items-center gap-1 flex-wrap"):
                for text, marker in chips:
                    if marker is None and on_tag_click is not None:
                        chip = ui.chip(text, on_click=lambda tag=text: on_tag_click(tag))
                    else:
                        chip = ui.chip(text)
                    chip.props("dense")
                    if marker is not None:
                        chip.mark(marker)


def _render_column(
    status: db.JobStatus,
    jobs: list[db.Job],
    empty_message: str,
    *,
    refresh: Callable[[], None],
    grouped_tags: dict[int, list[str]],
    on_tag_click: Callable[[str], None] | None,
    is_expanded: Callable[[], bool],
    mark_expanded: Callable[[db.JobStatus], None],
) -> None:
    """Render one droppable stage column with its cards."""
    column = ui.column().classes("ct-board__column").mark(f"board-column-{status}")
    column.on("dragenter", js_handler=_DRAG_ENTER_JS)
    column.on("dragleave", js_handler=_DRAG_LEAVE_JS)
    column.on("dragover", js_handler=_DRAG_OVER_JS)
    column.on("drop", _drop_handler(status, refresh), js_handler=_DROP_JS)
    attention = sum(1 for job in jobs if attention_reasons(job))
    with column:
        with ui.row().classes("ct-board__column-head w-full items-center gap-2"):
            ui.label(STAGE_LABELS[status]).classes("ct-board__column-title")
            ui.label(str(len(jobs))).classes(f"ct-badge ct-badge--{status}")
            if attention:
                with ui.row().classes("ct-board__column-attention items-center gap-1"):
                    ui.icon("warning")
                    ui.label(str(attention)).mark(f"board-attention-{status}")
        cards_box = ui.column().classes("ct-board__cards w-full gap-2")

    def render_cards() -> None:
        """Render the cards; "show all" re-renders this column only."""
        cards_box.clear()
        shown = jobs if is_expanded() else jobs[:MAX_CARDS_PER_COLUMN]
        with cards_box:
            for job in shown:
                _render_card(
                    job,
                    refresh=refresh,
                    tags=grouped_tags.get(job["id"], []),
                    on_tag_click=on_tag_click,
                )
            if len(jobs) > len(shown):

                def show_all() -> None:
                    mark_expanded(status)
                    render_cards()

                ui.button(f"Show all {len(jobs)}", on_click=show_all).props("flat dense").classes(
                    "ct-board__more"
                ).mark(f"board-expand-{status}")
            if not jobs:
                ui.label(empty_message).classes("ct-board__empty")

    render_cards()


def render_board(
    container: ui.element,
    jobs_by_status: dict[db.JobStatus, list[db.Job]],
    *,
    stages: tuple[db.JobStatus, ...],
    archived: tuple[db.JobStatus, ...],
    archived_open: bool,
    is_expanded: Callable[[db.JobStatus], bool],
    empty_messages: dict[db.JobStatus, str],
    refresh: Callable[[], None],
    on_tag_click: Callable[[str], None] | None,
    toggle_archive: Callable[[], None],
    mark_expanded: Callable[[db.JobStatus], None],
) -> None:
    """Render the board: the active stage columns plus the archived ones on demand."""
    archived_count = sum(len(jobs_by_status.get(status, [])) for status in archived)
    visible = (*stages, *archived) if archived_open else stages
    shown_jobs = {status: jobs_by_status.get(status, []) for status in visible}
    grouped = db.tags_for_jobs([job["id"] for jobs in shown_jobs.values() for job in jobs])
    container.clear()
    with container:
        with ui.row().classes("w-full items-center gap-2").mark("board-head"):
            ui.label("Drag a card to change its stage.").classes("text-caption ct-text-secondary")
            ui.space()
            ui.button(
                "Hide archived" if archived_open else f"Archived ({archived_count})",
                on_click=toggle_archive,
            ).props("outline dense").mark("board-archive")
        with ui.row().classes("ct-board w-full").mark("board"):
            for status in visible:
                _render_column(
                    status,
                    shown_jobs[status],
                    empty_messages[status],
                    refresh=refresh,
                    grouped_tags=grouped,
                    on_tag_click=on_tag_click,
                    is_expanded=partial(is_expanded, status),
                    mark_expanded=mark_expanded,
                )
