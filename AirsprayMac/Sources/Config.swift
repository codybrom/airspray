import Foundation

public struct AppConfig: Codable {
    public var lastAddress: String?
    public var lastRoomId: Int
    public var lastFragranceId: Int
    public var customLocationName: String?
    public var defaultIntensity: Int
    public var isDeskPresenceEnabled: Bool
    public var isDeskScheduleAlways: Bool
    public var deskScheduleStart: String
    public var deskScheduleEnd: String
    public var isLaunchAtLogin: Bool
    public var refillPercentage: Double
    public var refillInstalledDate: String?
    public var totalPuffsCount: Int
    public var lastActiveTimestamp: Double?

    public init(
        lastAddress: String? = nil,
        lastRoomId: Int = 1,
        lastFragranceId: Int = 1,
        customLocationName: String? = nil,
        defaultIntensity: Int = 2,
        isDeskPresenceEnabled: Bool = true,
        isDeskScheduleAlways: Bool = false,
        deskScheduleStart: String = "09:00",
        deskScheduleEnd: String = "17:30",
        isLaunchAtLogin: Bool = false,
        refillPercentage: Double = 100.0,
        refillInstalledDate: String? = nil,
        totalPuffsCount: Int = 0,
        lastActiveTimestamp: Double? = nil
    ) {
        self.lastAddress = lastAddress
        self.lastRoomId = lastRoomId
        self.lastFragranceId = lastFragranceId
        self.customLocationName = customLocationName
        self.defaultIntensity = defaultIntensity
        self.isDeskPresenceEnabled = isDeskPresenceEnabled
        self.isDeskScheduleAlways = isDeskScheduleAlways
        self.deskScheduleStart = deskScheduleStart
        self.deskScheduleEnd = deskScheduleEnd
        self.isLaunchAtLogin = isLaunchAtLogin
        self.refillPercentage = refillPercentage
        self.refillInstalledDate = refillInstalledDate
        self.totalPuffsCount = totalPuffsCount
        self.lastActiveTimestamp = lastActiveTimestamp
    }

    private enum CodingKeys: String, CodingKey {
        case lastAddress, lastRoomId, lastFragranceId, customLocationName, defaultIntensity
        case isDeskPresenceEnabled, isDeskScheduleAlways, deskScheduleStart, deskScheduleEnd
        case isLaunchAtLogin, refillPercentage, refillInstalledDate, totalPuffsCount
        case lastActiveTimestamp
    }

    /// Decoded key by key, falling back to the default for anything absent.
    ///
    /// The synthesized initializer throws when a key is missing, which is true of
    /// every existing config the moment a new field is added. `load()` swallows that
    /// with `try?` and hands back a fresh config, silently discarding the paired
    /// device, refill level and every preference. Tolerating absent keys is what
    /// makes adding a field safe.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let fallback = AppConfig()

        func value<T: Decodable>(_ key: CodingKeys, _ fallbackValue: T) throws -> T {
            try container.decodeIfPresent(T.self, forKey: key) ?? fallbackValue
        }

        lastAddress = try container.decodeIfPresent(String.self, forKey: .lastAddress)
        customLocationName = try container.decodeIfPresent(String.self, forKey: .customLocationName)
        refillInstalledDate = try container.decodeIfPresent(String.self, forKey: .refillInstalledDate)
        lastActiveTimestamp = try container.decodeIfPresent(Double.self, forKey: .lastActiveTimestamp)

        lastRoomId = try value(.lastRoomId, fallback.lastRoomId)
        lastFragranceId = try value(.lastFragranceId, fallback.lastFragranceId)
        defaultIntensity = try value(.defaultIntensity, fallback.defaultIntensity)
        isDeskPresenceEnabled = try value(.isDeskPresenceEnabled, fallback.isDeskPresenceEnabled)
        isDeskScheduleAlways = try value(.isDeskScheduleAlways, fallback.isDeskScheduleAlways)
        deskScheduleStart = try value(.deskScheduleStart, fallback.deskScheduleStart)
        deskScheduleEnd = try value(.deskScheduleEnd, fallback.deskScheduleEnd)
        isLaunchAtLogin = try value(.isLaunchAtLogin, fallback.isLaunchAtLogin)
        refillPercentage = try value(.refillPercentage, fallback.refillPercentage)
        totalPuffsCount = try value(.totalPuffsCount, fallback.totalPuffsCount)
    }

    public static var configURL: URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent(".airspray_config.json")
    }

    /// The URL parameter exists so tests can round-trip against a scratch file
    /// instead of the real config in the user's home directory.
    public static func load(from url: URL = configURL) -> AppConfig {
        if let data = try? Data(contentsOf: url),
           var config = try? JSONDecoder().decode(AppConfig.self, from: data) {
            // Check if we need to migrate from python ~/.airspray/state.json
            if config.refillPercentage == 100.0 {
                let pyStateURL = FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent(".airspray")
                    .appendingPathComponent("state.json")
                if let pyData = try? Data(contentsOf: pyStateURL),
                   let json = try? JSONSerialization.jsonObject(with: pyData) as? [String: Any],
                   let percent = json["refill_percent"] as? Double {
                    config.refillPercentage = percent
                    config.refillInstalledDate = json["installed_date"] as? String ?? config.refillInstalledDate
                    config.save(to: url)
                }
            }
            return config
        }

        // Fresh config - check python state migration
        var fresh = AppConfig()
        let pyStateURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".airspray")
            .appendingPathComponent("state.json")
        if let pyData = try? Data(contentsOf: pyStateURL),
           let json = try? JSONSerialization.jsonObject(with: pyData) as? [String: Any],
           let percent = json["refill_percent"] as? Double {
            fresh.refillPercentage = percent
            fresh.refillInstalledDate = json["installed_date"] as? String
        }
        return fresh
    }

    /// Merge over whatever is already on disk.
    ///
    /// The Python CLI shares this file and owns keys this app does not model
    /// (activeDays, maxIdleSeconds, ...). A plain overwrite dropped them, so the
    /// two ports quietly reset each other's settings. See
    /// tests/test_swift_protocol_parity.py::test_config_keys_match_python.
    public func save(to url: URL = AppConfig.configURL) {
        guard let encoded = try? JSONEncoder().encode(self),
              var merged = (try? JSONSerialization.jsonObject(with: encoded)) as? [String: Any]
        else { return }

        if let existing = try? Data(contentsOf: url),
           let onDisk = (try? JSONSerialization.jsonObject(with: existing)) as? [String: Any] {
            merged = onDisk.merging(merged) { _, ours in ours }
        }

        if let out = try? JSONSerialization.data(
            withJSONObject: merged, options: [.prettyPrinted, .sortedKeys]
        ) {
            try? out.write(to: url)
        }
    }
}
