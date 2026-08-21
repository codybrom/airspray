import Foundation

public struct DiffuserSchema: Codable {
    public var timestamp: String
    public var connected: Bool
    public var deviceInfo: DeviceInfo
    public var state: DeviceState
    public var location: LocationProfile
    public var schedules: [ScheduleItem]

    public struct DeviceInfo: Codable {
        public var model: String
        public var projectCodename: String
        public var mcuFirmware: String?
        public var bleFirmware: String?
        public var bluetoothAddress: String?
        public var rssi: Int?
    }

    public struct DeviceState: Codable {
        public var power: Bool
        public var spraying: Bool
        public var intensity: Int
        public var intensityLabel: String
        public var batteryLevel: Int
        public var batteryPercentage: Int?
        public var refillPercentage: Double
        public var refillInstalledDate: String?
        public var scheduleActive: Bool
        public var currentScheduleId: Int
    }

    public struct LocationProfile: Codable {
        public var roomId: Int
        public var roomName: String
        public var fragranceId: Int
        public var moodId: Int
    }

    public struct ScheduleItem: Codable {
        public var id: Int
        public var nameId: Int
        public var name: String
        public var enabled: Bool
        public var startTime: String
        public var endTime: String
        public var intensity: Int
        public var days: [String]
        public var repeatEnabled: Bool
    }

    public static func defaultSchema(connected: Bool = false) -> DiffuserSchema {
        return DiffuserSchema(
            timestamp: ISO8601DateFormatter().string(from: Date()),
            connected: connected,
            deviceInfo: DeviceInfo(
                model: "Smart BLE Diffuser",
                projectCodename: "Reese",
                mcuFirmware: nil,
                bleFirmware: nil,
                bluetoothAddress: nil,
                rssi: nil
            ),
            state: DeviceState(
                power: false,
                spraying: false,
                intensity: 2,
                intensityLabel: "Medium",
                // The diffuser reports 0-4 bars and no percentage. Seeding a
                // number here would put an invented reading in a bug report.
                batteryLevel: 0,
                batteryPercentage: nil,
                refillPercentage: 100.0,
                refillInstalledDate: RefillDepletionModel.installedDateString(),
                scheduleActive: false,
                currentScheduleId: 0
            ),
            location: LocationProfile(
                roomId: 8,
                roomName: "Office",
                fragranceId: 1,
                moodId: 1
            ),
            schedules: []
        )
    }

    public func toJsonString() -> String? {
        let encoder = JSONEncoder()
        // snake_case so a pasted diagnostics blob validates against the schema
        // published by airspray/schema.py. Swift property names stay camelCase.
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(self) else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
