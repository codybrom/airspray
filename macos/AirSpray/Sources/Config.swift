import Foundation

/// Settings shared with the Python CLI through `~/.airspray_config.json`.
///
/// The contract both ports follow:
/// - Writes are atomic, so a reader never sees a half-written file.
/// - A missing file is a fresh config. A file that exists but cannot be decoded
///   is not: the read is retried, and if it still fails the file is moved aside
///   to `<name>.corrupt`. Defaults are never saved over an unreadable file.
/// - Every change is read-fresh, mutate, write (`AppConfig.update`). Nothing
///   keeps a config loaded earlier and writes it back later.
/// - A nil optional removes its key from the file.
public struct AppConfig: Codable, Equatable {
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

    /// Keys whose nil value must delete the key on disk. `JSONEncoder` simply
    /// omits a nil optional, and the save merges over the existing file, so
    /// without this list a cleared value would silently come back.
    private static let optionalKeys: [CodingKeys] = [
        .lastAddress, .customLocationName, .refillInstalledDate, .lastActiveTimestamp,
    ]

    /// Decoded key by key. An absent key, an explicit null and a value of the
    /// wrong type all fall back to the default for that one key, so adding a
    /// field -- or one odd value written by the other port -- never discards the
    /// paired device, refill level and every other preference.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let fallback = AppConfig()

        func optional<T: Decodable>(_ key: CodingKeys, _ type: T.Type) -> T? {
            (try? container.decodeIfPresent(T.self, forKey: key)) ?? nil
        }
        func value<T: Decodable>(_ key: CodingKeys, _ fallbackValue: T) -> T {
            optional(key, T.self) ?? fallbackValue
        }

        lastAddress = optional(.lastAddress, String.self)
        customLocationName = optional(.customLocationName, String.self)
        refillInstalledDate = optional(.refillInstalledDate, String.self)
        lastActiveTimestamp = optional(.lastActiveTimestamp, Double.self)

        lastRoomId = value(.lastRoomId, fallback.lastRoomId)
        lastFragranceId = value(.lastFragranceId, fallback.lastFragranceId)
        defaultIntensity = value(.defaultIntensity, fallback.defaultIntensity)
        isDeskPresenceEnabled = value(.isDeskPresenceEnabled, fallback.isDeskPresenceEnabled)
        isDeskScheduleAlways = value(.isDeskScheduleAlways, fallback.isDeskScheduleAlways)
        deskScheduleStart = value(.deskScheduleStart, fallback.deskScheduleStart)
        deskScheduleEnd = value(.deskScheduleEnd, fallback.deskScheduleEnd)
        isLaunchAtLogin = value(.isLaunchAtLogin, fallback.isLaunchAtLogin)
        refillPercentage = value(.refillPercentage, fallback.refillPercentage)
        totalPuffsCount = value(.totalPuffsCount, fallback.totalPuffsCount)
    }

    public static var configURL: URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return home.appendingPathComponent(".airspray_config.json")
    }

    /// The CLI's pre-shared-config refill store. Only consulted when there is no
    /// config file at all.
    public static var legacyStateURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".airspray")
            .appendingPathComponent("state.json")
    }

    /// Where an undecodable config is moved so it is neither lost nor overwritten.
    public static func corruptURL(for url: URL) -> URL {
        url.appendingPathExtension("corrupt")
    }

    /// How many times an undecodable file is re-read before it is declared
    /// corrupt, and the pause between attempts. The other port writes atomically
    /// too, so this only has to ride out an older build mid-write.
    static let readAttempts = 4
    static let readRetryPause: TimeInterval = 0.05

    private struct Snapshot {
        var config: AppConfig
        /// The file's full JSON object, including keys this port does not model.
        var raw: [String: Any]
        /// False when an unreadable file could not be moved aside. Saving then
        /// would put defaults over data we failed to read.
        var writable: Bool
    }

    private static func decode(_ data: Data) -> (AppConfig, [String: Any])? {
        guard let raw = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let config = try? JSONDecoder().decode(AppConfig.self, from: data)
        else { return nil }
        return (config, raw)
    }

    private static func fresh(legacyStateURL: URL) -> AppConfig {
        var config = AppConfig()
        if let data = try? Data(contentsOf: legacyStateURL),
           let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
           let percent = json["refill_percent"] as? Double {
            config.refillPercentage = percent
            config.refillInstalledDate = json["installed_date"] as? String
        }
        return config
    }

    private static func snapshot(of url: URL, legacyStateURL: URL) -> Snapshot {
        let fm = FileManager.default
        guard fm.fileExists(atPath: url.path) else {
            // No config at all: the one case where the legacy store is migrated.
            // It must never be keyed off the refill reading 100%, or a new bottle
            // snaps back to the old level on the next load.
            return Snapshot(config: fresh(legacyStateURL: legacyStateURL), raw: [:], writable: true)
        }

        for attempt in 0..<readAttempts {
            if attempt > 0 { Thread.sleep(forTimeInterval: readRetryPause) }
            guard fm.fileExists(atPath: url.path) else {
                return Snapshot(config: fresh(legacyStateURL: legacyStateURL), raw: [:], writable: true)
            }
            if let data = try? Data(contentsOf: url), let (config, raw) = decode(data) {
                return Snapshot(config: config, raw: raw, writable: true)
            }
        }

        // Still undecodable. Keep the bytes for the user and start clean. The
        // legacy store is not consulted, because this is not a first run.
        let aside = corruptURL(for: url)
        try? fm.removeItem(at: aside)
        let moved = (try? fm.moveItem(at: url, to: aside)) != nil
        return Snapshot(config: AppConfig(), raw: [:], writable: moved)
    }

    /// The URL parameters exist so tests can use scratch files instead of the
    /// real config and legacy store in the user's home directory.
    public static func load(from url: URL = configURL,
                            legacyStateURL: URL = AppConfig.legacyStateURL) -> AppConfig {
        snapshot(of: url, legacyStateURL: legacyStateURL).config
    }

    /// Read the file fresh, apply `mutate`, and write the result back atomically.
    ///
    /// This is the only way the app changes the config. Because the read happens
    /// immediately before the write, a change made by the CLI (or by another part
    /// of this app) in the meantime is kept rather than reverted. Nothing is
    /// written when `mutate` changes nothing.
    @discardableResult
    public static func update(at url: URL = configURL,
                              legacyStateURL: URL = AppConfig.legacyStateURL,
                              _ mutate: (inout AppConfig) -> Void) -> AppConfig {
        let snap = snapshot(of: url, legacyStateURL: legacyStateURL)
        var config = snap.config
        mutate(&config)
        let fileExists = FileManager.default.fileExists(atPath: url.path)
        guard snap.writable, config != snap.config || !fileExists else { return config }
        config.write(to: url, over: snap.raw)
        return config
    }

    /// Write this whole value over the file, keeping keys this port does not
    /// model. Prefer `update`: a value loaded earlier and saved later reverts
    /// whatever changed in between.
    public func save(to url: URL = AppConfig.configURL) {
        let snap = AppConfig.snapshot(of: url, legacyStateURL: URL(fileURLWithPath: "/dev/null/none"))
        guard snap.writable else { return }
        write(to: url, over: snap.raw)
    }

    /// Merge over the file's existing keys.
    ///
    /// The Python CLI shares this file and owns keys this app does not model
    /// (activeDays, maxIdleSeconds, ...). A plain overwrite dropped them, so the
    /// two ports quietly reset each other's settings. See
    /// tests/test_swift_protocol_parity.py::test_config_keys_match_python.
    private func write(to url: URL, over existing: [String: Any]) {
        guard let encoded = try? JSONEncoder().encode(self),
              let ours = (try? JSONSerialization.jsonObject(with: encoded)) as? [String: Any]
        else { return }

        var merged = existing.merging(ours) { _, new in new }
        for key in AppConfig.optionalKeys where ours[key.rawValue] == nil {
            merged.removeValue(forKey: key.rawValue)
        }

        if let out = try? JSONSerialization.data(
            withJSONObject: merged, options: [.prettyPrinted, .sortedKeys]
        ) {
            try? out.write(to: url, options: .atomic)
        }
    }
}
