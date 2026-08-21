// Behavioural checks for the pure protocol layer shared with airspray/protocol.py.
// Compiled and run by tests/test_swift_protocol_behavior.py.
import Foundation

var failures = 0

func check(_ name: String, _ ok: Bool, _ detail: @autoclosure () -> String = "") {
    if ok {
        print("\(name)\tOK")
    } else {
        failures += 1
        print("\(name)\tFAIL: \(detail())")
    }
}

// MARK: - decodePacket

let good = DiffuserProtocol.constructCommand(cmd: 0x01, payload: [1, 0, 2, 4])
if let d = DiffuserProtocol.decodePacket(good) {
    check("decode_valid_cmd", d.cmd == 0x01, "got \(d.cmd)")
    check("decode_valid_payload", d.payload == [1, 0, 2, 4], "got \(d.payload)")
} else {
    check("decode_valid_cmd", false, "returned nil")
    check("decode_valid_payload", false, "returned nil")
}

var corrupt = [UInt8](good)
corrupt[corrupt.count - 1] ^= 0xFF
check("decode_rejects_bad_checksum", DiffuserProtocol.decodePacket(Data(corrupt)) == nil)

let prefixed = Data([0x00, 0xAB] + [UInt8](good))
check("decode_scans_past_leading_garbage",
      DiffuserProtocol.decodePacket(prefixed)?.payload == [1, 0, 2, 4])

check("decode_rejects_truncated", DiffuserProtocol.decodePacket(Data([0xFF, 0x55, 0x01])) == nil)

// Header claims 8 payload bytes but only 2 are present.
check("decode_rejects_short_payload",
      DiffuserProtocol.decodePacket(Data([0xFF, 0x55, 0x02, 0x08, 0x01, 0x02])) == nil)

// MARK: - device name matching

check("name_accepts_air_wick_mist", DiffuserProtocol.isRecognisedDeviceName("Air Wick Mist"))
check("name_accepts_airwick_reeses", DiffuserProtocol.isRecognisedDeviceName("AIRWICK REESES"))
check("name_rejects_other_mist_device", !DiffuserProtocol.isRecognisedDeviceName("Essential Mist Humidifier"))
check("name_rejects_substring_match", !DiffuserProtocol.isRecognisedDeviceName("Air Wick Mister"))
check("name_rejects_empty", !DiffuserProtocol.isRecognisedDeviceName(""))

// MARK: - battery glitch filter

check("battery_rejects_spurious_zero", !DiffuserProtocol.shouldAcceptBatteryLevel(0, previous: 4))
check("battery_accepts_zero_when_low", DiffuserProtocol.shouldAcceptBatteryLevel(0, previous: 1))
check("battery_accepts_normal_level", DiffuserProtocol.shouldAcceptBatteryLevel(3, previous: 0))
check("battery_rejects_out_of_range", !DiffuserProtocol.shouldAcceptBatteryLevel(9, previous: 3))

// MARK: - room table

let expectedRooms: [Int: String] = [
    1: "Bathroom", 2: "Bedroom", 3: "Dining Room", 4: "Foyer",
    5: "Kitchen", 6: "Living Room", 7: "Other", 8: "Office",
]
check("room_table_matches_firmware", DiffuserProtocol.roomNames == expectedRooms,
      "got \(DiffuserProtocol.roomNames)")

// MARK: - schedule name nibble

let sched = DiffuserProtocol.encodeSetSchedule(
    DeviceSchedule(slot: 3, nameId: 5, startHour: 9, startMin: 0,
                   endHour: 17, endMin: 30, days: [1, 2, 3, 4, 5], intensity: 2))
if let d = DiffuserProtocol.decodePacket(sched) {
    check("schedule_packs_name_and_slot", d.payload.first == 0x53, "got \(String(format: "%02X", d.payload.first ?? 0))")
} else {
    check("schedule_packs_name_and_slot", false, "packet did not decode")
}

// MARK: - intensity 0 (pause) survives encoding

if let d = DiffuserProtocol.decodePacket(DiffuserProtocol.encodeSetIntensity(level: 0)) {
    check("intensity_zero_preserved", d.payload == [0], "got \(d.payload)")
} else {
    check("intensity_zero_preserved", false, "packet did not decode")
}

// MARK: - refill header summary

let summary = RefillDepletionModel.headerSummary(percent: 95.8, intensity: 2)
check("refill_summary_leads_with_percentage", summary.hasPrefix("96%"), "got \(summary)")
check("refill_summary_includes_forecast", summary.contains("left"), "got \(summary)")
check("refill_summary_clamps_above_full",
      RefillDepletionModel.headerSummary(percent: 140.0, intensity: 2).hasPrefix("100%"),
      "got \(RefillDepletionModel.headerSummary(percent: 140.0, intensity: 2))")
check("refill_summary_clamps_below_empty",
      RefillDepletionModel.headerSummary(percent: -5.0, intensity: 2).hasPrefix("0%"),
      "got \(RefillDepletionModel.headerSummary(percent: -5.0, intensity: 2))")

// MARK: - battery description

check("battery_label_full", DiffuserProtocol.batteryDescription(4) == "\u{25AE}\u{25AE}\u{25AE}\u{25AE} Full",
      "got \(DiffuserProtocol.batteryDescription(4))")
check("battery_label_good", DiffuserProtocol.batteryDescription(3) == "\u{25AE}\u{25AE}\u{25AE}\u{25AF} Good",
      "got \(DiffuserProtocol.batteryDescription(3))")
check("battery_label_medium", DiffuserProtocol.batteryDescription(2) == "\u{25AE}\u{25AE}\u{25AF}\u{25AF} Medium",
      "got \(DiffuserProtocol.batteryDescription(2))")
check("battery_label_low", DiffuserProtocol.batteryDescription(1) == "\u{25AE}\u{25AF}\u{25AF}\u{25AF} Low",
      "got \(DiffuserProtocol.batteryDescription(1))")
check("battery_label_critical", DiffuserProtocol.batteryDescription(0) == "\u{25AF}\u{25AF}\u{25AF}\u{25AF} Critical",
      "got \(DiffuserProtocol.batteryDescription(0))")
check("battery_label_clamps_out_of_range",
      DiffuserProtocol.batteryDescription(9) == DiffuserProtocol.batteryDescription(4),
      "got \(DiffuserProtocol.batteryDescription(9))")

// MARK: - config persistence

let tmpConfig = URL(fileURLWithPath: NSTemporaryDirectory())
    .appendingPathComponent("airspray-test-\(UUID().uuidString).json")
var cfg = AppConfig()
cfg.refillPercentage = 42.0   // avoid the 100.0 python-state migration branch
cfg.lastRoomId = 8
cfg.save(to: tmpConfig)
let reloaded = AppConfig.load(from: tmpConfig)
check("config_round_trips_room_id", reloaded.lastRoomId == 8, "got \(reloaded.lastRoomId)")
check("config_round_trips_refill", reloaded.refillPercentage == 42.0, "got \(reloaded.refillPercentage)")
try? FileManager.default.removeItem(at: tmpConfig)

// MARK: - config tolerates older files

// A config written by an earlier build: keys added later are simply absent.
// The synthesized decoder throws on those, and load() swallows it and hands back
// a fresh config -- silently wiping every setting the user had.
let legacyPath = URL(fileURLWithPath: NSTemporaryDirectory())
    .appendingPathComponent("airspray-legacy-\(UUID().uuidString).json")
let legacyJSON = #"{"lastRoomId":6,"refillPercentage":37.5,"defaultIntensity":3,"lastAddress":"AA-BB"}"#
try? legacyJSON.write(to: legacyPath, atomically: true, encoding: .utf8)
let legacy = AppConfig.load(from: legacyPath)
check("legacy_config_keeps_room", legacy.lastRoomId == 6, "got \(legacy.lastRoomId)")
check("legacy_config_keeps_refill", legacy.refillPercentage == 37.5, "got \(legacy.refillPercentage)")
check("legacy_config_keeps_address", legacy.lastAddress == "AA-BB", "got \(String(describing: legacy.lastAddress))")
check("legacy_config_defaults_missing_key", legacy.totalPuffsCount == 0, "got \(legacy.totalPuffsCount)")
try? FileManager.default.removeItem(at: legacyPath)

// MARK: - fragrance persistence

let fragPath = URL(fileURLWithPath: NSTemporaryDirectory())
    .appendingPathComponent("airspray-frag-\(UUID().uuidString).json")
var fragCfg = AppConfig()
fragCfg.refillPercentage = 42.0
fragCfg.lastFragranceId = 17
fragCfg.save(to: fragPath)
check("config_round_trips_fragrance_id", AppConfig.load(from: fragPath).lastFragranceId == 17,
      "got \(AppConfig.load(from: fragPath).lastFragranceId)")
try? FileManager.default.removeItem(at: fragPath)

// MARK: - fragrance catalogue

check("fragrance_catalogue_covers_full_range",
      Set(DiffuserProtocol.fragranceNames.keys) == Set(1...33),
      "got \(DiffuserProtocol.fragranceNames.keys.sorted())")
check("fragrance_catalogue_has_no_blank_names",
      DiffuserProtocol.fragranceNames.values.allSatisfy { !$0.isEmpty })
check("fragrance_name_lookup",
      DiffuserProtocol.fragranceNames[3] == "Aroma Refresh",
      "got \(DiffuserProtocol.fragranceNames[3] ?? "nil")")
check("fragrance_catalogue_includes_newest",
      DiffuserProtocol.fragranceNames[33] == "Linen Petals",
      "got \(DiffuserProtocol.fragranceNames[33] ?? "nil")")
check("fragrance_name_with_ampersand",
      DiffuserProtocol.fragranceNames[9] == "Lavender & Almond Blossom",
      "got \(DiffuserProtocol.fragranceNames[9] ?? "nil")")

// MARK: - refill percentage clamping

check("refill_clamp_above_full", RefillDepletionModel.clampPercentage(140.0) == 100.0)
check("refill_clamp_below_empty", RefillDepletionModel.clampPercentage(-5.0) == 0.0)
check("refill_clamp_passes_valid_through", RefillDepletionModel.clampPercentage(42.5) == 42.5)

// MARK: - fragrance ordering

let ordered = DiffuserProtocol.fragrancesSortedByName
check("fragrance_order_has_every_entry",
      ordered.count == DiffuserProtocol.fragranceNames.count,
      "got \(ordered.count)")
check("fragrance_order_preserves_ids",
      Set(ordered.map { $0.id }) == Set(DiffuserProtocol.fragranceNames.keys))
check("fragrance_order_is_alphabetical",
      zip(ordered, ordered.dropFirst()).allSatisfy {
          $0.name.localizedStandardCompare($1.name) != .orderedDescending
      },
      "got \(ordered.map { $0.name }.prefix(5))")

// MARK: - schedule decoding

// slot 3, name 5, enabled, 09:00-17:30, weekly repeat Mon-Fri, intensity 2
let schedPayload: [UInt8] = [0x53, 0x01, 9, 0, 17, 30, 0x80 | 0x40 | 0x20 | 0x10 | 0x08 | 0x04, 2]
if let sched = DiffuserProtocol.decodeSchedule(payload: schedPayload) {
    check("schedule_decode_slot", sched.slot == 3, "got \(sched.slot)")
    check("schedule_decode_name", sched.nameId == 5, "got \(sched.nameId)")
    check("schedule_decode_enabled", sched.enabled)
    check("schedule_decode_window",
          sched.startHour == 9 && sched.startMin == 0 && sched.endHour == 17 && sched.endMin == 30,
          "got \(sched.startHour):\(sched.startMin)-\(sched.endHour):\(sched.endMin)")
    check("schedule_decode_weekdays", sched.days == Set([1, 2, 3, 4, 5]), "got \(sched.days.sorted())")
    check("schedule_decode_repeats", sched.repeatsWeekly)
    check("schedule_decode_intensity", sched.intensity == 2, "got \(sched.intensity)")
} else {
    for name in ["slot", "name", "enabled", "window", "weekdays", "repeats", "intensity"] {
        check("schedule_decode_\(name)", false, "decode returned nil")
    }
}

// A cleared slot reads back as all zeros; slot id 0 is not a real slot.
check("schedule_decode_rejects_empty_slot",
      DiffuserProtocol.decodeSchedule(payload: [0, 0, 0, 0, 0, 0, 0, 0]) == nil)
check("schedule_decode_rejects_short_payload",
      DiffuserProtocol.decodeSchedule(payload: [0x01, 0x01, 9]) == nil)

// A disabled but present slot must still decode, so the UI can show it exists.
if let disabled = DiffuserProtocol.decodeSchedule(payload: [0x02, 0x00, 8, 0, 9, 0, 0x80, 1]) {
    check("schedule_decode_disabled_slot", disabled.slot == 2 && !disabled.enabled,
          "got slot \(disabled.slot) enabled \(disabled.enabled)")
} else {
    check("schedule_decode_disabled_slot", false, "decode returned nil")
}

// Encode/decode must agree, or bit-packing errors ship silently.
let original = DeviceSchedule(slot: 7, nameId: 0, enabled: true,
                              startHour: 6, startMin: 15, endHour: 23, endMin: 45,
                              days: Set([6, 7]), repeatsWeekly: true, intensity: 4)
if let roundTripped = DiffuserProtocol.decodeSchedule(
        payload: DiffuserProtocol.scheduledPayload(for: original)) {
    check("schedule_round_trips", roundTripped == original, "got \(roundTripped)")
} else {
    check("schedule_round_trips", false, "decode returned nil")
}

// MARK: - schedule day formatting

func daysText(_ days: Set<Int>) -> String {
    DeviceSchedule(slot: 1, days: days).daysDescription
}
check("days_every_day", daysText(Set(1...7)) == "Every day", "got \(daysText(Set(1...7)))")
check("days_weekday_run", daysText(Set([1,2,3,4,5])) == "Mon–Fri", "got \(daysText(Set([1,2,3,4,5])))")
check("days_weekend_pair", daysText(Set([6,7])) == "Sat, Sun", "got \(daysText(Set([6,7])))")
check("days_scattered", daysText(Set([1,3,5])) == "Mon, Wed, Fri", "got \(daysText(Set([1,3,5])))")
check("days_single", daysText(Set([4])) == "Thu", "got \(daysText(Set([4])))")
check("days_none", daysText([]) == "Never", "got \(daysText([]))")

// MARK: - configured vs free slots

check("slot_configured_when_enabled",
      DeviceSchedule(slot: 1, enabled: true).isConfigured)
check("slot_free_when_cleared",
      !DeviceSchedule(slot: 1, enabled: false, startHour: 0, startMin: 0,
                      endHour: 0, endMin: 0, days: [], repeatsWeekly: false,
                      intensity: 0).isConfigured)
check("slot_configured_when_disabled_but_set",
      DeviceSchedule(slot: 1, enabled: false, startHour: 8, startMin: 0,
                     endHour: 9, endMin: 0, days: [1], repeatsWeekly: true,
                     intensity: 1).isConfigured)

// MARK: - occupied-slot semantics match the vendor's isSet()

// The vendor decides a slot is occupied purely from its window, ignoring `enable`.
check("slot_free_when_window_all_zero_even_if_enabled",
      !DeviceSchedule(slot: 1, enabled: true, startHour: 0, startMin: 0,
                      endHour: 0, endMin: 0, days: [1], repeatsWeekly: true,
                      intensity: 2).isConfigured)

// MARK: - midnight window guard

let allDay = DeviceSchedule(slot: 1, enabled: true, startHour: 0, startMin: 0,
                            endHour: 0, endMin: 0, days: Set(1...7),
                            repeatsWeekly: true, intensity: 2)
let normalized = allDay.normalizedForDevice()
check("normalize_expands_all_zero_window",
      normalized.endHour == 23 && normalized.endMin == 59,
      "got \(normalized.endHour):\(normalized.endMin)")
check("normalize_keeps_start_at_midnight",
      normalized.startHour == 0 && normalized.startMin == 0)

let realWindow = DeviceSchedule(slot: 1, startHour: 9, startMin: 0, endHour: 17, endMin: 30)
check("normalize_leaves_real_window_alone",
      realWindow.normalizedForDevice() == realWindow)

// An all-zero window on the wire is indistinguishable from a free slot.
let wire = DiffuserProtocol.scheduledPayload(for: allDay)
check("payload_never_writes_all_zero_window",
      !(wire[2] == 0 && wire[3] == 0 && wire[4] == 0 && wire[5] == 0),
      "got \(wire)")
check("normalized_payload_reads_back_as_occupied",
      DiffuserProtocol.decodeSchedule(payload: wire)?.isConfigured == true)

print("FAILURES=\(failures)")

// MARK: - refill consumption rate (mirrors CONSUMPTION_RATE_PER_SEC in refill.py)

// Level 0 is paused. Rating it like Medium drains the bottle while the diffuser
// is powered but deliberately not spraying.
check("consumption_zero_while_paused",
      RefillDepletionModel.consumptionPercentPerSecond(for: 0) == 0.0,
      "got \(RefillDepletionModel.consumptionPercentPerSecond(for: 0))")
check("consumption_zero_for_unknown_level",
      RefillDepletionModel.consumptionPercentPerSecond(for: 9) == 0.0)
check("consumption_medium_matches_python",
      abs(RefillDepletionModel.consumptionPercentPerSecond(for: 2) - 100.0 / (240.0 * 3600.0)) < 1e-12,
      "got \(RefillDepletionModel.consumptionPercentPerSecond(for: 2))")
check("consumption_rises_with_intensity",
      RefillDepletionModel.consumptionPercentPerSecond(for: 1)
          < RefillDepletionModel.consumptionPercentPerSecond(for: 4))

// MARK: - installed date format shared with the CLI

let installed = RefillDepletionModel.installedDateString()
check("installed_date_is_year_month_day",
      installed.range(of: "^[0-9]{4}-[0-9]{2}-[0-9]{2}$", options: .regularExpression) != nil,
      "got \(installed)")

// MARK: - out-of-range ids are clamped, not fatal

if let d = DiffuserProtocol.decodePacket(
    DiffuserProtocol.encodeSetRoomAndFragrance(roomId: 999, fragranceId: 999)) {
    check("room_fragrance_clamps_high", d.payload == [8, 33], "got \(d.payload)")
} else {
    check("room_fragrance_clamps_high", false, "packet did not decode")
}
if let d = DiffuserProtocol.decodePacket(
    DiffuserProtocol.encodeSetRoomAndFragrance(roomId: -1, fragranceId: 0)) {
    check("room_fragrance_clamps_low", d.payload == [1, 1], "got \(d.payload)")
} else {
    check("room_fragrance_clamps_low", false, "packet did not decode")
}

let wild = DeviceSchedule(slot: 1, startHour: 999, startMin: 999,
                          endHour: 999, endMin: 999, intensity: 99)
check("schedule_payload_clamps_out_of_range",
      DiffuserProtocol.scheduledPayload(for: wild) == [0x01, 1, 23, 59, 23, 59, wild.repeatByte, 4],
      "got \(DiffuserProtocol.scheduledPayload(for: wild))")

// MARK: - diagnostics schema uses the keys airspray/schema.py publishes

if let json = DiffuserSchema.defaultSchema().toJsonString() {
    check("schema_json_is_snake_case", json.contains("\"refill_percentage\"") && json.contains("\"device_info\""),
          "got \(json.prefix(120))")
    check("schema_json_has_no_camel_case_keys", !json.contains("\"refillPercentage\""))
    check("schema_json_omits_invented_battery_percentage", !json.contains("battery_percentage\" :"))
} else {
    check("schema_json_is_snake_case", false, "encoding failed")
    check("schema_json_has_no_camel_case_keys", false, "encoding failed")
    check("schema_json_omits_invented_battery_percentage", false, "encoding failed")
}

// MARK: - config save preserves keys owned by the Python CLI

let sharedPath = URL(fileURLWithPath: NSTemporaryDirectory())
    .appendingPathComponent("airspray-shared-\(UUID().uuidString).json")
let cliJSON = #"{"activeDays":[1,2,3],"maxIdleSeconds":600,"refillPercentage":55.0}"#
try? cliJSON.write(to: sharedPath, atomically: true, encoding: .utf8)
var shared = AppConfig.load(from: sharedPath)
check("shared_config_reads_cli_refill", shared.refillPercentage == 55.0, "got \(shared.refillPercentage)")
shared.lastRoomId = 8
shared.save(to: sharedPath)
let afterSave = (try? String(contentsOf: sharedPath, encoding: .utf8)) ?? ""
check("shared_config_keeps_cli_only_keys",
      afterSave.contains("activeDays") && afterSave.contains("maxIdleSeconds"),
      "got \(afterSave)")
check("shared_config_writes_own_keys", afterSave.contains("lastRoomId"), "got \(afterSave)")
try? FileManager.default.removeItem(at: sharedPath)

exit(failures == 0 ? 0 : 1)
