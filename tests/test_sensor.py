"""Offline regression tests for State Update Monitor."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.state_update_monitor import sensor
from custom_components.state_update_monitor.const import DOMAIN


@pytest.fixture
async def platform(tmp_path):
    """Set up the platform with a local HA event loop and a captured timer."""
    hass = HomeAssistant(str(tmp_path))
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("renamed", [False, True])
async def test_monitor_events_are_excluded_by_registry_identity(platform, renamed):
    """Own events stay excluded after renames, while other sensors still count."""
    hass, entities, refresh = platform
    registry = er.async_get(hass)
    own_ids = []
    for entity in entities:
        registered = registry.async_get_or_create(
            "sensor", DOMAIN, entity.unique_id, suggested_object_id=entity.name
        )
        own_ids.append(registered.entity_id)
    if renamed:
        for index, entity_id in enumerate(own_ids):
            own_ids[index] = registry.async_update_entity(
                entity_id, new_entity_id=f"sensor.renamed_monitor_{index}"
            ).entity_id
    collector = hass.data[DOMAIN]["test_entry"]
    with patch.object(sensor.time, "monotonic", return_value=0):
        for entity_id in own_ids + ["sensor.external"]:
            hass.states.async_set(entity_id, "1")
        await hass.async_block_till_done()
        stats = collector.get_stats(15)
    assert stats["total_events"] == 1
    assert stats["top_entities"][0]["entity_id"] == "sensor.external"
    # Even continued changes of the monitor's attributes cannot sustain activity.
    with patch.object(sensor.time, "monotonic", return_value=11):
        for entity_id in own_ids:
            hass.states.async_set(entity_id, "1", {"total_events_in_window": 1})
        await hass.async_block_till_done()
        await refresh(None)
        assert collector.get_stats(15)["total_events"] == 0


@pytest.mark.asyncio
async def test_total_rate_supports_measurement_statistics(platform):
    """The changing rate opts into min/max/mean sensor statistics."""
    _, entities, _ = platform
    assert entities[0].state_class is SensorStateClass.MEASUREMENT
    assert entities[0].native_unit_of_measurement == "events/min"
