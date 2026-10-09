"""Shared dialogs: delete confirmations, tag management, and database clearing."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from cursustrace import db


def _clear_database(refresh: Callable[[], None]) -> None:
    count = db.clear_all_jobs()
    ui.notify(f"Successfully cleared {count} positions.", type="positive")
    refresh()


def _delete_job(dialog: ui.dialog, job_id: int, refresh: Callable[[], None] | None = None) -> None:
    db.delete_job(job_id)
    dialog.close()
    ui.notify("Position deleted.", type="positive")
    if refresh is not None:
        refresh()


def _delete_job_from_detail(dialog: ui.dialog, job_id: int) -> None:
    _delete_job(dialog, job_id)
    ui.navigate.to("/")


def _confirm_delete_dialog(
    job: db.Job,
    on_confirm: Callable[[ui.dialog], None],
) -> ui.dialog:
    title = job["title"] or "Untitled position"
    company = job["company"] or "Unknown company"
    with ui.dialog() as dialog, ui.card():
        dialog.mark(f"delete-dialog-{job['id']}")
        ui.label("Delete this position?")
        ui.label(f"{title} — {company}").classes("font-bold")
        ui.label("This action cannot be undone.").classes("text-negative")
        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Delete", on_click=lambda: on_confirm(dialog)).props("color=negative").mark(
                f"delete-confirm-{job['id']}"
            )
    return dialog


def _tag_manager_dialog(refresh: Callable[[], None]) -> ui.dialog:
    """Dialog to create, rename, and delete tags, showing usage counts."""
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-lg"):
        dialog.mark("tag-manager")
        ui.label("Manage tags").classes("text-h6")
        name_input = ui.input("New tag name").classes("w-full").mark("new-tag-name")
        ui.button("Add tag", on_click=lambda: add_tag()).props("color=primary")
        list_column = ui.column().classes("w-full")

    with ui.dialog() as confirm_dialog, ui.card():
        confirm_dialog.mark("tag-delete-confirm")
        confirm_text = ui.label()
        pending: dict[str, int] = {}
        with ui.row():
            ui.button("Cancel", on_click=confirm_dialog.close).props("flat")
            ui.button("Delete tag", on_click=lambda: confirm_delete()).props("color=negative").mark(
                "confirm-delete-tag"
            )

    def add_tag() -> None:
        name = (name_input.value or "").strip()
        if not name:
            ui.notify("Enter a tag name.", type="warning")
            return
        if db.create_tag(name) is None:
            ui.notify(f"Tag '{name}' already exists.", type="warning")
            return
        name_input.value = ""
        render_list()
        refresh()
        ui.notify(f"Tag '{name}' created.", type="positive")

    def rename_tag(tag_id: int, new_name: str) -> None:
        if not db.rename_tag(tag_id, new_name.strip()):
            ui.notify("Tag name must not be empty or already taken.", type="warning")
            return
        render_list()
        refresh()
        ui.notify("Tag renamed.", type="positive")

    def ask_delete(tag_id: int, name: str, count: int) -> None:
        pending["tag_id"] = tag_id
        position_word = "position" if count == 1 else "positions"
        confirm_text.set_text(f"Delete tag '{name}'? {count} {position_word} would lose it.")
        confirm_dialog.open()

    def confirm_delete() -> None:
        tag_id = pending.get("tag_id")
        if tag_id is not None:
            db.delete_tag(tag_id)
        confirm_dialog.close()
        render_list()
        refresh()
        ui.notify("Tag deleted.", type="positive")

    def render_list() -> None:
        list_column.clear()
        counts = db.tag_counts()
        with list_column:
            if not counts:
                ui.label("No tags yet.")
                return
            for name, count in counts.items():
                tag_id = db.find_tag(name)
                if tag_id is None:
                    continue
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(f"{name} ({count})").classes("w-40")
                    rename_input = (
                        ui.input(value=name).classes("flex-1").mark(f"rename-tag-{tag_id}")
                    )
                    ui.button(
                        "Rename",
                        on_click=lambda old_id=tag_id, value_input=rename_input: rename_tag(
                            old_id, value_input.value or ""
                        ),
                    ).props("flat color=primary")
                    ui.button(
                        "Delete",
                        on_click=lambda old_id=tag_id, tag_name=name, uses=count: ask_delete(
                            old_id, tag_name, uses
                        ),
                    ).props("flat color=negative").mark(f"delete-tag-{tag_id}")

    render_list()
    return dialog
