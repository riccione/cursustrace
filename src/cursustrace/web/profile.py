"""Profile editor, copyable fields, and the read-only profile summary."""

from __future__ import annotations

from typing import TypeVar

from nicegui import events, ui

from cursustrace import db
from cursustrace.errors import PdfExportError
from cursustrace.pdf_exporter import generate_cv_pdf, get_pdf_filename, load_cv_styles
from cursustrace.web.state import SELECTED_PROFILE_KEY, _cv_style_path

_FieldT = TypeVar("_FieldT", bound=ui.input)


def _attach_copy_button(field: _FieldT, label: str, marker: str) -> _FieldT:
    """Add an in-field copy-to-clipboard button to an input or textarea."""

    def copy() -> None:
        if not field.value:
            ui.notify(f"{label} is empty", type="warning")
            return
        ui.clipboard.write(field.value)
        ui.notify(f"Copied {label}!", type="positive")

    with field.add_slot("append"):
        ui.button(icon="content_copy", on_click=copy).props("flat dense").mark(marker)
    return field


def _render_profile_editor() -> None:
    ui.label("👤 Profile & CV Configuration").classes("text-h5")

    profiles = db.list_profiles()
    known_ids = {profile["id"] for profile in profiles}
    stored = db.get_setting(SELECTED_PROFILE_KEY)
    stored_id = int(stored) if stored is not None and stored.isdigit() else None
    selected: dict[str, int | None] = {
        "id": stored_id if stored_id in known_ids else (min(known_ids) if known_ids else None)
    }

    with ui.dialog() as add_dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Add profile").classes("text-h6")
        new_name_input = ui.input("Profile name").classes("w-full").mark("new-profile-name")
        with ui.row():
            ui.button("Cancel", on_click=add_dialog.close).props("flat")
            ui.button("Add", on_click=lambda: confirm_add()).props("color=primary").mark(
                "confirm-add-profile"
            )

    with ui.dialog() as delete_dialog, ui.card():
        ui.label("Delete this profile?").classes("text-h6")
        delete_target = ui.label().classes("font-bold")
        ui.label("This action cannot be undone.").classes("text-negative")
        delete_confirm_input = (
            ui.input("Type 'DELETE' to confirm", placeholder="DELETE")
            .classes("w-full")
            .mark("delete-profile-confirm")
        )
        delete_button = (
            ui.button("Delete profile", on_click=lambda: confirm_delete())
            .props("color=negative")
            .mark("confirm-delete-profile")
        )
        delete_button.enabled = False

        def update_delete_button(event: events.ValueChangeEventArguments[str | None]) -> None:
            delete_button.enabled = event.value == "DELETE"

        delete_confirm_input.on_value_change(update_delete_button)

    container = ui.column().classes("w-full")
    with container:
        topbar = ui.row().classes("w-full items-center gap-4")
        body = ui.column().classes("w-full")

    def persist_selection() -> None:
        if selected["id"] is not None:
            db.set_setting(SELECTED_PROFILE_KEY, str(selected["id"]))

    def render_topbar() -> None:
        current_profiles = db.list_profiles()
        ids = {profile["id"] for profile in current_profiles}
        if selected["id"] not in ids:
            selected["id"] = min(ids) if ids else None
        topbar.clear()
        with topbar:
            if len(current_profiles) >= 2:
                selector = (
                    ui.select(
                        options={profile["id"]: profile["name"] for profile in current_profiles},
                        value=selected["id"],
                        label="Profile",
                    )
                    .classes("w-64")
                    .mark("profile-select")
                )
                selector.on_value_change(switch_profile)
            ui.button("➕ Add Profile", on_click=add_dialog.open).mark("add-profile")
            if selected["id"] is not None:
                ui.button(
                    "🗑 Delete Profile",
                    on_click=open_delete,
                ).props("color=negative").mark("delete-profile")

    def switch_profile(event: events.ValueChangeEventArguments[int | None]) -> None:
        if event.value is None:
            return
        selected["id"] = event.value
        persist_selection()
        render_form()

    def confirm_add() -> None:
        name = (new_name_input.value or "").strip()
        if not name:
            ui.notify("Enter a profile name.", type="warning")
            return
        profile_id = db.create_profile(name)
        if profile_id is None:
            ui.notify(f"Profile '{name}' already exists.", type="warning")
            return
        new_name_input.value = ""
        add_dialog.close()
        selected["id"] = profile_id
        persist_selection()
        rerender_all()
        ui.notify(f"Profile '{name}' created.", type="positive")

    def open_delete() -> None:
        if selected["id"] is None:
            return
        delete_target.set_text(f"Profile '{db.get_profile(selected['id'])['name']}'")
        delete_confirm_input.value = ""
        delete_button.enabled = False
        delete_dialog.open()

    def confirm_delete() -> None:
        if selected["id"] is None:
            return
        db.delete_profile(selected["id"])
        delete_dialog.close()
        remaining = db.list_profiles()
        selected["id"] = min((profile["id"] for profile in remaining), default=None)
        if selected["id"] is not None:
            persist_selection()
        rerender_all()
        ui.notify("Profile deleted.", type="positive")

    def render_form() -> None:
        body.clear()
        with body:
            profiles_now = db.list_profiles()
            ids = {profile["id"] for profile in profiles_now}
            if selected["id"] not in ids:
                selected["id"] = min(ids) if ids else None
            if selected["id"] is None:
                ui.label("No profiles yet. Add a profile to create your CV.").mark("no-profiles")
                return
            _render_profile_fields(db.get_profile(selected["id"]))

    def rerender_all() -> None:
        render_topbar()
        render_form()

    def _copyable_input(label: str, value: str, marker: str) -> ui.input:
        """Profile contact field with an in-field copy-to-clipboard button."""
        return _attach_copy_button(ui.input(label, value=value), label, marker)

    def _copyable_textarea(label: str, value: str, marker: str) -> ui.textarea:
        """CV markdown section with an in-field copy-to-clipboard button."""
        return _attach_copy_button(
            ui.textarea(label, value=value)
            .classes("w-full")
            .props('autogrow input-style="min-height: 140px"'),
            label,
            marker,
        )

    def _render_profile_fields(profile: db.Profile) -> None:
        with ui.row().classes("w-full gap-8"):
            with ui.column().classes("flex-1 gap-2"):
                name = _copyable_input("Full Name", profile["full_name"], "copy-full-name")
                email = _copyable_input("Email", profile["email"], "copy-email")
                linkedin = _copyable_input("LinkedIn URL", profile["linkedin_url"], "copy-linkedin")
            with ui.column().classes("flex-1 gap-2"):
                location = ui.input("Location", value=profile["location"])
                phone = _copyable_input("Phone Number", profile["phone"], "copy-phone")
                github = _copyable_input("GitHub URL", profile["github_url"], "copy-github")

        def combined_markdown() -> str:
            sections = (
                summary.value or "",
                work_history.value or "",
                education.value or "",
                skills.value or "",
            )
            return "\n\n".join(section.strip() for section in sections if section.strip())

        ui.label("Markdown CV").classes("text-h6")
        with ui.row().classes("w-full gap-4"):
            with ui.column().classes("flex-1 gap-4"):
                summary = _copyable_textarea("Summary", profile["summary"], "copy-summary")
                work_history = _copyable_textarea(
                    "Work History", profile["work_history"], "copy-work-history"
                )
                education = _copyable_textarea("Education", profile["education"], "copy-education")
                skills = _copyable_textarea("Skills", profile["skills"], "copy-skills")
            with ui.column().classes("flex-1"):
                ui.label("Live preview").classes("font-bold")
                preview = ui.markdown(combined_markdown() or "_Nothing to preview yet._")

        def update_preview(_event: events.ValueChangeEventArguments[str | None]) -> None:
            preview.set_content(combined_markdown() or "_Nothing to preview yet._")

        for section in (summary, work_history, education, skills):
            section.on_value_change(update_preview)

        def save() -> None:
            db.save_profile(
                {
                    "id": profile["id"],
                    "name": profile["name"],
                    "full_name": name.value or "",
                    "location": location.value or "",
                    "phone": phone.value or "",
                    "email": email.value or "",
                    "linkedin_url": linkedin.value or "",
                    "github_url": github.value or "",
                    "summary": summary.value or "",
                    "work_history": work_history.value or "",
                    "education": education.value or "",
                    "skills": skills.value or "",
                    "date_updated": None,
                }
            )
            ui.notify("Profile and CV saved successfully!", type="positive")

        ui.button("💾 Save Profile & CV", on_click=save).props("color=primary")

        ui.separator()

        def export() -> None:
            current = db.get_profile(profile["id"])
            try:
                pdf_bytes = generate_cv_pdf(current, load_cv_styles(_cv_style_path()))
            except PdfExportError as exc:
                ui.notify(f"Could not generate PDF: {exc}", type="negative")
                return
            ui.download.content(
                pdf_bytes,
                get_pdf_filename(current["full_name"]),
                media_type="application/pdf",
            )

        ui.button("📄 Export to PDF", on_click=export)

    rerender_all()


def _render_copyable_field(label: str, value: str, field_marker: str, copy_marker: str) -> None:
    """Readonly contact field with an in-field copy-to-clipboard button."""
    field_input = (
        ui.input(label, value=value).props("readonly").classes("w-full").mark(field_marker)
    )
    _attach_copy_button(field_input, label, copy_marker)


def _render_profile_summary() -> None:
    """Render each profile's contact fields; render nothing when no profiles exist."""
    profiles = db.list_profiles()
    if not profiles:
        return
    ui.separator()
    with ui.column().classes("w-full gap-2").mark("profile-summary"):
        ui.label("Your profile").classes("text-h6")
        for profile in profiles:
            if len(profiles) > 1:
                ui.label(profile["name"]).classes("text-subtitle1")
            with ui.row().classes("w-full gap-8"):
                with ui.column().classes("flex-1 gap-2"):
                    _render_copyable_field(
                        "Full Name",
                        profile["full_name"],
                        "detail-field-name",
                        "detail-copy-name",
                    )
                    _render_copyable_field(
                        "Email", profile["email"], "detail-field-email", "detail-copy-email"
                    )
                    _render_copyable_field(
                        "Phone", profile["phone"], "detail-field-phone", "detail-copy-phone"
                    )
                with ui.column().classes("flex-1 gap-2"):
                    _render_copyable_field(
                        "LinkedIn URL",
                        profile["linkedin_url"],
                        "detail-field-linkedin",
                        "detail-copy-linkedin",
                    )
                    _render_copyable_field(
                        "GitHub URL",
                        profile["github_url"],
                        "detail-field-github",
                        "detail-copy-github",
                    )
