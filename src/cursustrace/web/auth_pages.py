"""Login and register pages; no-ops that redirect home when multi-user is off."""

from __future__ import annotations

from nicegui import app, events, ui
from starlette.requests import Request

from cursustrace import auth, db
from cursustrace.web import state
from cursustrace.web.constants import APP_TITLE
from cursustrace.web.nav import app_footer
from cursustrace.web.session import current_user_id


def _multi_user_enabled() -> bool:
    return state._settings is not None and state._settings.multi_user


def _sanitize_redirect(raw: str | None) -> str:
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


def _login(email: str, password: str) -> bool:
    user = auth.authenticate(email, password)
    if user is None:
        return False
    app.storage.user["id"] = user["id"]
    app.storage.user["email"] = user["email"]
    return True


def _register(email: str, password: str) -> str | None:
    """Create the account and log in; return an error message on failure."""
    if not auth.is_valid_email(email):
        return "Enter a valid email address."
    if auth.password_problems(password):
        return "Password does not meet the requirements."
    user_id = db.create_user(email.strip(), auth.hash_password(password))
    if user_id is None:
        return "An account with this email already exists."
    app.storage.user["id"] = user_id
    app.storage.user["email"] = email.strip()
    return None


def login_page(request: Request) -> None:
    if not _multi_user_enabled():
        ui.navigate.to("/")
        return
    if current_user_id() is not None:
        ui.navigate.to(_sanitize_redirect(request.query_params.get("redirect_to")))
        return
    ui.page_title(f"Sign in — {APP_TITLE}")
    with ui.column().classes("w-full max-w-sm mx-auto justify-center").style("min-height: 70vh"):
        ui.label("Sign in to CursusTrace").classes("text-h5 text-center")
        email = ui.input("Email").classes("w-full").mark("login-email")
        password = ui.input("Password", password=True).classes("w-full").mark("login-password")
        error = ui.label().classes("text-negative w-full").mark("login-error")

        def submit() -> None:
            error.set_text("")
            if not _login(email.value or "", password.value or ""):
                error.set_text("Invalid email or password.")
                return
            ui.navigate.to(_sanitize_redirect(request.query_params.get("redirect_to")))

        password.on("keydown.enter", submit)
        ui.button("Sign in", on_click=submit).props("color=primary").classes("w-full").mark(
            "login-submit"
        )
        ui.link("Create an account", "/register").classes("w-full text-center").mark("go-register")

    app_footer()


def register_page() -> None:
    if not _multi_user_enabled():
        ui.navigate.to("/")
        return
    if current_user_id() is not None:
        ui.navigate.to("/")
        return
    ui.page_title(f"Create account — {APP_TITLE}")
    with ui.column().classes("w-full max-w-sm mx-auto justify-center").style("min-height: 70vh"):
        ui.label("Create your CursusTrace account").classes("text-h5 text-center")
        email = ui.input("Email").classes("w-full").mark("register-email")
        password = ui.input("Password", password=True).classes("w-full").mark("register-password")
        confirm = (
            ui.input("Confirm password", password=True).classes("w-full").mark("register-confirm")
        )
        checklist_column = ui.column().classes("w-full gap-0").mark("password-checklist")
        error = ui.label().classes("text-negative w-full").mark("register-error")

        def render_checklist() -> None:
            checklist_column.clear()
            value = password.value or ""
            with checklist_column:
                for problem in auth.password_problems(value):
                    ui.label(f"✗ {problem}").classes("text-caption text-negative")
                if value and not auth.password_problems(value):
                    ui.label("✓ Password meets all requirements").classes(
                        "text-caption text-positive"
                    )

        password.on_value_change(lambda _: render_checklist())
        render_checklist()

        def submit() -> None:
            error.set_text("")
            if (confirm.value or "") != (password.value or ""):
                error.set_text("Passwords do not match.")
                return
            registration_error = _register(email.value or "", password.value or "")
            if registration_error is not None:
                error.set_text(registration_error)
                return
            ui.navigate.to("/")

        confirm.on("keydown.enter", submit)
        ui.button("Create account", on_click=submit).props("color=primary").classes("w-full").mark(
            "register-submit"
        )
        ui.link("Back to sign in", "/login").classes("w-full text-center").mark("go-login")

    app_footer()


def _change_password_dialog() -> ui.dialog:
    """Dialog to replace the signed-in account's password."""
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Change password").classes("text-h6")
        current_input = (
            ui.input("Current password", password=True).classes("w-full").mark("current-password")
        )
        new_input = ui.input("New password", password=True).classes("w-full").mark("new-password")
        confirm_input = (
            ui.input("Confirm new password", password=True)
            .classes("w-full")
            .mark("confirm-new-password")
        )
        checklist_column = ui.column().classes("w-full gap-0")
        error = ui.label().classes("text-negative w-full").mark("change-password-error")

        def render_checklist() -> None:
            checklist_column.clear()
            value = new_input.value or ""
            with checklist_column:
                for problem in auth.password_problems(value):
                    ui.label(f"✗ {problem}").classes("text-caption text-negative")
                if value and not auth.password_problems(value):
                    ui.label("✓ Password meets all requirements").classes(
                        "text-caption text-positive"
                    )

        new_input.on_value_change(lambda _: render_checklist())
        render_checklist()

        def reset() -> None:
            """Clear the password fields so a reopened dialog starts empty."""
            current_input.value = ""
            new_input.value = ""
            confirm_input.value = ""
            render_checklist()

        def on_dialog_value_change(event: events.ValueChangeEventArguments[bool]) -> None:
            if event.value:
                reset()

        dialog.on_value_change(on_dialog_value_change)

        def submit() -> None:
            error.set_text("")
            user_id = current_user_id()
            user = db.get_user(user_id) if user_id is not None else None
            if user_id is None or user is None:
                error.set_text("Session expired. Sign in again.")
                return
            if not auth.verify_password(current_input.value or "", user["password_hash"]):
                error.set_text("Current password is incorrect.")
                return
            if auth.password_problems(new_input.value or ""):
                error.set_text("New password does not meet the requirements.")
                return
            if (confirm_input.value or "") != (new_input.value or ""):
                error.set_text("New passwords do not match.")
                return
            db.set_user_password(user_id, auth.hash_password(new_input.value or ""))
            dialog.close()
            ui.notify("Password changed.", type="positive")

        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat").mark("cancel-change-password")
            ui.button("Change password", on_click=submit).props("color=primary").mark(
                "change-password-submit"
            )
    return dialog


def logout() -> None:
    """Clear the session and return to the login page."""
    app.storage.user.clear()
    ui.navigate.to("/login")
