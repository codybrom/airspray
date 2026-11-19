import Foundation

/// One of the diffuser's ten on-device schedule slots.
///
/// Days use 1=Mon ... 7=Sun to match the UI. The wire format packs them into a
/// single repeat byte, high bit first (see `repeatByte`).
public struct DeviceSchedule: Equatable {
    public var slot: Int
    public var nameId: Int
    public var enabled: Bool
    public var startHour: Int
    public var startMin: Int
    public var endHour: Int
    public var endMin: Int
    public var days: Set<Int>
    public var repeatsWeekly: Bool
    public var intensity: Int

    public init(slot: Int, nameId: Int = 0, enabled: Bool = true,
                startHour: Int = 9, startMin: Int = 0,
                endHour: Int = 17, endMin: Int = 30,
                days: Set<Int> = [1, 2, 3, 4, 5],
                repeatsWeekly: Bool = true, intensity: Int = 2) {
        self.slot = slot
        self.nameId = nameId
        self.enabled = enabled
        self.startHour = startHour
        self.startMin = startMin
        self.endHour = endHour
        self.endMin = endMin
        self.days = days
        self.repeatsWeekly = repeatsWeekly
        self.intensity = intensity
    }

    public static let dayAbbreviations = [1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu",
                                          5: "Fri", 6: "Sat", 7: "Sun"]

    /// Compact description of the active days: "Every day", "Mon–Fri", "Sat, Sun".
    /// A contiguous run of three or more collapses to a range. Anything else lists.
    public var daysDescription: String {
        let sorted = days.sorted()
        guard let first = sorted.first, let last = sorted.last else { return "Never" }
        if sorted.count == 7 { return "Every day" }

        let isContiguous = sorted.count == (last - first + 1)
        if isContiguous && sorted.count >= 3 {
            let from = Self.dayAbbreviations[first] ?? ""
            let to = Self.dayAbbreviations[last] ?? ""
            return "\(from)–\(to)"
        }
        return sorted.compactMap { Self.dayAbbreviations[$0] }.joined(separator: ", ")
    }

    /// Whether the slot is occupied, matching the original app's `Schedule.isSet()`:
    /// decided purely by the window, ignoring `enabled`. A slot switched off still
    /// holds its times, and a cleared slot reads back all zeros.
    public var isConfigured: Bool {
        startHour != 0 || startMin != 0 || endHour != 0 || endMin != 0
    }

    /// An all-zero window is how the diffuser marks a slot free, so a midnight-to-
    /// midnight "all day" schedule would read back as an empty slot and vanish from
    /// the list. Widen it to 00:00-23:59, which is what the user meant anyway.
    /// (The original app dodged this by storing 00:01 as the start and hiding it on read.)
    public func normalizedForDevice() -> DeviceSchedule {
        guard startHour == 0, startMin == 0, endHour == 0, endMin == 0 else { return self }
        var widened = self
        widened.endHour = 23
        widened.endMin = 59
        return widened
    }

    /// The schedule with the editor's fields replaced and everything the editor
    /// does not expose -- slot, name id, the weekly-repeat flag -- left as it was.
    /// Rebuilding from scratch stripped the name off schedules made in the original
    /// app and forced the repeat flag on.
    public func applyingEdit(enabled: Bool, startHour: Int, startMin: Int,
                             endHour: Int, endMin: Int,
                             days: Set<Int>, intensity: Int) -> DeviceSchedule {
        var edited = self
        edited.enabled = enabled
        edited.startHour = startHour
        edited.startMin = startMin
        edited.endHour = endHour
        edited.endMin = endMin
        edited.days = days
        edited.intensity = intensity
        return edited
    }

    /// Intensities the editor should offer for this schedule. Level 0 (paused)
    /// is not something to pick for a new schedule, but a schedule that already
    /// stores it must be able to keep it rather than be rewritten to Medium.
    public var editableIntensities: [Int] {
        intensity == 0 ? [0, 1, 2, 3, 4] : [1, 2, 3, 4]
    }

    /// Bit per day, MSB is the weekly-repeat flag: 0x80 repeat, 0x40 Mon ... 0x01 Sun.
    public var repeatByte: UInt8 {
        var byte: UInt8 = repeatsWeekly ? 0x80 : 0x00
        let bits: [Int: UInt8] = [1: 0x40, 2: 0x20, 3: 0x10, 4: 0x08, 5: 0x04, 6: 0x02, 7: 0x01]
        for day in days { byte |= bits[day] ?? 0 }
        return byte
    }
}

public struct DiffuserProtocol {
    public static let serviceUUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
    public static let commandCharUUID = "6e400007-b5a3-f393-e0a9-e50e24dcca9e"
    public static let notifyCharUUID = "6e400008-b5a3-f393-e0a9-e50e24dcca9e"

    /// Names the diffuser advertises. The original app matches these exactly
    /// (^(Air Wick Mist|AIRWICK REESES)$), so a substring match would happily
    /// connect to an unrelated humidifier.
    public static let deviceNamePatterns = ["Air Wick Mist", "AIRWICK REESES"]

    /// Fragrance names as shipped in the original app.
    ///
    /// All 33 are listed and all transmit -- the encoder does not validate. Note the
    /// original app's own BLE decoder only acknowledges 1-31, so for 32 and 33 the device
    /// will not echo the value back. They are stored locally either way.
    public static let fragranceNames: [Int: String] = [
        1: "Aroma Unwind",
        2: "Aroma Happiness",
        3: "Aroma Refresh",
        4: "Aroma Sleep",
        5: "Apple & Cinnamon",
        6: "Eucalyptus",
        7: "Fresh Cucumber",
        8: "Fresh Waters",
        9: "Lavender & Almond Blossom",
        10: "Lemon Thyme",
        11: "Mandarin & Sweet Tangerine",
        12: "Peony & Jasmine",
        13: "Sweet Fruit & Nutmeg",
        14: "Sweet Melon & Vanilla",
        15: "Wild Mint",
        16: "Woodland Pine",
        17: "Coconut and Pineapple",
        18: "Sandalwood",
        19: "Pumpkin Spice",
        20: "Fresh Pine & Juniper",
        21: "Apple Cinnamon Medley",
        22: "Cinnamon & Crisp Apple",
        23: "Brown Sugar & Vanilla",
        24: "Vanilla & Caramel",
        25: "Aroma Rejuvenate",
        26: "Blue Agave",
        27: "Vanilla Pink Papaya",
        28: "Cotton & Spring Lilac",
        29: "Lush Honeysuckle & Raspberry",
        30: "Hibiscus & Bloom Orchids",
        31: "Bonfire & Crisp Fall Air",
        32: "Aroma Relax",
        33: "Linen Petals",
    ]

    /// The catalogue ordered by name, for pickers. A 33-item list in ID order is
    /// effectively unscannable. The IDs carry no meaning to the reader.
    public static var fragrancesSortedByName: [(id: Int, name: String)] {
        fragranceNames
            .map { (id: $0.key, name: $0.value) }
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    /// Level 0 pauses spraying, and 1-4 are the fragrance strengths.
    public static let intensityNames = [0: "Paused", 1: "Low", 2: "Medium", 3: "High", 4: "Max"]

    /// Room IDs are fixed by the diffuser firmware. Do not renumber.
    public static let roomNames: [Int: String] = [
        1: "Bathroom",
        2: "Bedroom",
        3: "Dining Room",
        4: "Foyer",
        5: "Kitchen",
        6: "Living Room",
        7: "Other",
        8: "Office",
    ]

    // Command Codes. These are the wire values the diffuser expects. They must stay
    // in sync with airspray/protocol.py (see tests/test_swift_protocol_parity.py).
    public static let cmdQueryMcuFirmware: UInt8 = 0x00
    public static let cmdQueryStatus: UInt8      = 0x01
    public static let cmdQuerySchedule: UInt8    = 0x02
    public static let cmdSetSchedule: UInt8      = 0x03
    public static let cmdClearSchedule: UInt8    = 0x04
    public static let cmdSetTime: UInt8          = 0x05
    public static let cmdSetPower: UInt8         = 0x06
    public static let cmdSetIntensity: UInt8     = 0x07
    public static let cmdSetRoomFragrance: UInt8 = 0x08
    public static let cmdBoost: UInt8            = 0x09
    public static let cmdReset: UInt8            = 0x0B
    public static let cmdSprayReport: UInt8      = 0x11
    public static let cmdBatteryReport: UInt8    = 0x12
    public static let cmdQueryBleFirmware: UInt8 = 0x22

    public static func calculateChecksum(cmd: UInt8, payload: [UInt8]) -> UInt8 {
        var chk = cmd ^ 0xAA ^ UInt8(payload.count)
        for b in payload {
            chk ^= b
        }
        return chk
    }

    public static func constructCommand(cmd: UInt8, payload: [UInt8] = []) -> Data {
        let checksum = calculateChecksum(cmd: cmd, payload: payload)
        var bytes: [UInt8] = [0xFF, 0x55, cmd, UInt8(payload.count)]
        bytes.append(contentsOf: payload)
        bytes.append(checksum)
        return Data(bytes)
    }

    public static func encodeSetPower(on: Bool) -> Data {
        return constructCommand(cmd: cmdSetPower, payload: [on ? 0x01 : 0x00])
    }

    public static func encodeSetIntensity(level: Int) -> Data {
        let clamped = max(0, min(4, level))
        return constructCommand(cmd: cmdSetIntensity, payload: [UInt8(clamped)])
    }

    public static func encodeBoost() -> Data {
        return constructCommand(cmd: cmdBoost, payload: [])
    }

    public static func encodeQueryStatus() -> Data {
        return constructCommand(cmd: cmdQueryStatus, payload: [])
    }

    public static func encodeQueryMcuFirmware() -> Data {
        return constructCommand(cmd: cmdQueryMcuFirmware, payload: [])
    }

    public static func encodeQueryBleFirmware() -> Data {
        return constructCommand(cmd: cmdQueryBleFirmware, payload: [])
    }

    public static func encodeSetTime(date: Date = Date()) -> Data {
        let calendar = Calendar.current
        let hour = UInt8(calendar.component(.hour, from: date))
        let min = UInt8(calendar.component(.minute, from: date))
        let sec = UInt8(calendar.component(.second, from: date))
        let weekday = UInt8(calendar.component(.weekday, from: date) - 1) // 0=Sun..6=Sat
        return constructCommand(cmd: cmdSetTime, payload: [hour, min, sec, weekday])
    }

    /// Room 1-8, fragrance 1-33. Out-of-range values are clamped rather than
    /// trapped: `UInt8(_:)` crashes on anything above 255, and a menu bar app
    /// should not die because a stored id went stale.
    public static func encodeSetRoomAndFragrance(roomId: Int, fragranceId: Int) -> Data {
        let room = UInt8(max(1, min(8, roomId)))
        let fragrance = UInt8(max(1, min(fragranceNames.keys.max() ?? 33, fragranceId)))
        return constructCommand(cmd: cmdSetRoomFragrance, payload: [room, fragrance])
    }

    /// Decode an 8-byte schedule payload from a 0x02 or 0x03 notification.
    /// Returns nil for a malformed payload or an unset slot (slot id 0).
    public static func decodeSchedule(payload: [UInt8]) -> DeviceSchedule? {
        guard payload.count >= 8 else { return nil }
        let slot = Int(payload[0] & 0x0F)
        guard slot != 0 else { return nil }

        let repeatByte = payload[6]
        let bits: [(Int, UInt8)] = [(1, 0x40), (2, 0x20), (3, 0x10), (4, 0x08),
                                    (5, 0x04), (6, 0x02), (7, 0x01)]
        var days = Set<Int>()
        for (day, mask) in bits where repeatByte & mask != 0 { days.insert(day) }

        return DeviceSchedule(
            slot: slot,
            nameId: Int((payload[0] & 0xF0) >> 4),
            enabled: payload[1] == 1,
            startHour: Int(payload[2]),
            startMin: Int(payload[3]),
            endHour: Int(payload[4]),
            endMin: Int(payload[5]),
            days: days,
            repeatsWeekly: repeatByte & 0x80 != 0,
            intensity: Int(payload[7])
        )
    }

    /// The 8-byte payload for a schedule, without the surrounding frame.
    public static func scheduledPayload(for schedule: DeviceSchedule) -> [UInt8] {
        let schedule = schedule.normalizedForDevice()
        // Clamped, not trapped -- UInt8(_:) crashes above 255, and these come
        // from decoded device state as well as from the editor.
        func byte(_ value: Int, max limit: Int) -> UInt8 { UInt8(Swift.max(0, Swift.min(limit, value))) }
        return [
            UInt8(((schedule.nameId & 0x0F) << 4) | (schedule.slot & 0x0F)),
            schedule.enabled ? 1 : 0,
            byte(schedule.startHour, max: 23),
            byte(schedule.startMin, max: 59),
            byte(schedule.endHour, max: 23),
            byte(schedule.endMin, max: 59),
            schedule.repeatByte,
            byte(schedule.intensity, max: 4)
        ]
    }

    public static func encodeSetSchedule(_ schedule: DeviceSchedule) -> Data {
        constructCommand(cmd: cmdSetSchedule, payload: scheduledPayload(for: schedule))
    }

    public static func encodeQuerySchedule(slot: Int) -> Data {
        constructCommand(cmd: cmdQuerySchedule, payload: [UInt8(slot & 0x0F)])
    }

    public static func encodeDeleteSchedule(slot: Int) -> Data {
        let b0 = UInt8(slot & 0x0F)
        return constructCommand(cmd: cmdSetSchedule, payload: [b0, 0, 0, 0, 0, 0, 0, 0])
    }

    public static func encodeClearAllSchedules() -> Data {
        return constructCommand(cmd: cmdClearSchedule, payload: [])
    }

    public static func encodeReset() -> Data {
        return constructCommand(cmd: cmdReset, payload: [])
    }

    /// Locate and validate one packet inside a notification buffer.
    ///
    /// Mirrors the original app's checkPackage(): the frame may be preceded by
    /// other bytes, and the trailing checksum must agree before the payload is
    /// acted on. Returns nil for anything malformed.
    public static func decodePacket(_ data: Data) -> (cmd: UInt8, payload: [UInt8])? {
        let bytes = [UInt8](data)
        guard bytes.count >= 5 else { return nil }

        var idx = 0
        while idx < bytes.count - 1 && !(bytes[idx] == 0xFF && bytes[idx + 1] == 0x55) {
            idx += 1
        }
        guard idx + 1 < bytes.count, bytes[idx] == 0xFF, bytes[idx + 1] == 0x55 else { return nil }
        guard idx + 4 < bytes.count else { return nil }

        let cmd = bytes[idx + 2]
        let len = Int(bytes[idx + 3])
        guard bytes.count >= idx + 5 + len else { return nil }

        let payload = Array(bytes[(idx + 4)..<(idx + 4 + len)])
        guard calculateChecksum(cmd: cmd, payload: payload) == bytes[idx + 4 + len] else { return nil }
        return (cmd, payload)
    }

    public static func isValidRoomId(_ id: Int) -> Bool { roomNames[id] != nil }
    public static func isValidFragranceId(_ id: Int) -> Bool { fragranceNames[id] != nil }

    /// Names for the schedule name id, as in airspray/schema.py.
    public static func scheduleName(forNameId nameId: Int) -> String {
        let names = [1: "Start the Day", 2: "Midday Boost", 3: "Wake Up", 4: "Morning",
                     5: "Midday", 6: "Afternoon", 7: "Evening", 8: "Custom"]
        return names[nameId] ?? "Schedule \(nameId)"
    }

    /// Split the status packet's schedule byte the way the original app does
    /// (Reese.getScheduleStatus / getScheduleID): high nibble is the status,
    /// low nibble the slot. A non-zero status is taken to mean a schedule is
    /// currently running.
    public static func scheduleStatus(fromInfo info: Int) -> (active: Bool, slot: Int) {
        (active: (info & 0xF0) >> 4 != 0, slot: info & 0x0F)
    }

    /// Whether an advertised BLE name belongs to a diffuser we control.
    /// Anchored like the original app, but case-insensitive for tolerance.
    public static func isRecognisedDeviceName(_ name: String) -> Bool {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        return deviceNamePatterns.contains { $0.compare(trimmed, options: .caseInsensitive) == .orderedSame }
    }

    /// Human-readable battery state.
    ///
    /// The diffuser reports a 0-4 level, not a percentage, so this renders a
    /// four-segment gauge plus a word rather than implying finer resolution than
    /// the hardware actually has.
    public static func batteryDescription(_ level: Int) -> String {
        let clamped = max(0, min(4, level))
        let gauge = String(repeating: "\u{25AE}", count: clamped)
            + String(repeating: "\u{25AF}", count: 4 - clamped)
        let word: String
        switch clamped {
        case 4: word = "Full"
        case 3: word = "Good"
        case 2: word = "Medium"
        case 1: word = "Low"
        default: word = "Critical"
        }
        return "\(gauge) \(word)"
    }

    /// The diffuser intermittently reports 0 bars mid-spray. The original app only
    /// believes a 0 once the level is already nearly empty. Without this the menu
    /// bar flashes a false "battery empty".
    public static func shouldAcceptBatteryLevel(_ level: Int, previous: Int) -> Bool {
        guard (0...4).contains(level) else { return false }
        if level != 0 { return true }
        return abs(previous) <= 1
    }
}
