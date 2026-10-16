"""Sensor platform for State Update Monitor.

Tracks state_changed events on the HA event bus over a sliding time
window and exposes:

  - sensor.state_update_monitor_total_rate
        Total state-change events per minute, system-wide (or within
        the include/exclude filters), over the configured window.
        Numeric, so it graphs cleanly in History/Statistics.

  - sensor.state_update_monitor_busiest_entity
        State = the single busiest entity_id right now. The
        top_entities attribute holds a full ranked list with counts
        and per-minute rates.

Configured entirely through the UI (Settings -> Devices & Services ->
Add Integration -> State Update Monitor), and reconfigurable later via
that integration's "Configure" option.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, deque
from collections.abc import Callable
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_EXCLUDE_DOMAINS,
    CONF_INCLUDE_FILTER,
    CONF_TOP_N,
    CONF_UPDATE_INTERVAL,
    CONF_WINDOW_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _parse_list(value) -> list[str]:
    """Accept either a comma-separated string or a list, return a clean list."""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


class StateChangeCollector:
    """Maintains a sliding window of (timestamp, entity_id) events."""

    def __init__(
        self,
        hass: HomeAssistant,
        window_seconds: int,
        exclude_domains: list[str],
        include_filter: list[str],
        entry_id: str,
    ) -> None:
        self.hass = hass
        self.window_seconds = window_seconds
        self.exclude_domains = set(exclude_domains)
        self.include_filter = [f.lower() for f in include_filter]
        self._entity_registry = er.async_get(hass)
        self._own_unique_ids = (f"{entry_id}_total_rate", f"{entry_id}_busiest_entity")
        self._events: deque[tuple[float, str]] = deque()
        self._remove_listener = None
        self._subscribers: list[Callable[[dict], None]] = []

    def async_subscribe(self, update: Callable[[dict], None]) -> Callable[[], None]:
        """Subscribe an added entity to shared refresh snapshots."""
        self._subscribers.append(update)
        return lambda: self._subscribers.remove(update)

    def async_refresh(self, top_n: int) -> None:
        """Prune and publish one snapshot, even with no added entities."""
        stats = self.get_stats(top_n)
        for update in tuple(self._subscribers):
            update(stats)

    def start(self) -> None:
        self._remove_listener = self.hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._handle_event
        )

    def stop(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    @callback
    def _handle_event(self, event: Event) -> None:
        self._prune()
        entity_id = event.data.get("entity_id")
        if not entity_id:
            return
        if any(
            entity_id
            == self._entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            for unique_id in self._own_unique_ids
        ):
            return
        domain = entity_id.split(".", 1)[0]
        if domain in self.exclude_domains:
            return
        if self.include_filter and not any(
            term in entity_id.lower() for term in self.include_filter
        ):
            return
        self._events.append((time.monotonic(), entity_id))

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def get_stats(self, top_n: int) -> dict:
        self._prune()
        counts = Counter(entity_id for _, entity_id in self._events)
        total = sum(counts.values())
        window_minutes = self.window_seconds / 60 if self.window_seconds else 1
        top_entities = [
            {
                "entity_id": entity_id,
                "count": count,
                "per_minute": round(count / window_minutes, 2),
            }
            for entity_id, count in counts.most_common(top_n)
        ]
        return {
            "total_events": total,
            "total_per_minute": round(total / window_minutes, 2),
            "unique_entities": len(counts),
            "top_entities": top_entities,
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up State Update Monitor sensors from a config entry."""
    config = {**entry.data, **entry.options}

    window_seconds = int(config[CONF_WINDOW_SECONDS])
    top_n = int(config[CONF_TOP_N])
    update_interval = int(config[CONF_UPDATE_INTERVAL])
    exclude_domains = _parse_list(config.get(CONF_EXCLUDE_DOMAINS, ""))
    include_filter = _parse_list(config.get(CONF_INCLUDE_FILTER, ""))

    collector = StateChangeCollector(
        hass, window_seconds, exclude_domains, include_filter, entry.entry_id
    )
    collector.start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = collector

    async def _stop_collector(_event: Event) -> None:
        collector.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop_collector)
    )
    entry.async_on_unload(collector.stop)

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="State Update Monitor",
        manufacturer="Custom",
        model="Event Bus Watcher",
    )

    total_rate_sensor = StateUpdateTotalRateSensor(
        collector, top_n, window_seconds, entry.entry_id, device_info
    )
    top_entity_sensor = StateUpdateTopEntitySensor(
        collector, top_n, window_seconds, entry.entry_id, device_info
    )
    async_add_entities([total_rate_sensor, top_entity_sensor])

    async def _async_refresh(_now) -> None:
        collector.async_refresh(top_n)

    remove_interval = async_track_time_interval(
        hass, _async_refresh, timedelta(seconds=update_interval)
    )
    entry.async_on_unload(remove_interval)


class StateUpdateSensor(SensorEntity):
    """Subscribe to refreshes only while the entity is added to HA."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._collector.async_subscribe(self._handle_refresh))
        await self.async_update()

    @callback
    def _handle_refresh(self, stats: dict) -> None:
        self._update_stats(stats)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        self._update_stats(self._collector.get_stats(self._top_n))


class StateUpdateTotalRateSensor(StateUpdateSensor):
    """Total state-change events per minute, system-wide, over the window."""

    _attr_name = "State Update Monitor Total Rate"
    _attr_native_unit_of_measurement = "events/min"
    _attr_icon = "mdi:pulse"
    _attr_should_poll = False

    def __init__(
        self,
        collector: StateChangeCollector,
        top_n: int,
        window_seconds: int,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._collector = collector
        self._top_n = top_n
        self._window_seconds = window_seconds
        self._attr_unique_id = f"{entry_id}_total_rate"
        self._attr_device_info = device_info
        self._attr_native_value: float = 0
        self._attr_extra_state_attributes: dict = {}

    def _update_stats(self, stats: dict) -> None:
        self._attr_native_value = stats["total_per_minute"]
        self._attr_extra_state_attributes = {
            "window_seconds": self._window_seconds,
            "unique_entities": stats["unique_entities"],
            "total_events_in_window": stats["total_events"],
        }


class StateUpdateTopEntitySensor(StateUpdateSensor):
    """Current busiest entity_id, with a ranked top-N list as an attribute."""

    _attr_name = "State Update Monitor Busiest Entity"
    _attr_icon = "mdi:fire"
    _attr_should_poll = False

    def __init__(
        self,
        collector: StateChangeCollector,
        top_n: int,
        window_seconds: int,
        entry_id: str,
        device_info: DeviceInfo,
    ) -> None:
        self._collector = collector
        self._top_n = top_n
        self._window_seconds = window_seconds
        self._attr_unique_id = f"{entry_id}_busiest_entity"
        self._attr_device_info = device_info
        self._attr_native_value: str | None = None
        self._attr_extra_state_attributes: dict = {}

    def _update_stats(self, stats: dict) -> None:
        top = stats["top_entities"]
        self._attr_native_value = top[0]["entity_id"] if top else "none"
        self._attr_extra_state_attributes = {
            "window_seconds": self._window_seconds,
            "total_events_in_window": stats["total_events"],
            "top_entities": top,
        }
