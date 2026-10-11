# State Update Monitor

## TL;DR

- **Find the entities flooding Home Assistant with updates.** It counts state changes per entity over the last few minutes and ranks the busiest ones.
- **Two sensors:** the total update rate (graph it, or alert on spikes) and the single busiest entity, with the full top list as an attribute.
- **Useful when Home Assistant feels sluggish**, the database grows fast, or Node-RED is busy, and you want to know which sensor is to blame.
- **Runs inside Home Assistant**, with no tokens or extra connections, and is set up entirely in the UI.

A custom component that watches Home Assistant's `state_changed` event
bus directly (no external WebSocket connection needed, since it runs
inside HA) and tracks which entities are generating the most updates
over a sliding time window. Configured entirely through the UI.

## Install

1. Copy the `custom_components/state_update_monitor` folder into your
   HA config directory, so you end up with:
   ```
   /config/custom_components/state_update_monitor/__init__.py
   /config/custom_components/state_update_monitor/const.py
   /config/custom_components/state_update_monitor/config_flow.py
   /config/custom_components/state_update_monitor/manifest.json
   /config/custom_components/state_update_monitor/sensor.py
   /config/custom_components/state_update_monitor/translations/en.json
   /config/custom_components/state_update_monitor/brand/icon.png
   /config/custom_components/state_update_monitor/brand/icon@2x.png
   /config/custom_components/state_update_monitor/brand/logo.png
   /config/custom_components/state_update_monitor/brand/logo@2x.png
   ```
   (Use the Samba, File Editor, or Studio Code Server add-on to copy
   files into `/config`.)

   The `brand/` folder gives the integration its own icon on the
   integrations page instead of the generic default — this only works
   on HA 2026.3 and newer, which serves local brand images directly
   from the integration folder (no submission to the external brands
   repo needed).

2. Restart Home Assistant (new custom components need a full restart
   before they show up in the integrations list).

3. Go to **Settings -> Devices & Services -> Add Integration**, search
   for **"State Update Monitor"**, and fill in the form:
   - **Sliding window (seconds)** — how far back to count events (default 300)
   - **Number of entities to rank** — top-N size for the ranked list (default 15)
   - **Sensor refresh interval (seconds)** — how often the sensors update (default 30)
   - **Exclude domains** — comma-separated, e.g. `persistent_notification`
   - **Only include entities matching** — comma-separated substrings,
     e.g. `bermuda, distance` to scope it to just your BLE sensors.
     Leave blank to track everything.

4. To change any of these later without removing and re-adding it, go
   to **Settings -> Devices & Services -> State Update Monitor -> Configure**.

Only one instance is supported (it watches the whole event bus by
design) — use the include/exclude fields to scope it rather than
adding a second instance.

## What you get

Both sensors are grouped under one "State Update Monitor" device card:

- **`sensor.state_update_monitor_total_rate`** — total state-change
  events per minute (within your filters, if set). Numeric, so it
  graphs cleanly in History/Statistics for correlating spikes with
  Node-RED CPU or anything else.

- **`sensor.state_update_monitor_busiest_entity`** — state is the
  single busiest entity_id right now. The `top_entities` attribute
  holds the full ranked list, e.g.:
  ```yaml
  top_entities:
    - entity_id: sensor.leela_distance
      count: 42
      per_minute: 8.4
    - entity_id: sensor.primrose_bps_zone
      count: 38
      per_minute: 7.6
  ```

## Example: alert when something goes unusually noisy

```yaml
automation:
  - alias: "Notify on entity update storm"
    trigger:
      - platform: numeric_state
        entity_id: sensor.state_update_monitor_total_rate
        above: 200   # tune this to your normal baseline
        for:
          minutes: 2
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "HA event volume spike"
          message: >
            Busiest entity: {{ state_attr('sensor.state_update_monitor_busiest_entity', 'top_entities')[0] }}
```

## Notes

- The include/exclude filters match as case-insensitive substrings
  against the full entity_id.
- Because this listens on the event bus directly rather than opening
  a second WebSocket connection, it has lower overhead than an
  external Python script and needs no token or network config.
- `window_seconds` is a rolling window recomputed on every
  `update_interval` tick, not a fixed historical bucket.
- On very recent Home Assistant versions you may see a harmless
  deprecation warning in the log about the options flow's
  `config_entry` attribute — this is a framework transition notice,
  not a functional problem, and doesn't affect the sensors.
