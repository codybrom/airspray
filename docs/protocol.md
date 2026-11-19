# Protocol

The diffuser's Bluetooth LE protocol, reverse-engineered from the original
Android app (Air Wick 3.2.1, package `cn.xlink.airwick`) and checked against a real
diffuser. The first half is the wire format. The second half covers what the
original app does around it and where this project differs.

The original app has since been pulled from the app stores and its cloud
service shut down, so anything described here as coming from that service (the
identity handshake key, the refill estimate) can no longer be obtained.

The device is called **Reese** inside the original app. It advertises as
`Air Wick Mist` or `AIRWICK REESES`. Both names use one code path and no
behavioral difference between them is known.

Where something is marked **unknown**, the original app does not reveal it and it
has not been established on a device.

## GATT

| Item | Value |
| --- | --- |
| Service | `6e400001-b5a3-f393-e0a9-e50e24dcca9e` |
| Write characteristic | `6e400007-b5a3-f393-e0a9-e50e24dcca9e` |
| Notify characteristic | `6e400008-b5a3-f393-e0a9-e50e24dcca9e` |
| CCCD | `00002902-0000-1000-8000-00805f9b34fb`, written with "enable notification" |

- The diffuser refuses writes from a central it is not bonded with, and it only
  accepts a bond while in pairing mode (see the README's pairing section).
- The original app never requests an MTU, a connection priority, or a write type.
  It uses Android's default write and waits for the write callback before
  sending the next packet. Whether the characteristic needs write-with-response
  is **unknown**. Both work in practice (the Swift app writes without response,
  the Python client with).
- The original app does not parse advertisement or manufacturer data. Discovery is
  by the service UUID and the anchored name pattern
  `^(Air Wick Mist|AIRWICK REESES)$`.

## Framing

```txt
FF 55 CMD LEN PAYLOAD... CHK
```

- `LEN` is the payload length in bytes.
- `CHK = CMD ^ 0xAA ^ LEN ^ (XOR of every payload byte)`.
- There is no escaping and no multi-packet handling. A decoder should skip
  leading bytes until it finds `FF 55`.

Sample frames:

| Meaning | Bytes |
| --- | --- |
| Query status | `FF 55 01 00 AB` |
| Power on | `FF 55 06 01 01 AC` |
| Request identity | `FF 55 20 00 8A` |
| Debug on | `FF 55 0A 01 01 A0` |

## Commands

"App" is what the central sends. "Device" is what comes back on the notify
characteristic.

| Code | Name | App → device payload | Device → app payload |
| --- | --- | --- | --- |
| `0x00` | Query MCU firmware | none | 4 bytes, shown as `a.bb,c.dd` |
| `0x01` | Query status | none | 8 bytes or more, see [Status](#status) |
| `0x02` | Query schedule | `[slot 1..10]` | 8-byte schedule |
| `0x03` | Set or delete schedule | 8-byte schedule. Delete is `[slot, 0, 0, 0, 0, 0, 0, 0]` | 8-byte schedule echo |
| `0x04` | Clear all schedules | none | echo, and all ten slots are now empty |
| `0x05` | Set time | `[hour, minute, second, weekday]` | the same 4 bytes echoed |
| `0x06` | Set power | `[1]` on, `[0]` off | `[1]` or `[0]` |
| `0x07` | Set intensity | `[0..4]` | `[0..4]` |
| `0x08` | Set room and fragrance | `[room, fragrance]` | echo. The original app's decoder accepts room 1..8 and fragrance 1..31 |
| `0x09` | Boost (one immediate spray) | none | not decoded by the original app |
| `0x0A` | Set debug | `[1]` or `[0]` | `[0]` is sent unprompted after every status reply. Effect of enabling it is **unknown** |
| `0x0B` | Factory reset | none | not decoded, exact effect **unknown** |
| `0x10` | Hardware switch report | none | payload **unknown**, a constant only |
| `0x11` | Spray report (unsolicited) | none | `[1]` while spraying, otherwise `[0]` |
| `0x12` | Battery report (unsolicited) | none | `[0..4]` |
| `0x13` | Schedule status report (unsolicited) | none | one byte, same encoding as status byte 5 |
| `0x20` | Request identity | none | challenge, 13 bytes or more |
| `0x21` | Identity authentication | 15 bytes | any reply means success |
| `0x22` | Query BLE firmware | none | 4 bytes, shown as `a.bb,c.dd` |
| `0x40` | Set timeout | **unknown**, a constant only, never encoded | none |
| `0x41` | Set spray parameter | **unknown**, a constant only, never encoded | none |

The original app defines `0x04`, `0x09`, `0x0A` and `0x0B` but never calls them
from its UI. Boost and factory reset are known to work because this project
sends them.

## Status

Payload of the `0x01` reply. The original app only decodes it when at least 8
bytes are present.

| Byte | Meaning |
| --- | --- |
| 0 | Power: `1` on |
| 1 | Spraying: `1` while a spray is in progress |
| 2 | Intensity, `0..4` |
| 3 | Battery, `0..4` |
| 4 | **Unknown**, never read by the original app |
| 5 | Schedule info, `0..26`: high nibble is the status (greater than 0 means a schedule is running), low nibble is the slot |
| 6 | Room id (observed on a real diffuser, ignored by the original app) |
| 7 | Fragrance id (observed on a real diffuser, ignored by the original app) |

The original app discards intensity or battery outside `0..4` and schedule info
outside `0..26`. Nothing about faults, temperature, refill level or firmware is
in this payload. The device does not report refill level at all.

## Intensity

The wire value is `0..4`, and the diffuser has five states: paused, plus four
strengths. `0` pauses spraying without powering the diffuser down, and `1` to
`4` are four increasing strengths. This has been confirmed on a real diffuser.
This project labels them `0` Paused, `1` Low, `2` Medium, `3` High, `4` Max.

The diffuser acknowledges exactly those five values. A set-intensity command
with `0` to `4` is echoed back on `0x07`. A value of `5` or more gets no echo,
although the next status reply does report the out-of-range value as stored.
What the diffuser does while holding such a value is **unknown**, so nothing
should send one.

The original app treated the diffuser the same way. Its lowest setting was
paused, with four strengths above it. It did not show `0` to the user. In its
code the number on screen was the wire value plus one:

- The intensity dial held a value from 0 to 4, drew it as that value plus one,
  and passed the value to the set-intensity command (`0x07`).
- The schedule editor's slider ran from 1 to 5 and stored the slider position
  minus one.

So the bottom of the app's scale sent wire value `0`, which is the pause.

Powering the diffuser off and on was a separate command (`0x06`). The app's
older control screen sent it. No call to it was found in the newer control
screen.

The spray interval for each level, boost duration, and any cooldown are
**unknown**: the original app contains no timing data. The intervals this project
uses for its countdown are its own estimates.

## Battery

`0..4`. The original app shows its "battery level low, please replace batteries"
banner only at `0`. This project ignores a `0` report unless the previous level
was `0` or `1`, because the device sends spurious zeros. The words this project
uses for the levels are its own.

## Schedules

Ten slots, numbered 1 to 10, stored on the device and run while disconnected.

| Byte | Meaning |
| --- | --- |
| 0 | `name << 4 \| slot` |
| 1 | Enabled: `1` or `0` |
| 2 | Start hour |
| 3 | Start minute |
| 4 | End hour |
| 5 | End minute |
| 6 | Repeat byte, see below |
| 7 | Intensity, `0..4` |

Repeat byte:

| Bit | Meaning |
| --- | --- |
| `0x80` | Repeats weekly |
| `0x40` | Monday |
| `0x20` | Tuesday |
| `0x10` | Wednesday |
| `0x08` | Thursday |
| `0x04` | Friday |
| `0x02` | Saturday |
| `0x01` | Sunday |

- An empty slot is one whose start and end are all zero.
- Deleting a slot is a set with the slot number followed by seven zero bytes.
- How the device runs a schedule with the repeat bit clear is **unknown**. The
  original app clears it when no day is selected or its repeat switch is off.
- How the device answers a query for an empty slot has not been confirmed on a
  device. This project's loaders handle an all-zero reply, a reply with slot 0,
  and no reply.

Schedule names (the high nibble of byte 0):

| Id | Name |
| --- | --- |
| 1 | Start the Day |
| 2 | Midday Boost |
| 3 | Wake Up |
| 4 | Morning |
| 5 | Midday |
| 6 | Afternoon |
| 7 | Evening |
| 8 | Other (this project labels it "Custom") |

## Time

`0x05` carries `[hour, minute, second, weekday]` in local time, with weekday
`0` for Sunday through `6` for Saturday. There is no timezone on the wire. The
original app sends it on every connect.

## Rooms

| Id | Name |
| --- | --- |
| 1 | Bathroom |
| 2 | Bedroom |
| 3 | Dining Room |
| 4 | Foyer/Entryway |
| 5 | Kitchen |
| 6 | Living Room |
| 7 | Other |
| 8 | Office |

## Fragrances

| Id | Name | Id | Name |
| --- | --- | --- | --- |
| 1 | Aroma Unwind | 18 | Sandalwood |
| 2 | Aroma Happiness | 19 | Pumpkin Spice |
| 3 | Aroma Refresh | 20 | Fresh Pine & Juniper |
| 4 | Aroma Sleep | 21 | Apple Cinnamon Medley |
| 5 | Apple & Cinnamon | 22 | Cinnamon & Crisp Apple |
| 6 | Eucalyptus | 23 | Brown Sugar & Vanilla |
| 7 | Fresh Cucumber | 24 | Vanilla & Caramel |
| 8 | Fresh Waters | 25 | Aroma Rejuvenate |
| 9 | Lavender & Almond Blossom | 26 | Blue Agave |
| 10 | Lemon Thyme | 27 | Vanilla Pink Papaya |
| 11 | Mandarin & Sweet Tangerine | 28 | Cotton & Spring Lilac |
| 12 | Peony & Jasmine | 29 | Lush Honeysuckle & Raspberry |
| 13 | Sweet Fruit & Nutmeg | 30 | Hibiscus & Bloom Orchids |
| 14 | Sweet Melon & Vanilla | 31 | Bonfire & Crisp Fall Air |
| 15 | Wild Mint | 32 | Aroma Relax |
| 16 | Woodland Pine | 33 | Linen Petals |
| 17 | Coconut and Pineapple | | |

Notes from the original app:

- Its picker omits id 3 (Aroma Refresh), and its decoder only accepts an echo
  of ids 1 to 31, so 32 and 33 can be set but are not confirmed back.
- Six fragrances have subtitles:
  - 1: Jasmine, Green Tea and Lotus Blossom
  - 2: Pineapple, Peach and Wild Mint
  - 3: Honeydew, Sage and Basil
  - 4: Lavender, Eucalyptus and Sage
  - 25: Bamboo, Bergamot and Cardamom
  - 32: Comforting Orange, Rosemary and Blackberry

The status reply carries the stored room and fragrance in bytes 6 and 7. The
original app did not read them there and relied on the echo of the set command.
This project does not read them from the status yet either.

## Identity handshake (`0x20` / `0x21`)

The original app ran this once, during first-time setup, and never on later
connects. This project does not perform it, and the diffuser works without it.
Whether skipping it has any device-side effect is **unknown**.

It is recorded here for completeness. It can no longer be performed, because
step 4 needed a key from the app's cloud service, and that service has been
shut down.

1. The app wrote `FF 55 20 00 8A`.
2. The device notified `0x20` with a challenge of at least 13 payload bytes.
3. The app built two buffers from the challenge payload `P`:
   - `content`, 16 bytes: `P[0..12]` followed by three zero bytes.
   - `mask`, 15 bytes: `P[0..8]` followed by the device's 6 MAC address bytes in
     address order.
4. The app fetched a per-device key from its cloud service, looked up by the
   diffuser's MAC address.
5. The app replied with `0x21` and a 15-byte payload:
   `AES-128-ECB-NoPadding(content, key)`, XORed byte by byte with `mask`, cut to
   15 bytes. (The app named the XOR step "CTR", but it was a plain XOR.)
6. Any `0x21` notification from the device counted as success. The app gave up
   after 20 seconds.

The key only ever existed on that service. Separately, macOS does not expose a
peripheral's MAC address to apps, so a Mac could not have built the reply even
with the key.

## Original app behavior

What the original app (built on the XLink IoT SDK) does around the wire protocol:
how it connects, what it shows, and what it leaves to its cloud. Class names are given so the same code can be found in the app. Paths are under
`cn/xlink/airwick/`.

### Connecting and bonding

From `ble/core/BluetoothConnection.java`, `ble/core/BluetoothBondHelper.java`,
`task/XLinkConnectBluetoothDeviceTask.java`, `task/XLinkSendBluetoothDataTask.java`.

1. `connectGatt` with the LE transport, then service discovery.
2. If the device is not bonded, the app asks Android to bond and waits for the
   bond broadcast. Bonded: carry on. Bond removed or refused: close and fail.
3. For every characteristic with the notify property, in every service, it
   enables notifications and writes the CCCD. It treats the connection as ready
   500 ms after that.
4. On every connect it queues, in order: query status (`0x01`), query BLE
   firmware (`0x22`), query MCU firmware (`0x00`), query schedule slots 1 to 10
   (`0x02`), set time (`0x05`).
5. Each write is sent at least 50 ms after the previous write's success
   callback.

Other details:

- No MTU request, no connection-priority request, no explicit write type.
- GATT status 257 or 143 is treated as a failed connect.
- One connection at a time. With auto-connect on, a dropped link is reconnected
  immediately.
- Timeouts: scan during setup 10 s, connect during setup 30 s, connect from the
  control screen 15 s, the whole send queue 5 s, waiting for a room/fragrance
  acknowledgement 10 s, identity handshake 20 s.

First-time setup (`ui/bind/SearchDeviceActivity.java`) adds the identity
handshake described in [Identity handshake](#identity-handshake-0x20--0x21)
and writes set-time straight after it succeeds. Everyday connects from the
control screens skip the handshake.

### What the original app says about the device

From its strings and FAQ images:

- Blinking blue light: pairing mode. Slow flash or pulse: connected.
- Glowing or slowly pulsing while not connected to you: it is connected to
  another device. Power-cycle it and try again.
- "When a notification pops up, press pair, otherwise the app will have issues
  connecting."
- Range is about 30 feet. A phone can know up to 10 diffusers but connects to
  one at a time. A diffuser holds 10 schedules, which keep running while
  disconnected.
- "Don't use continuously." "Do not reuse batteries. Replace all batteries at
  the same time."

### Intensity in the original app

- A dial and a slider numbered **1 to 5**
  (`view/IntensityView.java`, `ui/schedule/EditScheduleActivity.java`), sent
  as wire values 0 to 4. The lowest position was paused.
- Onboarding copy described the scale as "from 1 (being the lowest) to 5 (being
  the highest)".
- New and preset schedules defaulted to strength 5 (wire 4).
- The newer control screen only lets you change intensity while a schedule is
  active, describing it as adjusting "the intensity of your current schedule".
- There was no Low/Medium/High/Max labeling.
- Power on and off was sent from the older control screen only.

### Refill in the original app

The diffuser reports nothing about the refill. In the original app's system the
remaining level is a cloud value (0 to 100, default 100) that the app only
displays. The depletion algorithm and the low-refill notification thresholds
run on the app's server and are not in the app.

Bottle life from the app's FAQ, "based off using your diffuser for 8 hours
every day":

| App strength | Wire value sent | Days | Operating hours |
| --- | --- | --- | --- |
| 1 | 0 | 55 | 440 |
| 2 | 1 | 40 | 320 |
| 3 | 2 | 32 | 256 |
| 4 | 3 | 27 | 216 |
| 5 | 4 | 23 | 184 |

The first row is the paused state (see [Intensity](#intensity)), so its 55-day
figure is unexplained. The other four rows are the four spraying strengths.

When the user tells the app how full the bottle looks, it offers 14 levels
(`ui/eol/AdjustRefillDialog.java`, `ui/changerefill/AdjustRefillFragment.java`):

| Level | Percent | Level | Percent |
| --- | --- | --- | --- |
| 1 | 2.26 | 8 | 43.61 |
| 2 | 5.75 | 9 | 51.28 |
| 3 | 10.31 | 10 | 58.91 |
| 4 | 15.77 | 11 | 73.46 |
| 5 | 21.99 | 12 | 85.99 |
| 6 | 28.81 | 13 | 95.26 |
| 7 | 36.07 | 14 | 100 |

Going the other way, a percentage maps to the first level whose upper bound it
does not exceed: 4.0, 8.02, 13.03, 18.87, 25.39, 32.43, 39.83, 47.43, 55.08,
66.18, 79.32, 90.62, 97.62, and level 14 above that.

Other refill behavior:

- The refill icon changes at 20, 40, 60 and 80 percent.
- "Empty" is stored as 0.1. The "insert a new refill" dialog appears only at
  that sentinel.
- "Remind me later" postpones by 3 hours.
- The original app has no counterpart to a per-boost cost.

### Battery in the original app

- Levels 0 to 4. The low-battery banner appears only at 0.
- Icon mapping in `util/ViewUtils.java`: level 2 uses `battery_3`, 3 uses
  `battery_2`, 4 uses `battery_1`, anything else uses `battery_4`. Which drawing
  means "full" is not stated in code.

### Schedules in the original app

From `ui/schedule/EditScheduleActivity.java`, `CreateScheduleActivity.java`,
`data/Schedule.java`, `data/ScheduleGroup.java`:

- End must be after start. A schedule cannot cross midnight.
- 00:00 to 00:01 is rejected ("at least 2 minutes").
- A start of 00:00 is stored on the device as 00:01 and shown as 00:00 when read
  back. This avoids a 00:00 to 00:00 window, which would look like an empty
  slot.
- Two schedules that share a day may not overlap.
- The repeat bit is cleared when no day is selected or the repeat switch is off.
- The editor offers names 3 to 8. Names 1, 2 and 7 come from the presets.
- Presets, all every day, repeating, enabled, strength 5:
  - 07:00 to 11:00, "Start the Day"
  - 12:00 to 16:00, "Midday Boost"
  - 17:00 to 22:00, "Evening"

### Rooms, fragrances and moods

- Rooms are picked in the order 1 to 6, then 8 (Office), then 7 (Other).
- The fragrance picker omits id 3 (Aroma Refresh).
- Moods are a cloud-only setting, never sent to the diffuser: 1 Get Energized,
  2 Relax, 3 Relieve Stress, 4 Refresh, 5 Meditate, 6 Sleep, 7 Improve my
  wellbeing, 8 Focus.

### Cloud service

Only setup and the refill estimate depended on the app's cloud service. Power,
intensity, schedules, room, fragrance and time sync are pure Bluetooth.

- The per-device key for the identity handshake came from the service.
- The remaining refill level was a value stored by the service. The app only
  displayed it.
- Moods and the purchase source were stored by the service and never sent to
  the diffuser.

The service has been shut down, so its addresses and identifiers are of no use
to anyone and are not recorded here. Nothing in this project used it.

## Where this project differs from the original app

Known differences, kept here so they are choices rather than surprises:

| Topic | Original app | This project |
| --- | --- | --- |
| Intensity labels | Numbered 1 to 5 on screen, with the lowest being paused | "Paused", then Low, Medium, High, Max |
| Bottle life at 8 h/day | 40, 32, 27, 23 days for the four strengths | 45, 30, 20, 15 days for Low to Max |
| Boost cost | None | 0.08% per boost |
| Schedule name 8 | "Other" | "Custom" |
| Status reply | Requires 8 payload bytes and range-checks fields | Python accepts 4 bytes with no range checks. Swift range-checks |
| Schedule status report `0x13` | Decoded | Ignored, so the current schedule only refreshes on a status query |
| Repeat bit | Cleared for non-repeating schedules | Python always sets it and drops it on read. Swift preserves it |
| Set-time and clear-all echoes | Decoded | Ignored |
| Intensity changes | Only while a schedule is active (newer screen) | At any time |
| Identity handshake | Once, at setup | Never |
| Write type | Android default, waits for the write callback | Swift writes without response, Python with response |
| Room 4 | "Foyer/Entryway" | "Foyer", with the longer name as an alias |

Both apps model intensity the same way, as a pause plus four strengths. Only
the labels differ.

The bottle-life difference is still open. The original app's figures for the
four strengths are 40, 32, 27 and 23 days, and this project's are 45, 30, 20 and
15. Neither set has been measured against a real bottle.

## Not determinable from the original app

- Spray interval per intensity, boost duration, any cooldown or lockout.
- The effect of factory reset (`0x0B`) and debug mode (`0x0A`), and the
  payloads of `0x10`, `0x40` and `0x41`.
- The meaning of status byte 4, and of the four firmware version bytes beyond
  their `a.bb,c.dd` formatting.
- Whether the write characteristic requires write-with-response, and its MTU.
- Why the device rejects pairing outside pairing mode. That is firmware
  behavior. The app simply asks Android to bond.
- Any advertisement or manufacturer-data layout, and any hardware variants.
- Firmware update: the app has no OTA or DFU code for this product and bundles
  no firmware.
