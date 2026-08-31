"""Home Assistant tools: states, service calls, automations, registries, logs."""

from __future__ import annotations

from datetime import UTC
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..context import Homelab
from ..errors import ToolError
from ..util import truncate


def _slim_state(entity: dict[str, Any]) -> dict[str, Any]:
    attrs = entity.get("attributes", {}) or {}
    return {
        "entity_id": entity.get("entity_id"),
        "state": entity.get("state"),
        "name": attrs.get("friendly_name"),
        "device_class": attrs.get("device_class"),
        "unit": attrs.get("unit_of_measurement"),
        "last_changed": entity.get("last_changed"),
    }


def register(mcp: FastMCP, lab: Homelab) -> None:
    ha = lab.ha
    limit = lab.config.server.max_output_bytes

    @mcp.tool()
    async def ha_states(
        domain: str | None = None, search: str | None = None, full: bool = False
    ) -> list[dict[str, Any]]:
        """List Home Assistant entity states.

        Args:
            domain: restrict to a domain, e.g. "light", "sensor", "automation".
            search: case-insensitive substring matched on entity_id and name.
            full: include all attributes (default false = a slim summary).
        """
        states = await ha.request("GET", "/states") or []
        if domain:
            states = [s for s in states if str(s.get("entity_id", "")).startswith(domain + ".")]
        if search:
            needle = search.lower()
            states = [
                s
                for s in states
                if needle in str(s.get("entity_id", "")).lower()
                or needle in str((s.get("attributes") or {}).get("friendly_name", "")).lower()
            ]
        states.sort(key=lambda s: s.get("entity_id", ""))
        return states if full else [_slim_state(s) for s in states]

    @mcp.tool()
    async def ha_get_state(entity_id: str) -> dict[str, Any]:
        """Full state and attributes of a single entity."""
        return await ha.request("GET", f"/states/{entity_id}")

    @mcp.tool()
    async def ha_call_service(
        domain: str, service: str, data: dict[str, Any] | None = None
    ) -> Any:
        """Call a Home Assistant service.

        Args:
            domain: service domain, e.g. "light", "switch", "climate", "homeassistant".
            service: service name, e.g. "turn_on", "toggle", "set_temperature".
            data: service payload, e.g. {"entity_id": "light.kuchyna", "brightness": 200}.
        """
        lab.audit.record("ha_call_service", domain=domain, service=service, data=data)
        return await ha.request("POST", f"/services/{domain}/{service}", json_body=data or {})

    @mcp.tool()
    async def ha_services(domain: str | None = None) -> Any:
        """Discover callable services, optionally filtered to one domain."""
        services = await ha.request("GET", "/services") or []
        if domain:
            return [s for s in services if s.get("domain") == domain]
        return [
            {"domain": s.get("domain"), "services": list((s.get("services") or {}).keys())}
            for s in services
        ]

    @mcp.tool()
    async def ha_areas() -> Any:
        """List areas (rooms/zones) from the area registry."""
        return await ha.ws_command({"type": "config/area_registry/list"})

    @mcp.tool()
    async def ha_devices(area: str | None = None) -> Any:
        """List devices from the device registry, optionally filtered by area id.

        This is how ESPHome nodes, Zigbee/Z-Wave devices and integrations appear
        as physical devices in Home Assistant.
        """
        devices = await ha.ws_command({"type": "config/device_registry/list"}) or []
        if area:
            devices = [d for d in devices if d.get("area_id") == area]
        return [
            {
                "id": d.get("id"),
                "name": d.get("name_by_user") or d.get("name"),
                "manufacturer": d.get("manufacturer"),
                "model": d.get("model"),
                "area_id": d.get("area_id"),
                "sw_version": d.get("sw_version"),
                "connections": d.get("connections"),
            }
            for d in devices
        ]

    @mcp.tool()
    async def ha_entities_registry(search: str | None = None) -> Any:
        """List the entity registry (entity_id -> device/area/platform mapping)."""
        entities = await ha.ws_command({"type": "config/entity_registry/list"}) or []
        if search:
            needle = search.lower()
            entities = [e for e in entities if needle in str(e.get("entity_id", "")).lower()]
        return [
            {
                "entity_id": e.get("entity_id"),
                "name": e.get("name") or e.get("original_name"),
                "platform": e.get("platform"),
                "device_id": e.get("device_id"),
                "area_id": e.get("area_id"),
                "disabled": bool(e.get("disabled_by")),
            }
            for e in entities
        ]

    @mcp.tool()
    async def ha_automation(entity_id: str, action: str = "trigger") -> Any:
        """Trigger, turn on, turn off or toggle an automation.

        Args:
            entity_id: the automation entity, e.g. "automation.kurnik_zatvor".
            action: trigger, turn_on, turn_off or toggle.
        """
        allowed = {"trigger", "turn_on", "turn_off", "toggle", "reload"}
        if action not in allowed:
            raise ToolError(f"unknown automation action {action!r}; use {sorted(allowed)}")
        lab.audit.record("ha_automation", entity_id=entity_id, action=action)
        if action == "reload":
            return await ha.request("POST", "/services/automation/reload", json_body={})
        return await ha.request(
            "POST", f"/services/automation/{action}", json_body={"entity_id": entity_id}
        )

    @mcp.tool()
    async def ha_render_template(template: str) -> str:
        """Render a Jinja2 template against live state (read-only introspection)."""
        result = await ha.request("POST", "/template", json_body={"template": template})
        return truncate(str(result), limit)

    @mcp.tool()
    async def ha_history(entity_id: str, hours: int = 24) -> Any:
        """State history for one entity over the last N hours."""
        from datetime import datetime, timedelta

        start = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        data = await ha.request(
            "GET",
            f"/history/period/{start}",
            params={"filter_entity_id": entity_id, "minimal_response": "true"},
        )
        return data

    @mcp.tool()
    async def ha_logbook(hours: int = 6, entity_id: str | None = None) -> Any:
        """Logbook entries (what happened) over the last N hours."""
        from datetime import datetime, timedelta

        start = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        params = {"entity": entity_id} if entity_id else None
        return await ha.request("GET", f"/logbook/{start}", params=params)

    @mcp.tool()
    async def ha_error_log() -> str:
        """The Home Assistant error log (home-assistant.log)."""
        return truncate(str(await ha.request("GET", "/error_log")), limit)

    @mcp.tool()
    async def ha_config_check() -> Any:
        """Validate the Home Assistant configuration (config/core/check_config)."""
        lab.audit.record("ha_config_check")
        return await ha.request("POST", "/config/core/check_config", json_body={})

    @mcp.tool()
    async def ha_api(
        method: str, path: str, params: dict[str, Any] | None = None, json_body: Any = None
    ) -> Any:
        """Call any Home Assistant REST endpoint below /api (escape hatch)."""
        lab.audit.record("ha_api", method=method, path=path)
        return await ha.request(method, path, params=params, json_body=json_body)

    @mcp.tool()
    async def ha_ws(payload: dict[str, Any]) -> Any:
        """Run any Home Assistant WebSocket command (escape hatch for registries etc.).

        Args:
            payload: the command object, e.g. {"type": "config/label_registry/list"}.
        """
        lab.audit.record("ha_ws", payload=payload)
        return await ha.ws_command(payload)
