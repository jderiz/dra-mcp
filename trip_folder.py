"""Per-trip folders holding private copies of the shared form templates.

The template folder (DRA_TEMPLATE_DIR) is shared with colleagues, so nothing is
ever written there. Each new Dienstreiseantrag gets its own folder below
DRA_TRIPS_DIR with a copy of the whole template tree, a `receipts/` folder and
the filled PDFs.
"""

import os
import re
import shutil
import stat
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
DEFAULT_TRIPS_DIR = Path.home() / "Documents" / "Dienstreisen"

APPLICATION_FORM = Path("01_Application") / "01_application-form.pdf"
TRAVEL_COST_REPORT_FORM = Path("02_Accounting") / "02_travel-cost-report.pdf"
APPLICATION_PREFIX = "Dienstreiseantrag_"
TRAVEL_COST_REPORT_PREFIX = "Reisekostenabrechnung_"

# Sync-client state and editor lock files must not travel with the copy.
TEMPLATE_IGNORE = shutil.ignore_patterns(".sync_*", ".DS_Store", "*.db", "*.db-*", "~$*", ".~lock*")


def template_dir() -> Path | None:
    """The shared template folder, or None if DRA_TEMPLATE_DIR is not set."""
    load_dotenv(ENV_FILE)
    value = os.getenv("DRA_TEMPLATE_DIR")
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"DRA_TEMPLATE_DIR {path} is not a directory.")
    return path


def assert_outside_templates(path: str | Path) -> None:
    templates = template_dir()
    resolved = Path(path).expanduser().resolve()
    if templates is not None and resolved.is_relative_to(templates):
        raise ValueError(f"Refusing to write {resolved}: it is inside the shared template folder {templates}.")


def trips_dir() -> Path:
    load_dotenv(ENV_FILE)
    path = Path(os.getenv("DRA_TRIPS_DIR") or DEFAULT_TRIPS_DIR).expanduser().resolve()
    assert_outside_templates(path)
    return path


def slugify(text: str) -> str:
    return re.sub(r"[^\w.-]+", "-", text).strip("-.")[:60] or "trip"


def create_trip_folder(trip_date: date | None, destination: str | None) -> Path:
    """Create `<trips>/<YYYY-MM-DD>_<destination>/` with templates/ and receipts/."""
    root = trips_dir()
    root.mkdir(parents=True, exist_ok=True)
    name = f"{(trip_date or date.today()).isoformat()}_{slugify(destination or 'trip')}"
    folder = root / name
    suffix = 0
    while True:
        try:
            folder.mkdir()
            break
        except FileExistsError:
            suffix += 1
            folder = root / f"{name}-{suffix}"

    templates = template_dir()
    if templates is not None:
        copy = folder / "templates"
        shutil.copytree(templates, copy, ignore=TEMPLATE_IGNORE)
        for path in copy.rglob("*"):
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
    (folder / "receipts").mkdir()
    return folder


def trip_form(folder: Path, form: Path) -> Path | None:
    path = folder / "templates" / form
    return path if path.is_file() else None


def latest_pdf(folder: Path, prefix: str) -> Path | None:
    candidates = sorted(folder.glob(f"{prefix}*.pdf"), key=lambda path: path.stat().st_mtime)
    return candidates[-1] if candidates else None


def resolve_trip_folder(trip_folder: str | Path) -> Path:
    """Accept an absolute path or a folder name below DRA_TRIPS_DIR."""
    folder = Path(trip_folder).expanduser()
    if not folder.is_absolute():
        folder = trips_dir() / folder
    folder = folder.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"Trip folder {folder} does not exist.")
    assert_outside_templates(folder)
    return folder


def list_trips() -> list[dict]:
    """List trip folders with their filled forms and receipt count."""
    root = trips_dir()
    if not root.is_dir():
        return []
    trips = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        application = latest_pdf(folder, APPLICATION_PREFIX)
        report = latest_pdf(folder, TRAVEL_COST_REPORT_PREFIX)
        receipts = folder / "receipts"
        trips.append(
            {
                "trip_folder": str(folder),
                "application_pdf": str(application) if application else None,
                "travel_cost_report_pdf": str(report) if report else None,
                "receipts": len([path for path in receipts.iterdir() if path.is_file()]) if receipts.is_dir() else 0,
            }
        )
    return trips
