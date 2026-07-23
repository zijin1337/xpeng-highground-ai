from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_IDS = {
    "scenario",
    "run-button",
    "step-button",
    "reset-button",
    "rainfall",
    "water-level",
    "secondary-water",
    "rise-rate",
    "sensor-confidence",
    "route-dry",
    "route-open",
    "occupants-clear",
    "charging-disconnected",
    "vehicle-healthy",
    "positioning-online",
    "network-online",
    "operator-online",
    "owner-authorized",
    "rain-output",
    "water-output",
    "water-2-output",
    "rise-output",
    "confidence-output",
    "decision-value",
    "permission-value",
    "risk-value",
    "confidence-value",
    "latest-start-value",
    "threshold-value",
    "decision-reason",
    "evidence-body",
    "event-id",
    "snapshot-hash",
    "action-permission",
    "event-time",
    "vehicle",
    "water",
    "water-line",
    "water-label",
    "scene-status-dot",
    "scene-status-text",
    "scene-desc",
    "play-migration-button",
    "animation-step",
    "animation-speed",
    "animation-progress",
    "animation-percent",
    "migration-route",
    "api-key-input",
    "api-connect-button",
    "api-status",
    "api-status-dot",
    "command-button",
}

FLEET_REQUIRED_IDS = {
    "view-fleet-tab",
    "view-single-tab",
    "fleet-shadow-view",
    "single-car-view",
    "fleet-source-label",
    "fleet-stage-label",
    "fleet-vehicle-count",
    "fleet-scheduled-count",
    "fleet-verify-count",
    "fleet-denied-count",
    "fleet-capacity-count",
    "fleet-map",
    "fleet-queue-body",
    "fleet-timeline",
    "fleet-evidence-body",
    "fleet-next-button",
    "fleet-reset-button",
    "fleet-api-key-input",
    "fleet-api-connect-button",
    "fleet-api-status",
    "fleet-run-id",
    "fleet-input-hash",
    "fleet-plan-hash",
}

SCENARIOS = {"normal", "rising", "conflict", "blocked", "occupant", "movingFault"}
FORM_INPUTS = {
    "rainfall",
    "water-level",
    "secondary-water",
    "rise-rate",
    "sensor-confidence",
    "route-dry",
    "route-open",
    "occupants-clear",
    "charging-disconnected",
    "vehicle-healthy",
    "positioning-online",
    "network-online",
    "operator-online",
    "owner-authorized",
}


class ContractParser(HTMLParser):
    VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict[str, object]] = []
        self.ids: list[str] = []
        self.form_inputs: set[str] = set()
        self.scenarios: set[str] = set()
        self.pipeline_items = 0
        self.api_status_scoped = False
        self.evidence_body_tag: str | None = None
        self.fleet_view_hidden: bool | None = None
        self.single_car_hidden: bool | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        classes = set((values.get("class") or "").split())
        ancestors = [*self.stack]
        if element_id:
            self.ids.append(element_id)
        if element_id in FORM_INPUTS and any(item["id"] == "control-form" for item in ancestors):
            self.form_inputs.add(element_id)
        if tag == "option" and any(item["id"] == "scenario" for item in ancestors):
            value = values.get("value")
            if value:
                self.scenarios.add(value)
        if tag == "li" and any("pipeline" in item["classes"] for item in ancestors):
            self.pipeline_items += 1
        if element_id == "api-status":
            self.api_status_scoped = any("api-state" in item["classes"] for item in ancestors)
        if element_id == "evidence-body":
            self.evidence_body_tag = tag
        if element_id == "fleet-shadow-view":
            self.fleet_view_hidden = "hidden" in values
        if element_id == "single-car-view":
            self.single_car_hidden = "hidden" in values
        if tag not in self.VOID_ELEMENTS:
            self.stack.append({"tag": tag, "id": element_id, "classes": classes})

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return


def parse_console() -> ContractParser:
    parser = ContractParser()
    parser.feed((REPO_ROOT / "index.html").read_text(encoding="utf-8"))
    return parser


def test_web_console_keeps_runtime_dom_contract() -> None:
    parser = parse_console()
    counts = Counter(parser.ids)

    assert REQUIRED_IDS | FLEET_REQUIRED_IDS <= counts.keys()
    assert not {element_id for element_id, count in counts.items() if count > 1}
    assert parser.form_inputs == FORM_INPUTS
    assert parser.scenarios == SCENARIOS
    assert parser.pipeline_items == 5
    assert parser.api_status_scoped
    assert parser.evidence_body_tag == "tbody"
    assert parser.fleet_view_hidden is False
    assert parser.single_car_hidden is True
