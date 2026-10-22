"""Fill the travel-cost report (Reisekostenabrechnung) inside a trip folder.

Values come from, in order of precedence: explicit arguments, the trip's filled
Dienstreiseantrag, and `.env`. Answers that no receipt or ticket can provide
(free meals, private stay, bonus programs, ...) must always be given explicitly.
"""

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import Literal, Sequence

from dotenv import load_dotenv
from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.generic import NameObject

import business_trip
from business_trip import check_trip_order, format_date, open_file
from pdf_forms import fill_pdf_form, resolve_signature
from trip_folder import (
    APPLICATION_PREFIX,
    ENV_FILE,
    TRAVEL_COST_REPORT_FORM,
    TRAVEL_COST_REPORT_PREFIX,
    assert_outside_templates,
    latest_pdf,
    resolve_trip_folder,
    trip_form,
)

SIGNATURE_FIELD = "Signature1"

TEXT_FIELDS = {
    "service_unit": "dienststelle",
    "applicant_name": "Antragsteller.Name",
    "personnel_number": "Antragsteller.GZ",
    "department": "Antragsteller.Referat",
    "office_phone": "Antragsteller.telefondientl",
    "home_address": "Antragsteller.adr1",
    "additional_home_address": "Antragsteller.adr2",
    "temporary_stay_address": "Antragsteller.adr3",
    "iban": "ktonr",
    "bic": "blz",
    "bank_name": "bank",
    "destination": "reiseziel",
    "purpose": "zweck",
    "departure_date": "dat1",
    "departure_time": "uz1",
    "outbound_transport": "mittel1",
    "outbound_scheduled_departure": "abfahrt1",
    "outbound_local_transport": "zugang1",
    "outbound_ticket_cost": "fk1",
    "outbound_reservation_cost": "res1",
    "outbound_luggage_cost": "gp1",
    "outbound_local_transport_cost": "dwg1",
    "outbound_private_car_km": "kmzahl",
    "business_start_date": "dat2",
    "business_start_time": "uz2",
    "business_end_date": "dat3",
    "business_end_time": "uz3",
    "further_business_locations": "gedschort",
    "business_address": "adrgeschort",
    "trips_at_business_location": "fahrten",
    "business_local_transport_cost": "dwg2",
    "business_private_car_km": "kmzahl2",
    "return_transport": "mittel4",
    "return_scheduled_departure": "abfahrt4",
    "return_date": "dat4",
    "return_time": "uz4",
    "return_local_transport": "zugang",
    "return_ticket_cost": "fk4",
    "return_reservation_cost": "res4",
    "return_luggage_cost": "gp2",
    "return_local_transport_cost": "dwg4",
    "return_private_car_km": "kmzahl3",
    "hotel_name": "hotel",
    "hotel_location": "ort",
    "hotel_from": "von",
    "hotel_until": "bis",
    "accommodation_total_cost": "uebernkosten_ges",
    "accommodation_total_nights": "anznaechte_01",
    "accommodation_cost": "uebKostenBetr_01",
    "accommodation_nights": "anznaechte_02",
    "hotel_breakfast_cost": "uebKostenBetr_02",
    "hotel_breakfast_count": "anznaechte_03",
    "hotel_lunch_cost": "uebKostenBetr_03",
    "hotel_lunch_count": "anznaechte_04",
    "hotel_dinner_cost": "uebKostenBetr_04",
    "hotel_dinner_count": "anznaechte_05",
    "service_package_name": "servicePauschBez_01",
    "service_package_cost": "servicePauschBetr_01",
    "service_package_nights": "anznaechte_06",
    "declined_free_meals": "mahlzeiten",
    "declined_free_meals_reason": "begruendung",
    "free_meals_occasion": "anlass",
    "free_accommodation_nights": "naechte1",
    "bonus_program_name": "programm",
    "private_stay_from": "dat12",
    "private_stay_until": "dat13",
    "private_stay_destination": "ziel",
    "other_notes": "angaben",
    "advance_received": "betrag3",
    "email": "mail",
    "report_date": "dat14",
}

DATE_FIELDS = {
    "departure_date",
    "business_start_date",
    "business_end_date",
    "return_date",
    "hotel_from",
    "hotel_until",
    "private_stay_from",
    "private_stay_until",
    "report_date",
}

# Boolean argument -> check boxes it ticks (field, export state).
CHECKBOX_FIELDS = {
    "uses_personal_travel_card": (("kk1", "/Yes"), ("kk7", "/Yes")),
    "ticket_provided_by_travel_office": (("kk2", "/Yes"), ("kk8", "/Yes")),
    "email_consent": (("kk22", "/Yes"),),
}

# Amount argument -> the check box in front of it, ticked when the amount is given.
AMOUNT_CHECKBOXES = {
    "outbound_ticket_cost": ("kk3", "/ja"),
    "outbound_reservation_cost": ("kk4", "/ja"),
    "outbound_luggage_cost": ("kkgp", "/ja"),
    "outbound_local_transport_cost": ("kk5", "/ja"),
    "business_local_transport_cost": ("kk6", "/ja"),
    "return_ticket_cost": ("kk9", "/ja"),
    "return_reservation_cost": ("kk10", "/ja"),
    "return_luggage_cost": ("kkgp2", "/ja"),
    "return_local_transport_cost": ("kk11", "/ja"),
    "accommodation_total_cost": ("Pg1_Obj124", "/gesamt"),
    "accommodation_cost": ("uebKosten_01", "/ja"),
    "hotel_breakfast_cost": ("uebKosten_02", "/ja"),
    "hotel_lunch_cost": ("uebKosten_03", "/ja"),
    "hotel_dinner_cost": ("uebKosten_04", "/ja"),
    "service_package_cost": ("servicePauschale", "/ja"),
    "declined_free_meals": ("kk12", "/ja"),
}

PRIVATE_CAR_REASONS = {"no-valid-reason": "/1", "valid-reason": "/2", "field-service": "/3", "bicycle": "/4"}
LOCATIONS = {"home-a": "/Wohnunga", "home-b": "/Wohnungb", "office": "/Dienststelle "}

RADIO_OPTIONS = {
    "application_kind": (
        "Beantragung",
        {"business-trip": "/Dienstreise   ", "training-trip": "/Aus-/Fortbildungsreise   ", "it-trip": "/IT-Reise    "},
    ),
    "employment_status": (
        "beruf",
        {
            "civil-servant-or-judge": "/Beamter/Richter ",
            "employee": "/Arbeitnehmer  ",
            "temporary-civil-servant": "/Beamter a.W.",
            "trainee": "/Auszubildender ",
        },
    ),
    "travel_start_from": ("hr", {**LOCATIONS, "temporary-address": "/voruebergehender Aufenthaltsort1"}),
    "travel_end_at": ("rr", {**LOCATIONS, "temporary-address": "/voruebergehender Aufenthaltsort2"}),
    "hotel_invoice_addressed_to": ("raapd", {"private": "/privat", "office": "/dienst"}),
    "shared_room_with_non_entitled_person": ("nebp", {True: "/ja", False: "/nein"}),
    "meals_initiated_by_employer": ("veranlassung", {True: "/ja", False: "/nein"}),
    "participated_in_bonus_program": ("bonus", {True: "/ja", False: "/nein"}),
    "outbound_private_car_reason": ("km", PRIVATE_CAR_REASONS),
    "business_private_car_reason": ("km2", PRIVATE_CAR_REASONS),
    "return_private_car_reason": ("km4", PRIVATE_CAR_REASONS),
}

PRIVATE_STAY_TIMING = {"before": ("kk33a", "/vor"), "after": ("kk33b", "/nach"), "during": ("kk33c", "/waehrend")}
FREE_ACCOMMODATION = {"official": "kk141", "private": "kk142"}
HOTEL_INCLUDED_MEALS = {"breakfast": "inclFrueh", "lunch": "inclMittag", "dinner": "inclAbend"}

# Nebenkosten: two (kind, amount) rows per section of the journey.
INCIDENTAL_FIELDS = {
    "outbound": (("nk1", "nk3"), ("nk2", "nk4")),
    "business": (("nk5", "nk7"), ("nk6", "nk8")),
    "return": (("nk9", "nk11"), ("nk10", "nk12")),
}
MEAL_DAY_FIELDS = tuple((f"dat{day}", f"kk{day}a", f"kk{day}b", f"kk{day}c") for day in range(5, 12))
HOTEL_EXTRA_FIELDS = (
    ("servicePauschBez_02", "servicePauschBetr_02", "anznaechte_07"),
    ("servicePauschBez_03", "servicePauschBetr_03", "anznaechte_08"),
)

ENV_DEFAULTS = {**business_trip.ENV_DEFAULTS, "personnel_number": "PERSONALNUMMER", "email": "EMAIL"}

# Taken over from the trip's Dienstreiseantrag (same argument names there).
PREFILL_TEXT = (
    "service_unit",
    "applicant_name",
    "department",
    "office_phone",
    "home_address",
    "additional_home_address",
    "temporary_stay_address",
    "iban",
    "bic",
    "bank_name",
    "destination",
    "purpose",
    "departure_date",
    "departure_time",
    "business_start_date",
    "business_start_time",
    "business_end_date",
    "business_end_time",
    "return_date",
    "return_time",
    "private_stay_from",
    "private_stay_until",
    "private_stay_destination",
)
PREFILL_RADIO = ("application_kind", "employment_status", "travel_start_from", "travel_end_at")

# Never derivable from receipts or tickets: (arguments, question).
REQUIRED_ANSWERS = (
    (
        ("free_meals",),
        "Which meals were provided free of charge, per day (breakfast/lunch/dinner)? Count conference "
        "catering, meals included in a participation fee, in-flight meals and breakfast included in the "
        "hotel rate. Pass [] if none.",
    ),
    (
        ("business_start_date", "business_start_time", "business_end_date", "business_end_time"),
        "When did the business (meeting/conference) actually start and end? The DRA only has the planned times.",
    ),
    (
        ("uses_personal_travel_card",),
        "Did you use a personal season ticket (Deutschlandticket/Jobticket, BahnCard 100) for any leg?",
    ),
    (
        ("ticket_provided_by_travel_office",),
        "Were tickets booked and provided by the travel-cost office (Reisekostenstelle)?",
    ),
    (
        ("participated_in_bonus_program",),
        "Did you collect bonus points (e.g. bahn.bonus, Miles & More) on this trip? If yes, which program?",
    ),
    (
        ("free_accommodation",),
        "Was free accommodation provided: 'official' (by the host/organizer), 'private' (friends/family), or 'none'?",
    ),
    (
        ("private_stay",),
        "Did you combine the trip with a private stay or vacation? If yes: from, until, where, and before/after/during the trip.",
    ),
    (
        ("used_private_car",),
        "Did you use a private car or bicycle for any leg? If yes: km per leg and the reason category.",
    ),
    (
        ("advance_received",),
        "Did you receive an advance payment (Abschlag)? Amount in EUR, '0' if none.",
    ),
    (
        ("email_consent",),
        "May the travel-cost office send the settlement notice to your office email address?",
    ),
)

PRIVATE_STAY_ARGUMENTS = ("private_stay_from", "private_stay_until", "private_stay_destination", "private_stay_timing")
LEGS = ("outbound", "business", "return")

# Follow-up questions: (still needed?, arguments, question).
CONDITIONAL_ANSWERS = (
    (
        lambda answers: bool(answers.get("free_meals")) and answers.get("meals_initiated_by_employer") is None,
        ("meals_initiated_by_employer",),
        "Were the free meals provided by or at the instigation of the employer (e.g. conference fee paid by "
        "the institute), or by an unrelated third party?",
    ),
    (
        lambda answers: _has_hotel_costs(answers) and answers.get("shared_room_with_non_entitled_person") is None,
        ("shared_room_with_non_entitled_person",),
        "Did you share the hotel room with a person who is not entitled to reimbursement?",
    ),
    (
        lambda answers: _has_hotel_costs(answers) and answers.get("hotel_invoice_addressed_to") is None,
        ("hotel_invoice_addressed_to",),
        "Is the hotel invoice addressed to you privately ('private') or to the institute ('office')? Usually visible on the invoice.",
    ),
    (
        lambda answers: bool(answers.get("private_stay"))
        and any(answers.get(name) is None for name in PRIVATE_STAY_ARGUMENTS),
        PRIVATE_STAY_ARGUMENTS,
        "Private stay details: from, until, where, and whether it was before, after or during the trip.",
    ),
    (
        lambda answers: bool(answers.get("used_private_car"))
        and not any(answers.get(f"{leg}_private_car_km") and answers.get(f"{leg}_private_car_reason") for leg in LEGS),
        tuple(f"{leg}_private_car_{field}" for leg in LEGS for field in ("km", "reason")),
        "Private car/bicycle: km and reason category for each leg it was used on (outbound/business/return).",
    ),
)

HOTEL_COST_ARGUMENTS = ("accommodation_total_cost", "accommodation_cost")

INFERABLE_FROM_RECEIPTS = (
    "ticket, reservation and luggage costs per leg (train/flight tickets)",
    "actual departure and return dates/times and means of transport (tickets, boarding passes)",
    "hotel name, place, check-in/out, nights, amounts, meals included in the rate (hotel invoice)",
    "incidental costs with receipts (conference fee, local transport, parking, visa, ...)",
)


class MealDay(BaseModel):
    """Meals provided free of charge on one day."""

    date: str
    breakfast: bool = False
    lunch: bool = False
    dinner: bool = False


class IncidentalCost(BaseModel):
    """One Nebenkosten line; at most two per section."""

    section: Literal["outbound", "business", "return"]
    kind: str
    amount: str


class HotelExtra(BaseModel):
    """Further position on the hotel invoice, e.g. city tax, parking, Wi-Fi."""

    name: str
    amount: str
    nights: str | None = None


def read_application(path: Path) -> dict:
    """Read the values of a filled Dienstreiseantrag back into argument names."""
    fields = PdfReader(path).get_fields() or {}
    values = {}
    for argument in PREFILL_TEXT:
        value = (fields.get(business_trip.TEXT_FIELDS[argument]) or {}).get("/V")
        if value:
            values[argument] = str(value)
    for argument in PREFILL_RADIO:
        pdf_field, choices = business_trip.RADIO_OPTIONS[argument]
        field = fields.get(pdf_field) or {}
        states = [state for state in field.get("/_States_", []) if state != "/Off"]
        if field.get("/V") in states:
            index = states.index(field["/V"]) + 1
            values[argument] = next(choice for choice, number in choices.items() if number == index)
    return values


def _has_hotel_costs(answers: dict) -> bool:
    return any(answers.get(name) for name in HOTEL_COST_ARGUMENTS)


def missing_answers(answers: dict) -> list[dict]:
    """The must-ask questions whose arguments are still None."""
    missing = []
    for arguments, question in REQUIRED_ANSWERS:
        absent = [name for name in arguments if answers.get(name) is None]
        if absent:
            missing.append({"arguments": absent, "question": question})
    for still_needed, arguments, question in CONDITIONAL_ANSWERS:
        if still_needed(answers):
            missing.append({"arguments": list(arguments), "question": question})
    return missing


def _application_values(folder: Path) -> tuple[Path | None, dict]:
    application = latest_pdf(folder, APPLICATION_PREFIX)
    return application, read_application(application) if application else {}


def prepare_travel_cost_report(trip_folder: str) -> dict:
    """Collect what is known for the travel-cost report and what must be asked.

    Returns the values read from the trip's Dienstreiseantrag, the receipt files
    to read (tickets, invoices), and the questions the user must answer because
    no receipt can answer them.
    """
    folder = resolve_trip_folder(trip_folder)
    application, prefill = _application_values(folder)
    receipts = folder / "receipts"
    return {
        "trip_folder": str(folder),
        "application_pdf": str(application) if application else None,
        "prefill_from_application": prefill,
        "receipt_files": sorted(str(path) for path in receipts.rglob("*") if path.is_file()) if receipts.is_dir() else [],
        "infer_from_receipts": list(INFERABLE_FROM_RECEIPTS),
        "open_questions": missing_answers({}),
        "follow_up_questions": [question for _, _, question in CONDITIONAL_ANSWERS],
    }


def create_travel_cost_report(
    trip_folder: str,
    free_meals: list[MealDay] | None = None,
    business_start_date: str | None = None,
    business_start_time: str | None = None,
    business_end_date: str | None = None,
    business_end_time: str | None = None,
    uses_personal_travel_card: bool | None = None,
    ticket_provided_by_travel_office: bool | None = None,
    participated_in_bonus_program: bool | None = None,
    free_accommodation: Literal["none", "official", "private"] | None = None,
    private_stay: bool | None = None,
    used_private_car: bool | None = None,
    advance_received: str | None = None,
    email_consent: bool | None = None,
    meals_initiated_by_employer: bool | None = None,
    shared_room_with_non_entitled_person: bool | None = None,
    hotel_invoice_addressed_to: Literal["private", "office"] | None = None,
    application_kind: Literal["business-trip", "training-trip", "it-trip"] | None = None,
    employment_status: Literal["civil-servant-or-judge", "employee", "temporary-civil-servant", "trainee"] | None = None,
    service_unit: str | None = None,
    applicant_name: str | None = None,
    personnel_number: str | None = None,
    department: str | None = None,
    office_phone: str | None = None,
    home_address: str | None = None,
    additional_home_address: str | None = None,
    temporary_stay_address: str | None = None,
    iban: str | None = None,
    bic: str | None = None,
    bank_name: str | None = None,
    destination: str | None = None,
    purpose: str | None = None,
    departure_date: str | None = None,
    departure_time: str | None = None,
    travel_start_from: Literal["home-a", "home-b", "office", "temporary-address"] | None = None,
    outbound_transport: str | None = None,
    outbound_scheduled_departure: str | None = None,
    outbound_local_transport: str | None = None,
    outbound_ticket_cost: str | None = None,
    outbound_reservation_cost: str | None = None,
    outbound_luggage_cost: str | None = None,
    outbound_local_transport_cost: str | None = None,
    outbound_private_car_km: str | None = None,
    outbound_private_car_reason: Literal["no-valid-reason", "valid-reason", "field-service", "bicycle"] | None = None,
    further_business_locations: str | None = None,
    business_address: str | None = None,
    trips_at_business_location: str | None = None,
    business_local_transport_cost: str | None = None,
    business_private_car_km: str | None = None,
    business_private_car_reason: Literal["no-valid-reason", "valid-reason", "field-service", "bicycle"] | None = None,
    return_transport: str | None = None,
    return_scheduled_departure: str | None = None,
    return_date: str | None = None,
    return_time: str | None = None,
    travel_end_at: Literal["home-a", "home-b", "office", "temporary-address"] | None = None,
    return_local_transport: str | None = None,
    return_ticket_cost: str | None = None,
    return_reservation_cost: str | None = None,
    return_luggage_cost: str | None = None,
    return_local_transport_cost: str | None = None,
    return_private_car_km: str | None = None,
    return_private_car_reason: Literal["no-valid-reason", "valid-reason", "field-service", "bicycle"] | None = None,
    incidental_costs: list[IncidentalCost] | None = None,
    hotel_name: str | None = None,
    hotel_location: str | None = None,
    hotel_from: str | None = None,
    hotel_until: str | None = None,
    accommodation_total_cost: str | None = None,
    accommodation_total_nights: str | None = None,
    accommodation_total_includes: list[Literal["breakfast", "lunch", "dinner"]] | None = None,
    accommodation_cost: str | None = None,
    accommodation_nights: str | None = None,
    hotel_breakfast_cost: str | None = None,
    hotel_breakfast_count: str | None = None,
    hotel_lunch_cost: str | None = None,
    hotel_lunch_count: str | None = None,
    hotel_dinner_cost: str | None = None,
    hotel_dinner_count: str | None = None,
    service_package_name: str | None = None,
    service_package_cost: str | None = None,
    service_package_nights: str | None = None,
    hotel_extras: list[HotelExtra] | None = None,
    declined_free_meals: str | None = None,
    declined_free_meals_reason: str | None = None,
    free_meals_occasion: str | None = None,
    free_accommodation_nights: str | None = None,
    bonus_program_name: str | None = None,
    private_stay_from: str | None = None,
    private_stay_until: str | None = None,
    private_stay_destination: str | None = None,
    private_stay_timing: Literal["before", "after", "during"] | None = None,
    other_notes: str | None = None,
    email: str | None = None,
    report_date: str | None = None,
    sign: bool | None = None,
) -> dict[str, str]:
    """Fill the trip's copy of the travel-cost report and save it in the trip folder."""
    load_dotenv(ENV_FILE)
    values = locals().copy()
    values.pop("trip_folder")
    values.pop("sign")

    missing = missing_answers(values)
    if missing:
        lines = "\n".join(f"- {', '.join(item['arguments'])}: {item['question']}" for item in missing)
        raise ValueError(f"Ask the user before filling the travel-cost report:\n{lines}")

    folder = resolve_trip_folder(trip_folder)
    template = trip_form(folder, TRAVEL_COST_REPORT_FORM)
    if template is None:
        raise FileNotFoundError(
            f"{folder / 'templates' / TRAVEL_COST_REPORT_FORM} is missing. Set DRA_TEMPLATE_DIR to the "
            "shared template folder before creating the Dienstreiseantrag."
        )

    _, prefill = _application_values(folder)
    for argument, value in prefill.items():
        if values.get(argument) is None:
            values[argument] = value
    for argument, variable in ENV_DEFAULTS.items():
        if values.get(argument) is None:
            values[argument] = os.getenv(variable)
    if not values["private_stay"]:
        for argument in PRIVATE_STAY_ARGUMENTS:
            values[argument] = None
    if not values["participated_in_bonus_program"]:
        values["bonus_program_name"] = None

    check_trip_order(values)

    signature_image = resolve_signature(sign)
    if signature_image is not None and values["report_date"] is None:
        values["report_date"] = date.today().isoformat()

    form_values = _form_values(values)
    output = folder / f"{TRAVEL_COST_REPORT_PREFIX}{folder.name}.pdf"
    assert_outside_templates(output)
    signature = (SIGNATURE_FIELD, signature_image) if signature_image is not None else None
    result = fill_pdf_form(template, output, form_values, signature)
    return {"trip_folder": str(folder), "travel_cost_report_pdf": str(result)}


def _form_values(values: dict) -> dict[str, str | NameObject]:
    form_values: dict[str, str | NameObject] = {}

    for argument, pdf_field in TEXT_FIELDS.items():
        value = values.get(argument)
        if value is not None:
            form_values[pdf_field] = format_date(value) if argument in DATE_FIELDS else str(value)

    for argument, boxes in CHECKBOX_FIELDS.items():
        selected = values.get(argument)
        if selected is not None:
            for pdf_field, state in boxes:
                form_values[pdf_field] = NameObject(state if selected else "/Off")

    for argument, (pdf_field, state) in AMOUNT_CHECKBOXES.items():
        if values.get(argument):
            form_values[pdf_field] = NameObject(state)

    for argument, (pdf_field, choices) in RADIO_OPTIONS.items():
        selected = values.get(argument)
        if selected is not None:
            form_values[pdf_field] = NameObject(choices[selected])

    if values.get("hotel_invoice_addressed_to") is None:
        # The blank template comes with "auf die Dienststelle" pre-ticked.
        form_values["raapd"] = NameObject("/Off")
    if any(values.get(name) for name in ("accommodation_cost", "hotel_breakfast_cost", "hotel_lunch_cost", "hotel_dinner_cost")):
        form_values["ausgewUebernKosten"] = NameObject("/seperat")
    for meal in values.get("accommodation_total_includes") or []:
        form_values[HOTEL_INCLUDED_MEALS[meal]] = NameObject("/ja")

    hotel_extras = [HotelExtra.model_validate(item) for item in values.get("hotel_extras") or []]
    if len(hotel_extras) > len(HOTEL_EXTRA_FIELDS):
        raise ValueError(f"The form has room for {len(HOTEL_EXTRA_FIELDS)} further hotel positions; put the rest in other_notes.")
    if hotel_extras:
        form_values["weiterePos"] = NameObject("/ja")
    for extra, (name_field, amount_field, nights_field) in zip(hotel_extras, HOTEL_EXTRA_FIELDS):
        form_values[name_field] = extra.name
        form_values[amount_field] = extra.amount
        if extra.nights is not None:
            form_values[nights_field] = extra.nights

    free_meals = [MealDay.model_validate(item) for item in values["free_meals"]]
    if len(free_meals) > len(MEAL_DAY_FIELDS):
        raise ValueError(f"The form has room for {len(MEAL_DAY_FIELDS)} days of free meals; put the rest in other_notes.")
    form_values["verpflegung"] = NameObject("/ja" if free_meals else "/nein")
    for meal_day, (date_field, breakfast_field, lunch_field, dinner_field) in zip(free_meals, MEAL_DAY_FIELDS):
        form_values[date_field] = business_trip.parse_date(meal_day.date).strftime("%d.%m.%y")
        for selected, pdf_field in ((meal_day.breakfast, breakfast_field), (meal_day.lunch, lunch_field), (meal_day.dinner, dinner_field)):
            form_values[pdf_field] = NameObject("/1" if selected else "/Off")

    if values["free_accommodation"] in FREE_ACCOMMODATION:
        form_values["kk14"] = NameObject("/Yes")
        form_values[FREE_ACCOMMODATION[values["free_accommodation"]]] = NameObject("/Yes")

    if values.get("private_stay_timing") is not None:
        pdf_field, state = PRIVATE_STAY_TIMING[values["private_stay_timing"]]
        form_values[pdf_field] = NameObject(state)

    incidental_costs = [IncidentalCost.model_validate(item) for item in values.get("incidental_costs") or []]
    for section, rows in INCIDENTAL_FIELDS.items():
        costs = [cost for cost in incidental_costs if cost.section == section]
        if len(costs) > len(rows):
            raise ValueError(f"The form has room for {len(rows)} incidental costs per section ({section}); put the rest in other_notes.")
        for cost, (kind_field, amount_field) in zip(costs, rows):
            form_values[kind_field] = cost.kind
            form_values[amount_field] = cost.amount

    return form_values


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill the travel-cost report (Reisekostenabrechnung) of a trip folder.")
    parser.add_argument("trip_folder", help="Trip folder path, or its name below DRA_TRIPS_DIR.")
    parser.add_argument(
        "answers",
        nargs="?",
        type=Path,
        help="JSON file with the arguments of create_travel_cost_report. Without it, print the open questions.",
    )
    arguments = parser.parse_args(argv)
    if arguments.answers is None:
        print(json.dumps(prepare_travel_cost_report(arguments.trip_folder), indent=2, ensure_ascii=False))
        return 0
    answers = json.loads(arguments.answers.read_text(encoding="utf-8"))
    result = create_travel_cost_report(arguments.trip_folder, **answers)
    print(f"Travel-cost report written to {result['travel_cost_report_pdf']}")
    open_file(Path(result["travel_cost_report_pdf"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
