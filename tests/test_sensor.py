"""Offline regression tests for State Update Monitor."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from homeassistant.core import Event, HomeAssistant

from custom_components.state_update_monitor import sensor
from custom_components.state_update_monitor.const import DOMAIN


@pytest.fixture
async def platform(tmp_path):
    """Set up the platform with a local HA event loop and a captured timer."""
    hass = HomeAssistant(str(tmp_path))
    unload = []
    entry = SimpleNamespace(
        entry_id="test_entry",
        data={"window_seconds": 10, "top_n": 15, "update_interval": 600},
        options={},
        async_on_unload=unload.append,
    )
    entities = []
    with patch.object(sensor, "async_track_time_interval") as timer:
        await sensor.async_setup_entry(hass, entry, entities.extend)
        refresh = timer.call_args.args[1]
    yield hass, entities, refresh
    for remove in reversed(unload):
        remove()
    await hass.async_block_till_done()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled", [(True, False), (False, True), (False, False), (True, True)]
)
async def test_refresh_only_added_entities_and_prunes(platform, enabled):
    """Rejected entities cannot break refresh or expiry, including with neither added."""
    hass, entities, refresh = platform
    collector = hass.data[DOMAIN]["test_entry"]
    with patch.object(sensor.time, "monotonic", return_value=0):
        collector._handle_event(Event("state_changed", {"entity_id": "sensor.external"}))
    for index, (entity, added) in enumerate(zip(entities, enabled)):
        entity.entity_id = f"sensor.monitor_{index}"
        entity.async_write_ha_state = Mock()
        if added:
            entity.hass = hass
            await entity.async_added_to_hass()
    with patch.object(sensor.time, "monotonic", return_value=11):
        await refresh(None)
    await hass.async_block_till_done()
    assert not collector._events
    for entity, added in zip(entities, enabled):
        assert entity.async_write_ha_state.call_count == int(added)
    # Entity removal must disconnect the update subscription.
    for entity, added in zip(entities, enabled):
        if added:
            await entity.async_remove(force_remove=True)
            entity.async_write_ha_state.reset_mock()
    await refresh(None)
    for entity in entities:
        entity.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_ingestion_prunes_before_long_refresh_interval(platform):
    """A short window is respected during traffic, even between timer ticks."""
    hass, _, _ = platform
    collector = hass.data[DOMAIN]["test_entry"]
    with patch.object(sensor.time, "monotonic", return_value=0):
        collector._handle_event(Event("state_changed", {"entity_id": "sensor.old"}))
    with patch.object(sensor.time, "monotonic", return_value=11):
        collector._handle_event(Event("state_changed", {"entity_id": "sensor.new"}))
    assert list(collector._events) == [(11, "sensor.new")]


@pytest.mark.asyncio
async def test_refresh_computes_one_shared_snapshot(platform):
    """Both added sensors consume the same single scan of the window."""
    hass, entities, refresh = platform
    collector = hass.data[DOMAIN]["test_entry"]
    for index, entity in enumerate(entities):
        entity.entity_id = f"sensor.monitor_{index}"
        async def update_ha_state(_force, entity=entity):
            await entity.async_update()
            entity.async_write_ha_state()

        entity.async_update_ha_state = update_ha_state
        entity.hass = hass
        entity.async_write_ha_state = Mock()
        await entity.async_added_to_hass()
    with patch.object(collector, "get_stats", wraps=collector.get_stats) as stats:
        await refresh(None)
        await hass.async_block_till_done()
    stats.assert_called_once_with(15)
    assert (
        entities[0].extra_state_attributes["total_events_in_window"]
        == entities[1].extra_state_attributes["total_events_in_window"]
    )

